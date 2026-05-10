from fastapi.testclient import TestClient

from hermes_managed_network.api import create_app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def _node(node_id: str) -> Node:
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


def test_api_lists_pending_telegram_notifications_for_gateway(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    notification = store.enqueue_notification(
        channel="telegram",
        subject_type="approval",
        subject_id="appr_gateway",
        payload={"text": "审批", "buttons": [{"text": "批准", "callback_data": "hmn:approval:approve:appr_gateway"}]},
    )
    client = TestClient(create_app(db))

    response = client.get("/api/v1/gateway/telegram/notifications")

    assert response.status_code == 200
    body = response.json()
    assert body["notifications"][0]["notification_id"] == notification.notification_id
    assert body["notifications"][0]["payload"]["buttons"][0]["callback_data"] == "hmn:approval:approve:appr_gateway"


def test_api_marks_telegram_notification_delivered(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    notification = store.enqueue_notification(
        channel="telegram",
        subject_type="approval",
        subject_id="appr_delivered",
        payload={"text": "审批", "buttons": []},
    )
    client = TestClient(create_app(db))

    response = client.post(f"/api/v1/gateway/telegram/notifications/{notification.notification_id}/delivered")

    assert response.status_code == 200
    assert response.json()["status"] == "delivered"
    assert SQLiteStore(db).list_notifications(status="pending") == []
    assert SQLiteStore(db).list_notifications(status="delivered")[0].notification_id == notification.notification_id


def test_api_handles_telegram_approval_callback_and_dispatches(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_node("node_gateway_approve"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"node_id": "node_gateway_approve", "command": "reboot", "created_by": "telegram"},
    )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/gateway/telegram/callback",
        json={"callback_data": f"hmn:approval:approve:{approval.approval_id}", "decided_by": "Misk"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "approved"
    assert body["dispatched_task_id"].startswith("task_")
    assert SQLiteStore(db).list_tasks()[0].task_id == body["dispatched_task_id"]


def test_api_rejects_duplicate_or_invalid_telegram_callback(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_node("node_gateway_reject"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="high",
        requested_by="hmn",
        details={"node_id": "node_gateway_reject", "command": "shutdown now", "created_by": "telegram"},
    )
    client = TestClient(create_app(db))

    rejected = client.post(
        "/api/v1/gateway/telegram/callback",
        json={"callback_data": f"hmn:approval:reject:{approval.approval_id}", "decided_by": "Misk"},
    )
    duplicate = client.post(
        "/api/v1/gateway/telegram/callback",
        json={"callback_data": f"hmn:approval:approve:{approval.approval_id}", "decided_by": "Other"},
    )
    invalid = client.post("/api/v1/gateway/telegram/callback", json={"callback_data": "bad", "decided_by": "Misk"})

    assert rejected.status_code == 200
    assert duplicate.status_code == 409
    assert invalid.status_code == 400
    assert SQLiteStore(db).list_tasks() == []
