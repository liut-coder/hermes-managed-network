from typer.testing import CliRunner

from hermes_managed_network.approval_gateway import (
    ApprovalCard,
    ApprovalGatewayClientConfig,
    ApprovalGatewayHttpApiClient,
    InMemoryApprovalGatewayApiClient,
    RecordingApprovalGatewayClient,
    poll_once,
)
from hermes_managed_network.approval_notifications import (
    build_approval_card,
    build_telegram_approval_card,
    parse_approval_callback,
)
from hermes_managed_network.cli import app
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


def test_build_approval_card_is_client_neutral_and_telegram_wrapper_stays_compatible():
    card = build_approval_card(_approval(node_id="node_prod", command="TOKEN=secret reboot"))

    assert isinstance(card, ApprovalCard)
    assert card.channel == "approval"
    assert "高风险审批" in card.text
    assert "TOKEN=secret" not in card.text
    assert card.buttons[0]["callback_data"] == "hmn:approval:approve:appr_demo123"
    assert build_telegram_approval_card(_approval()).text == build_approval_card(_approval()).text


def test_parse_approval_callback_is_client_neutral():
    assert parse_approval_callback("hmn:approval:reject:appr_demo123") == {
        "decision": "reject",
        "approval_id": "appr_demo123",
    }
    assert parse_approval_callback("hmn:approval:delete:appr_demo123") is None


def test_generic_poll_once_delivers_via_named_client_and_marks_delivered():
    api = InMemoryApprovalGatewayApiClient(
        notifications=[
            {
                "notification_id": "notif_1",
                "payload": {
                    "text": "审批",
                    "buttons": [{"text": "批准", "callback_data": "hmn:approval:approve:appr_1"}],
                },
            }
        ]
    )
    client = RecordingApprovalGatewayClient()

    result = poll_once(api, client, ApprovalGatewayClientConfig(client="telegram", target="7500615916"))

    assert result.sent == 1
    assert result.failed == 0
    assert api.delivered_ids == ["notif_1"]
    assert client.sent_messages == [
        {
            "client": "telegram",
            "target": "7500615916",
            "text": "审批",
            "buttons": [{"text": "批准", "callback_data": "hmn:approval:approve:appr_1"}],
        }
    ]


def test_http_gateway_client_uses_generic_approval_paths():
    api = ApprovalGatewayHttpApiClient("http://hmn.local", client="telegram")

    assert api.notifications_path == "/api/v1/gateway/approval/notifications?client=telegram"
    assert api.delivered_path("notif_1") == "/api/v1/gateway/approval/notifications/notif_1/delivered"
    assert api.callback_path == "/api/v1/gateway/approval/callback"
    assert api.callback_payload("hmn:approval:approve:appr_1", decided_by="Misk") == {
        "client": "telegram",
        "callback_data": "hmn:approval:approve:appr_1",
        "decided_by": "Misk",
    }


def test_approval_gateway_cli_exists_and_keeps_telegram_alias():
    runner = CliRunner()

    generic = runner.invoke(app, ["approval-gateway", "--help"])
    legacy = runner.invoke(app, ["telegram-gateway", "--help"])

    assert generic.exit_code == 0
    poll_help = runner.invoke(app, ["approval-gateway", "poll-once", "--help"])

    assert poll_help.exit_code == 0
    assert "--client" in poll_help.stdout
    assert "poll-once" in generic.stdout
    assert legacy.exit_code == 0
    assert "poll-once" in legacy.stdout


def test_approval_gateway_poll_once_requires_telegram_token_for_telegram_client():
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "approval-gateway",
            "poll-once",
            "--client",
            "telegram",
            "--api-url",
            "http://127.0.0.1:8765",
            "--target",
            "7500615916",
        ],
        env={"HMN_TELEGRAM_BOT_TOKEN": ""},
    )

    assert result.exit_code != 0
    assert "HMN_TELEGRAM_BOT_TOKEN" in result.stdout
