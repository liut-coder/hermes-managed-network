from __future__ import annotations

from dataclasses import dataclass

from .approval_gateway import ApprovalCard
from .approval_notifications import build_approval_card, parse_approval_callback
from .network_acl import dispatch_approved_network_acl_apply
from .network_base import NetworkProviderError
from .storage import SQLiteStore


@dataclass(frozen=True)
class PendingApprovalNotification:
    approval_id: str
    status: str
    delivery_hint: str
    card: ApprovalCard


@dataclass(frozen=True)
class TelegramApprovalCallbackResult:
    ok: bool
    message: str
    approval_id: str | None = None
    status: str | None = None
    dispatched_task_id: str | None = None


def build_pending_approval_notification(store: SQLiteStore, approval_id: str) -> PendingApprovalNotification:
    approval = store.load_approval_request(approval_id)
    if approval is None:
        raise ValueError(f"approval not found: {approval_id}")
    return PendingApprovalNotification(
        approval_id=approval.approval_id,
        status=approval.status,
        delivery_hint="telegram",
        card=build_approval_card(approval),
    )


def handle_telegram_approval_callback(
    store: SQLiteStore,
    callback_data: str,
    *,
    decided_by: str,
) -> TelegramApprovalCallbackResult:
    parsed = parse_approval_callback(callback_data)
    if parsed is None:
        return TelegramApprovalCallbackResult(ok=False, message="无效的审批回调。")

    approval_id = parsed["approval_id"]
    approval = store.load_approval_request(approval_id)
    if approval is None:
        return TelegramApprovalCallbackResult(ok=False, approval_id=approval_id, message="审批不存在或已清理。")
    if approval.status != "pending":
        return TelegramApprovalCallbackResult(
            ok=False,
            approval_id=approval.approval_id,
            status=approval.status,
            message=f"审批已处理：{approval.status}",
            dispatched_task_id=approval.details.get("dispatched_task_id"),
        )

    decision = parsed["decision"]
    target_status = "approved" if decision == "approve" else "rejected"
    resolved = store.resolve_approval_request(approval.approval_id, status=target_status, decided_by=decided_by)
    if resolved is None:
        return TelegramApprovalCallbackResult(ok=False, approval_id=approval.approval_id, message="审批处理失败。")

    dispatched_task_id = None
    if target_status == "approved" and resolved.subject_type == "task" and resolved.action == "task.run":
        task = store.dispatch_approved_task_request(resolved.approval_id)
        if task is None:
            return TelegramApprovalCallbackResult(
                ok=False,
                approval_id=resolved.approval_id,
                status=resolved.status,
                message="已批准，但任务创建失败：审批详情缺少 node_id/command。",
            )
        dispatched_task_id = task.task_id
    if target_status == "approved" and resolved.subject_type == "component_run" and resolved.action.startswith("component."):
        run = store.dispatch_approved_component_action(resolved.approval_id)
        if run is None:
            return TelegramApprovalCallbackResult(
                ok=False,
                approval_id=resolved.approval_id,
                status=resolved.status,
                message="已批准，但组件操作执行失败：审批详情不完整或动作不可调度。",
            )
    if target_status == "approved" and resolved.subject_type == "network_acl" and resolved.action == "network.acl.apply":
        try:
            dispatched = dispatch_approved_network_acl_apply(store, resolved.approval_id)
        except NetworkProviderError as exc:
            return TelegramApprovalCallbackResult(
                ok=False,
                approval_id=resolved.approval_id,
                status=resolved.status,
                message=f"已批准，但 Headscale ACL 应用失败：{exc}",
            )
        if not dispatched:
            return TelegramApprovalCallbackResult(
                ok=False,
                approval_id=resolved.approval_id,
                status=resolved.status,
                message="已批准，但 Headscale ACL 应用失败：审批详情不完整或文件不可用。",
            )

    if target_status == "approved":
        message = "已批准。"
        if dispatched_task_id:
            message += f" 已创建任务: {dispatched_task_id}"
    else:
        message = "已拒绝。不会创建任务。"

    return TelegramApprovalCallbackResult(
        ok=True,
        approval_id=resolved.approval_id,
        status=resolved.status,
        dispatched_task_id=dispatched_task_id,
        message=message,
    )
