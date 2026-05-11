from __future__ import annotations

from typing import Any, Protocol

from .approval_gateway import (
    ApprovalGatewayClientConfig,
    ApprovalGatewayHttpApiClient,
    InMemoryApprovalGatewayApiClient,
    PollResult,
    RecordingTelegramClient,
    TelegramBotApiClient,
    poll_once as approval_gateway_poll_once,
)


class GatewayApiClient(Protocol):
    def list_notifications(self) -> list[dict[str, Any]]: ...

    def mark_delivered(self, notification_id: str) -> None: ...

    def handle_callback(self, callback_data: str, *, decided_by: str) -> dict[str, Any]: ...


class TelegramClient(Protocol):
    def send_message(self, *, chat_id: str, text: str, buttons: list[dict[str, str]]) -> None: ...


InMemoryGatewayApiClient = InMemoryApprovalGatewayApiClient


class HttpGatewayApiClient(ApprovalGatewayHttpApiClient):
    def __init__(self, base_url: str) -> None:
        super().__init__(base_url, client="telegram")

    @property
    def notifications_path(self) -> str:
        return "/api/v1/gateway/telegram/notifications"

    def delivered_path(self, notification_id: str) -> str:
        return f"/api/v1/gateway/telegram/notifications/{notification_id}/delivered"

    @property
    def callback_path(self) -> str:
        return "/api/v1/gateway/telegram/callback"

    def callback_payload(self, callback_data: str, *, decided_by: str) -> dict[str, Any]:
        return {"callback_data": callback_data, "decided_by": decided_by}


def poll_once(api: GatewayApiClient, telegram: TelegramClient, *, chat_id: str) -> PollResult:
    class _TelegramAdapter:
        def __init__(self, client: TelegramClient) -> None:
            self.client = client

        def send_card(
            self,
            *,
            config: ApprovalGatewayClientConfig,
            text: str,
            buttons: list[dict[str, str]],
        ) -> None:
            self.client.send_message(chat_id=config.target, text=text, buttons=buttons)

    return approval_gateway_poll_once(api, _TelegramAdapter(telegram), ApprovalGatewayClientConfig(client="telegram", target=chat_id))
