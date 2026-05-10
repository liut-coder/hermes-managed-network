from types import SimpleNamespace

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def test_task_run_rejects_wait_without_ssh_executor(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(_managed_node())

    result = runner.invoke(app, ["task", "run", "uptime", "--wait", "--db", str(db)])

    assert result.exit_code == 1
    assert "只支持 --executor ssh" in result.stdout




def _managed_node(node_id: str = "node_cli_ssh") -> Node:
    return Node(
        node_id=node_id,
        fingerprint="sha256:" + node_id,
        hostname=node_id + ".example",
        addresses=["100.64.0.20"],
        trust_level="B",
        labels=["ssh-user=deployer", "ssh-port=2202"],
        status="managed",
        permission_bundles=["observe"],
    )


def test_task_run_with_ssh_wait_executes_immediately(tmp_path, monkeypatch):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(_managed_node())

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("hermes_managed_network.executor.subprocess.run", fake_run)

    result = runner.invoke(app, ["task", "run", "uptime", "--executor", "ssh", "--wait", "--db", str(db)])

    assert result.exit_code == 0
    assert "已通过 SSH 执行" in result.stdout
    task = SQLiteStore(db).list_tasks()[0]
    assert task.executor == "ssh"
    assert task.status == "succeeded"


def test_task_ssh_run_next_returns_non_zero_when_no_pending_ssh_task(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_node(_managed_node())

    result = runner.invoke(app, ["task", "ssh-run-next", "--db", str(db)])

    assert result.exit_code == 1
    assert "没有待执行的 SSH 任务" in result.stdout


def test_task_ssh_run_next_executes_approved_pending_ssh_task(tmp_path, monkeypatch):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node())
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="high",
        requested_by="hmn",
        details={"node_id": "node_cli_ssh", "command": "systemctl restart app", "executor": "ssh", "created_by": "hmn"},
    )
    store.resolve_approval_request(approval.approval_id, status="approved", decided_by="Misk")

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr("hermes_managed_network.executor.subprocess.run", fake_run)

    result = runner.invoke(app, ["task", "ssh-run-next", "--db", str(db)])

    assert result.exit_code == 0
    assert "已执行任务" in result.stdout
    task = SQLiteStore(db).list_tasks()[0]
    assert task.executor == "ssh"
    assert task.status == "succeeded"
