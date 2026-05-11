from fastapi.testclient import TestClient

from hermes_managed_network.api import create_app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore
from hermes_managed_network.version import current_version_info


def _managed_node(node_id: str, *, status: str = "managed") -> Node:
    return Node(
        node_id=node_id,
        fingerprint="sha256:" + node_id,
        hostname=node_id + ".example",
        addresses=[],
        trust_level="B",
        labels=[],
        status=status,
        permission_bundles=["observe"],
    )


def test_rejected_node_cannot_poll_or_submit_results(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node("node_revoked_runtime", status="revoked"))
    task = store.create_task(node_id="node_revoked_runtime", command="uptime", risk="low", created_by="test")
    client = TestClient(create_app(db))

    next_response = client.post(
        "/api/v1/nodes/node_revoked_runtime/tasks/next",
        json={
            "fingerprint": "sha256:node_revoked_runtime",
            "worker_protocol_version": current_version_info().worker_protocol_version,
        },
    )
    result_response = client.post(
        f"/api/v1/tasks/{task.task_id}/result",
        json={"fingerprint": "sha256:node_revoked_runtime", "exit_code": 0, "stdout": "ok", "stderr": ""},
    )

    assert next_response.status_code == 403
    assert next_response.json()["detail"] == "node is revoked"
    assert result_response.status_code == 403
    assert result_response.json()["detail"] == "node is revoked"
    assert SQLiteStore(db).load_task(task.task_id).status == "pending"


def test_non_managed_node_heartbeat_is_rejected(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node("node_pending_hb", status="pending"))
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_pending_hb/heartbeat",
        json={"fingerprint": "sha256:node_pending_hb", "status": "ok", "facts": {}},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "node is not managed"


def test_task_dispatch_requires_managed_node_even_after_approval(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node("node_pending_dispatch", status="pending"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="high",
        requested_by="hmn",
        details={"node_id": "node_pending_dispatch", "command": "reboot", "created_by": "hmn"},
    )
    client = TestClient(create_app(db))

    response = client.post(f"/api/v1/approvals/{approval.approval_id}/approve", json={"decided_by": "Misk"})

    assert response.status_code == 422
    assert response.json()["detail"] == "approval cannot be dispatched"
    assert SQLiteStore(db).list_tasks() == []
    dispatch_event = SQLiteStore(db).list_audit_events()[-1]
    assert dispatch_event.action == "approval/dispatch"
    assert dispatch_event.outcome == "failed"
    assert dispatch_event.details["reason"] == "node not managed"


def test_telegram_callback_rejects_pending_node_dispatch(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_managed_node("node_pending_callback", status="pending"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"node_id": "node_pending_callback", "command": "shutdown now", "created_by": "telegram"},
    )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/gateway/telegram/callback",
        json={"callback_data": f"hmn:approval:approve:{approval.approval_id}", "decided_by": "Misk"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["status"] == "approved"
    assert "任务创建失败" in detail["message"]
    assert SQLiteStore(db).list_tasks() == []
