from pathlib import Path

from hermes_managed_network.playbook import Playbook


def test_playbook_loads_required_fields_from_yaml():
    playbook = Playbook.load(Path("playbooks/restart-container.yaml"))

    assert playbook.id == "restart-container"
    assert playbook.risk == "low"
    assert playbook.permissions == ["restart.container"]
    assert "container" in playbook.inputs
    assert playbook.action == ["docker restart {{ container }}"]


def test_playbook_renders_commands_with_inputs():
    playbook = Playbook.load(Path("playbooks/restart-container.yaml"))

    rendered = playbook.render_phase("action", {"container": "halo"})

    assert rendered == ["docker restart halo"]


def test_missing_required_input_is_rejected():
    playbook = Playbook.load(Path("playbooks/restart-container.yaml"))

    try:
        playbook.render_phase("action", {})
    except ValueError as exc:
        assert "container" in str(exc)
    else:
        raise AssertionError("expected ValueError")
