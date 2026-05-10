from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


class GatewayApiClient(Protocol):
    def list_notifications(self) -> list[dict[str, Any]]: ...

    def mark_delivered(self, notification_id: str) -> None: ...

    def handle_callback(self, callback_data: str, *, decided_by: str) -> dict[str, Any]: ...


class TelegramClient(Protocol):
    def send_message(self, *, chat_id: str, text: str, buttons: list[dict[str, str]]) -> None: ...


@dataclass(frozen=True)
class PollResult:
    sent: int = 0
    failed: int = 0


@dataclass
class InMemoryGatewayApiClient:
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
class RecordingTelegramClient:
    fail: bool = False
    sent_messages: list[dict[str, Any]] = field(default_factory=list)

    def send_message(self, *, chat_id: str, text: str, buttons: list[dict[str, str]]) -> None:
        if self.fail:
            raise RuntimeError("telegram send failed")
        self.sent_messages.append({"chat_id": chat_id, "text": text, "buttons": buttons})


class HttpGatewayApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

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
        body = self._request("GET", "/api/v1/gateway/telegram/notifications")
        return list(body.get("notifications", []))

    def mark_delivered(self, notification_id: str) -> None:
        self._request("POST", f"/api/v1/gateway/telegram/notifications/{notification_id}/delivered")

    def handle_callback(self, callback_data: str, *, decided_by: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/gateway/telegram/callback",
            {"callback_data": callback_data, "decided_by": decided_by},
        )


class TelegramBotApiClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"

    def send_message(self, *, chat_id: str, text: str, buttons: list[dict[str, str]]) -> None:
        keyboard = [[{"text": button["text"], "callback_data": button["callback_data"]}] for button in buttons]
        payload = {
            "chat_id": chat_id,
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


def poll_once(api: GatewayApiClient, telegram: TelegramClient, *, chat_id: str) -> PollResult:
    sent = 0
    failed = 0
    for notification in api.list_notifications():
        payload = notification.get("payload") or {}
        try:
            telegram.send_message(
                chat_id=chat_id,
                text=str(payload.get("text", "")),
                buttons=list(payload.get("buttons") or []),
            )
            api.mark_delivered(str(notification["notification_id"]))
            sent += 1
        except Exception:
            failed += 1
    return PollResult(sent=sent, failed=failed)
