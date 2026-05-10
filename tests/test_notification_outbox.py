from hermes_managed_network.approval_notifications import build_telegram_approval_card
from hermes_managed_network.storage import SQLiteStore


def test_store_records_telegram_approval_notification_outbox(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="high",
        requested_by="hmn",
        details={"node_id": "node_outbox", "command": "API_KEY=secret reboot"},
    )
    card = build_telegram_approval_card(approval)

    notification = store.enqueue_notification(
        channel="telegram",
        subject_type="approval",
        subject_id=approval.approval_id,
        payload={"text": card.text, "buttons": card.buttons},
    )

    assert notification.notification_id.startswith("notif_")
    assert notification.status == "pending"
    pending = SQLiteStore(db).list_notifications(status="pending")
    assert len(pending) == 1
    assert pending[0].channel == "telegram"
    assert pending[0].subject_id == approval.approval_id
    assert "API_KEY=secret" not in pending[0].payload["text"]
    assert pending[0].payload["buttons"][0]["callback_data"] == f"hmn:approval:approve:{approval.approval_id}"
