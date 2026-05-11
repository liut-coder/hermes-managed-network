from hermes_managed_network.telegram_gateway import (
    InMemoryGatewayApiClient,
    RecordingTelegramClient,
    poll_once,
)


def test_poll_once_sends_pending_notification_and_marks_delivered():
    api = InMemoryGatewayApiClient(
        notifications=[
            {
                "notification_id": "notif_1",
                "payload": {
                    "text": "高风险审批",
                    "buttons": [
                        {"text": "批准", "callback_data": "hmn:approval:approve:appr_1"},
                        {"text": "拒绝", "callback_data": "hmn:approval:reject:appr_1"},
                    ],
                },
            }
        ]
    )
    telegram = RecordingTelegramClient()

    result = poll_once(api, telegram, chat_id="7500615916")

    assert result.sent == 1
    assert telegram.sent_messages == [
        {
            "chat_id": "7500615916",
            "text": "高风险审批",
            "buttons": [
                {"text": "批准", "callback_data": "hmn:approval:approve:appr_1"},
                {"text": "拒绝", "callback_data": "hmn:approval:reject:appr_1"},
            ],
        }
    ]
    assert api.delivered_ids == ["notif_1"]


def test_poll_once_does_not_mark_delivered_when_send_fails():
    api = InMemoryGatewayApiClient(
        notifications=[{"notification_id": "notif_1", "payload": {"text": "审批", "buttons": []}}]
    )
    telegram = RecordingTelegramClient(fail=True)

    result = poll_once(api, telegram, chat_id="7500615916")

    assert result.sent == 0
    assert result.failed == 1
    assert api.delivered_ids == []


def test_telegram_client_posts_callback_to_gateway_api():
    api = InMemoryGatewayApiClient()

    result = api.handle_callback("hmn:approval:approve:appr_1", decided_by="Misk")

    assert result["ok"] is True
    assert api.callbacks == [{"callback_data": "hmn:approval:approve:appr_1", "decided_by": "Misk"}]
