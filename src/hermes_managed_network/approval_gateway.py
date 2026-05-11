from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class TelegramUpdateResult:
    processed: int = 0
    approved: int = 0
    rejected: int = 0
    failed: int = 0
    next_offset: int | None = None


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

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"telegram request failed: HTTP {exc.code}") from exc
        return json.loads(body) if body else {}

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
        self._request("POST", "/sendMessage", payload)

    def get_updates(self, *, offset: int | None = None, timeout: int = 0) -> list[dict[str, Any]]:
        query = {"timeout": timeout, "allowed_updates": json.dumps(["callback_query"])}
        if offset is not None:
            query["offset"] = offset
        body = self._request("GET", "/getUpdates?" + urllib.parse.urlencode(query))
        return list(body.get("result") or [])

    def answer_callback_query(self, callback_query_id: str, *, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._request("POST", "/answerCallbackQuery", payload)

    def clear_inline_keyboard(self, *, chat_id: str | int, message_id: str | int) -> None:
        payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}}
        self._request("POST", "/editMessageReplyMarkup", payload)


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


def process_telegram_callbacks(
    api: ApprovalGatewayApiClient,
    telegram: TelegramApprovalGatewayClient,
    *,
    offset: int | None = None,
    decided_by_prefix: str = "telegram",
) -> TelegramUpdateResult:
    processed = approved = rejected = failed = 0
    next_offset = offset
    for update in telegram.get_updates(offset=offset):
        update_id = int(update.get("update_id", 0))
        next_offset = max(next_offset or 0, update_id + 1)
        callback = update.get("callback_query") or {}
        callback_data = str(callback.get("data") or "")
        callback_id = str(callback.get("id") or "")
        user = callback.get("from") or {}
        username = user.get("username") or user.get("id") or "unknown"
        if not callback_data.startswith("hmn:approval:"):
            continue
        processed += 1
        try:
            result = api.handle_callback(callback_data, decided_by=f"{decided_by_prefix}:{username}")
            message = str(result.get("message") or "已处理。")
            status = str(result.get("status") or "")
            if status == "approved":
                approved += 1
            elif status == "rejected":
                rejected += 1
            telegram.answer_callback_query(callback_id, text=message[:180] if callback_id else None)
            msg = callback.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            message_id = msg.get("message_id")
            if chat_id is not None and message_id is not None:
                try:
                    telegram.clear_inline_keyboard(chat_id=chat_id, message_id=message_id)
                except Exception:
                    pass
        except Exception:
            failed += 1
            if callback_id:
                try:
                    telegram.answer_callback_query(callback_id, text="审批处理失败，请查看 HMN 日志。")
                except Exception:
                    pass
    return TelegramUpdateResult(
        processed=processed,
        approved=approved,
        rejected=rejected,
        failed=failed,
        next_offset=next_offset,
    )
