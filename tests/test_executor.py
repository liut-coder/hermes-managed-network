from pathlib import Path

import pytest

from hermes_managed_network.executor import PlaybookExecutionError, PlaybookExecutor
from hermes_managed_network.playbook import Playbook


def write_playbook(tmp_path: Path, *, risk: str = "low") -> Path:
    path = tmp_path / "playbook.yml"
    content = """
id: demo.echo
name: Demo Echo
risk: __RISK__
permissions: [observe]
inputs:
  message:
    required: true
precheck:
  - "test -n '{{ message }}'"
backup: []
action:
  - "printf '{{ message }}'"
verify:
  - "printf ok"
rollback_hint: []
""".strip().replace("__RISK__", risk)
    path.write_text(content)
    return path


def test_executor_renders_dry_run_without_running_commands(tmp_path):
    playbook = Playbook.load(write_playbook(tmp_path))
    run = PlaybookExecutor(dry_run=True).run(playbook, values={"message": "hello"})

    assert run.ok
    assert [result.phase for result in run.results] == ["precheck", "action", "verify"]
    assert run.results[1].command == "printf 'hello'"


def test_executor_can_run_local_commands(tmp_path):
    playbook = Playbook.load(write_playbook(tmp_path))
    run = PlaybookExecutor(dry_run=False).run(playbook, values={"message": "hello"}, phases=["action"])

    assert run.ok
    assert run.results[0].stdout == "hello"


def test_executor_rejects_high_risk_without_explicit_allowance(tmp_path):
    playbook = Playbook.load(write_playbook(tmp_path, risk="high"))

    with pytest.raises(PermissionError):
        PlaybookExecutor().run(playbook, values={"message": "hello"})


def test_executor_stops_on_failed_command(tmp_path):
    path = tmp_path / "playbook.yml"
    path.write_text(
        """
id: demo.fail
name: Demo Fail
risk: low
permissions: [observe]
inputs: {}
precheck:
  - "exit 7"
backup: []
action: []
verify: []
rollback_hint: []
""".strip()
    )
    playbook = Playbook.load(path)

    with pytest.raises(PlaybookExecutionError) as exc:
        PlaybookExecutor(dry_run=False).run(playbook, values={})

    assert exc.value.result.exit_code == 7
