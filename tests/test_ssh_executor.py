from types import SimpleNamespace

import pytest

from hermes_managed_network.executor import SSHExecutionError, run_ssh_task
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def test_run_ssh_task_prefers_explicit_node_ssh_fields(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_ssh_fields",
            fingerprint="sha256:fields",
            hostname="fields.example",
            addresses=["100.64.0.99"],
            trust_level="B",
            labels=["ssh-user=ops", "ssh-port=2222"],
            status="managed",
            permission_bundles=["observe"],
            ssh_host="10.10.10.10",
            ssh_user="deploy",
            ssh_port=2201,
        )
    )
    task = store.create_task(node_id="node_ssh_fields", command="uptime", risk="low", created_by="test", executor="ssh")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("hermes_managed_network.executor.subprocess.run", fake_run)

    completed = run_ssh_task(store, task.task_id)

    assert completed.status == "succeeded"
    assert calls == [["ssh", "-p", "2201", "deploy@10.10.10.10", "uptime"]]




def _managed_node(node_id: str = "node_ssh") -> Node:
    return Node(
        node_id=node_id,
        fingerprint="sha256:" + node_id,
        hostname=node_id + ".example",
        addresses=["100.64.0.10"],
        trust_level="B",
        labels=["ssh-user=ops", "ssh-port=2222"],
        status="managed",
        permission_bundles=["observe"],
    )


def test_run_ssh_task_executes_pending_ssh_task_and_records_result(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node())
    task = store.create_task(node_id="node_ssh", command="uptime", risk="low", created_by="test", executor="ssh")

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="up 1 day\n", stderr="")

    monkeypatch.setattr("hermes_managed_network.executor.subprocess.run", fake_run)

    completed = run_ssh_task(store, task.task_id)

    assert completed.status == "succeeded"
    assert completed.exit_code == 0
    assert completed.stdout == "up 1 day\n"
    assert calls == [["ssh", "-p", "2222", "ops@100.64.0.10", "uptime"]]
    actions = [event.action for event in store.list_audit_events()]
    assert "ssh_execute" in actions
    assert actions[-1] == "task_result"


def test_run_ssh_task_rejects_high_risk_without_explicit_allowance(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node())
    task = store.create_task(node_id="node_ssh", command="reboot", risk="high", created_by="test", executor="ssh")

    with pytest.raises(PermissionError):
        run_ssh_task(store, task.task_id)

    loaded = store.load_task(task.task_id)
    assert loaded.status == "pending"


def test_run_ssh_task_requires_ssh_executor_tasks(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node())
    task = store.create_task(node_id="node_ssh", command="uptime", risk="low", created_by="test", executor="worker")

    with pytest.raises(ValueError):
        run_ssh_task(store, task.task_id)


def test_run_ssh_task_requires_ssh_host(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_no_ssh_host",
            fingerprint="sha256:no-host",
            hostname="no-host.example",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    task = store.create_task(node_id="node_no_ssh_host", command="uptime", risk="low", created_by="test", executor="ssh")

    with pytest.raises(ValueError):
        run_ssh_task(store, task.task_id)

    loaded = store.load_task(task.task_id)
    assert loaded.status == "pending"


def test_run_ssh_task_surfaces_non_zero_exit_and_marks_failed(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node())
    task = store.create_task(node_id="node_ssh", command="false", risk="medium", created_by="test", executor="ssh")

    def fake_run(command, **kwargs):
        return SimpleNamespace(returncode=7, stdout="", stderr="boom\n")

    monkeypatch.setattr("hermes_managed_network.executor.subprocess.run", fake_run)

    with pytest.raises(SSHExecutionError) as exc:
        run_ssh_task(store, task.task_id)

    assert exc.value.task_id == task.task_id
    loaded = store.load_task(task.task_id)
    assert loaded.status == "failed"
    assert loaded.exit_code == 7
    assert loaded.stderr == "boom\n"
