from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ApprovalCard:
    text: str
    buttons: list[dict[str, str]]
    channel: str = "approval"


@dataclass(frozen=True)
class ApprovalGatewayClientConfig:
    client: str
    target: str


class ApprovalGatewayApiClient(Protocol):
    def list_notifications(self) -> list[dict[str, Any]]: ...

    def mark_delivered(self, notification_id: str) -> None: ...

    def handle_callback(self, callback_data: str, *, decided_by: str) -> dict[str, Any]: ...


class ApprovalGatewayClient(Protocol):
    def send_card(
        self,
        *,
        config: ApprovalGatewayClientConfig,
        text: str,
        buttons: list[dict[str, str]],
    ) -> None: ...


@dataclass(frozen=True)
class PollResult:
    sent: int = 0
    failed: int = 0


@dataclass
class InMemoryApprovalGatewayApiClient:
    notifications: list[dict[str, Any]] = field(default_factory=list)
    delivered_ids: list[str] = field(default_factory=list)
    callbacks: list[dict[str, str]] = field(default_factory=list)

    def list_notifications(self) -> list[dict[str, Any]]:
        return list(self.notifications)

    def mark_delivered(self, notification_id: str) -> None:
        self.delivered_ids.append(notification_id)

    def handle_callback(self, callback_data: str, *, decided_by: str) -> dict[str, Any]:
        payload = {"callback_data": callback_data, "decided_by": decided_by}
        self.callbacks.append(payload)
        return {"ok": True, "message": "ok"}


@dataclass
class RecordingApprovalGatewayClient:
    fail: bool = False
    sent_messages: list[dict[str, Any]] = field(default_factory=list)

    def send_card(
        self,
        *,
        config: ApprovalGatewayClientConfig,
        text: str,
        buttons: list[dict[str, str]],
    ) -> None:
        if self.fail:
            raise RuntimeError(f"{config.client} send failed")
        self.sent_messages.append(
            {
                "client": config.client,
                "target": config.target,
                "text": text,
                "buttons": buttons,
            }
        )


class ApprovalGatewayHttpApiClient:
    def __init__(self, base_url: str, *, client: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client

    @property
    def notifications_path(self) -> str:
        return "/api/v1/gateway/approval/notifications?" + urllib.parse.urlencode({"client": self.client})

    def delivered_path(self, notification_id: str) -> str:
        escaped = urllib.parse.quote(notification_id, safe="")
        return f"/api/v1/gateway/approval/notifications/{escaped}/delivered"

    @property
    def callback_path(self) -> str:
        return "/api/v1/gateway/approval/callback"

    def callback_payload(self, callback_data: str, *, decided_by: str) -> dict[str, Any]:
        return {"client": self.client, "callback_data": callback_data, "decided_by": decided_by}

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {}

    def list_notifications(self) -> list[dict[str, Any]]:
        body = self._request("GET", self.notifications_path)
        return list(body.get("notifications", []))

    def mark_delivered(self, notification_id: str) -> None:
        self._request("POST", self.delivered_path(notification_id))

    def handle_callback(self, callback_data: str, *, decided_by: str) -> dict[str, Any]:
        return self._request("POST", self.callback_path, self.callback_payload(callback_data, decided_by=decided_by))


class TelegramApprovalGatewayClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send_card(
        self,
        *,
        config: ApprovalGatewayClientConfig,
        text: str,
        buttons: list[dict[str, str]],
    ) -> None:
        keyboard = [[{"text": button["text"], "callback_data": button["callback_data"]}] for button in buttons]
        payload = {
            "chat_id": config.target,
            "text": text,
            "reply_markup": {"inline_keyboard": keyboard},
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"telegram send failed: HTTP {exc.code}") from exc


class TelegramBotApiClient(TelegramApprovalGatewayClient):
    def send_message(self, *, chat_id: str, text: str, buttons: list[dict[str, str]]) -> None:
        self.send_card(config=ApprovalGatewayClientConfig(client="telegram", target=chat_id), text=text, buttons=buttons)


@dataclass
class RecordingTelegramClient:
    fail: bool = False
    sent_messages: list[dict[str, Any]] = field(default_factory=list)

    def send_message(self, *, chat_id: str, text: str, buttons: list[dict[str, str]]) -> None:
        if self.fail:
            raise RuntimeError("telegram send failed")
        self.sent_messages.append({"chat_id": chat_id, "text": text, "buttons": buttons})


def poll_once(
    api: ApprovalGatewayApiClient,
    gateway_client: ApprovalGatewayClient,
    config: ApprovalGatewayClientConfig,
) -> PollResult:
    sent = 0
    failed = 0
    for notification in api.list_notifications():
        payload = notification.get("payload") or {}
        try:
            gateway_client.send_card(
                config=config,
                text=str(payload.get("text", "")),
                buttons=list(payload.get("buttons") or []),
            )
            api.mark_delivered(str(notification["notification_id"]))
            sent += 1
        except Exception:
            failed += 1
    return PollResult(sent=sent, failed=failed)
