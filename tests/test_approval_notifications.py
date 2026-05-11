from hermes_managed_network.approval_notifications import (
    build_telegram_approval_card,
    parse_telegram_approval_callback,
)
from hermes_managed_network.storage import ApprovalRequest


def _approval(**details):
    return ApprovalRequest(
        approval_id="appr_demo123",
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        status="pending",
        requested_by="hmn",
        details=details,
        created_at=None,
    )


def test_build_telegram_approval_card_redacts_command_and_has_actions():
    card = build_telegram_approval_card(
        _approval(node_id="node_prod", command="TOKEN=secret reboot", created_by="hmn")
    )

    assert "高风险审批" in card.text
    assert "critical" in card.text
    assert "node_prod" in card.text
    assert "appr_demo123" in card.text
    assert "TOKEN=secret" not in card.text
    assert "命令: `TOKEN… reboot`" in card.text
    assert card.buttons == [
        {"text": "✅ 批准", "callback_data": "hmn:approval:approve:appr_demo123"},
        {"text": "❌ 拒绝", "callback_data": "hmn:approval:reject:appr_demo123"},
    ]


def test_parse_telegram_approval_callback():
    parsed = parse_telegram_approval_callback("hmn:approval:approve:appr_demo123")

    assert parsed == {"decision": "approve", "approval_id": "appr_demo123"}
    assert parse_telegram_approval_callback("hmn:approval:delete:appr_demo123") is None
    assert parse_telegram_approval_callback("other") is None
