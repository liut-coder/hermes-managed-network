from hermes_managed_network.approval_telegram_flow import (
    build_pending_approval_notification,
    handle_telegram_approval_callback,
)
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


def test_pending_high_risk_task_builds_telegram_notification(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_node("node_notify"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="high",
        requested_by="hmn",
        details={"node_id": "node_notify", "command": "TOKEN=secret reboot", "created_by": "hmn"},
    )

    notification = build_pending_approval_notification(store, approval.approval_id)

    assert notification.approval_id == approval.approval_id
    assert notification.status == "pending"
    assert "高风险审批" in notification.card.text
    assert "TOKEN=secret" not in notification.card.text
    assert notification.delivery_hint == "telegram"


def test_telegram_approval_callback_approves_and_dispatches(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_node("node_callback"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"node_id": "node_callback", "command": "reboot", "created_by": "telegram"},
    )

    result = handle_telegram_approval_callback(
        store,
        f"hmn:approval:approve:{approval.approval_id}",
        decided_by="Misk",
    )

    assert result.ok is True
    assert result.status == "approved"
    assert result.dispatched_task_id.startswith("task_")
    assert "已批准" in result.message
    assert SQLiteStore(db).list_tasks()[0].task_id == result.dispatched_task_id


def test_telegram_approval_callback_rejects_without_dispatch(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(_node("node_reject_callback"))
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"node_id": "node_reject_callback", "command": "shutdown now", "created_by": "telegram"},
    )

    result = handle_telegram_approval_callback(
        store,
        f"hmn:approval:reject:{approval.approval_id}",
        decided_by="Misk",
    )
    later = handle_telegram_approval_callback(
        store,
        f"hmn:approval:approve:{approval.approval_id}",
        decided_by="Other",
    )

    assert result.ok is True
    assert result.status == "rejected"
    assert "已拒绝" in result.message
    assert later.ok is False
    assert SQLiteStore(db).list_tasks() == []


def test_telegram_approval_callback_rejects_invalid_payload(tmp_path):
    store = SQLiteStore(tmp_path / "hmn.db")

    result = handle_telegram_approval_callback(store, "bad", decided_by="Misk")

    assert result.ok is False
    assert "无效" in result.message
