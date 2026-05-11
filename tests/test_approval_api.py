from fastapi.testclient import TestClient

from hermes_managed_network.api import create_app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def _managed_node(node_id: str) -> Node:
    return Node(
        node_id=node_id,
        fingerprint="sha256:" + node_id,
        hostname=node_id + ".example",
        addresses=[],
        trust_level="B",
        labels=[],
        status="managed",
        permission_bundles=["observe"],
    )


def test_approval_api_approves_and_dispatches_task_for_telegram_bridge(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node("node_api_approve"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="high",
        requested_by="telegram",
        details={"node_id": "node_api_approve", "command": "reboot", "created_by": "telegram"},
    )
    client = TestClient(create_app(db))

    response = client.post(f"/api/v1/approvals/{approval.approval_id}/approve", json={"decided_by": "Misk"})

    assert response.status_code == 200
    body = response.json()
    assert body["approval_id"] == approval.approval_id
    assert body["status"] == "approved"
    assert body["dispatched_task_id"].startswith("task_")
    tasks = SQLiteStore(db).list_tasks()
    assert len(tasks) == 1
    assert tasks[0].task_id == body["dispatched_task_id"]
    assert tasks[0].node_id == "node_api_approve"
    assert tasks[0].command == "reboot"
    assert tasks[0].risk == "high"


def test_approval_api_rejects_without_dispatching_task_for_telegram_bridge(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node("node_api_reject"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="telegram",
        details={"node_id": "node_api_reject", "command": "shutdown now", "created_by": "telegram"},
    )
    client = TestClient(create_app(db))

    response = client.post(f"/api/v1/approvals/{approval.approval_id}/reject", json={"decided_by": "Misk"})
    later_approve = client.post(f"/api/v1/approvals/{approval.approval_id}/approve", json={"decided_by": "Other"})

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert later_approve.status_code == 409
    assert SQLiteStore(db).load_approval_request(approval.approval_id).status == "rejected"
    assert SQLiteStore(db).list_tasks() == []
