from __future__ import annotations

from .approval_gateway import ApprovalCard
from .storage import ApprovalRequest


TelegramApprovalCard = ApprovalCard


def _redact_command(command: str, *, max_length: int = 80) -> str:
    parts = []
    for part in command.split():
        if any(marker in part.upper() for marker in ("TOKEN=", "SECRET=", "PASSWORD=", "API_KEY=")):
            key = part.split("=", 1)[0]
            parts.append(f"{key}…")
        else:
            parts.append(part)
    redacted = " ".join(parts)
    if len(redacted) > max_length:
        return redacted[: max_length - 1] + "…"
    return redacted


def build_approval_card(approval: ApprovalRequest) -> ApprovalCard:
    node_id = str(approval.details.get("node_id", "未知节点"))
    command = _redact_command(str(approval.details.get("command", "")))
    task_name = str(approval.details.get("task_name", "")).strip()
    task_description = str(approval.details.get("task_description", "")).strip()
    lines = [
        "⚠️ 高风险审批",
        f"审批: `{approval.approval_id}`",
        f"风险: `{approval.risk}`",
        f"动作: `{approval.action}`",
    ]
    if task_name:
        lines.append(f"任务: `{task_name}`")
    if task_description:
        lines.append(f"说明: `{task_description}`")
    if approval.subject_type == "network_acl" and approval.action == "network.acl.apply":
        diff = str(approval.details.get("diff", ""))
        if len(diff) > 900:
            diff = diff[:899] + "…"
        lines.extend(
            [
                f"ACL: `{approval.details.get('current_path', approval.subject_id)}`",
                "Diff:",
                f"```diff\n{diff}\n```" if diff else "`(无 diff)`",
            ]
        )
    else:
        lines.extend(
            [
                f"节点: `{node_id}`",
                f"命令: `{command}`" if command else "命令: `(无)`",
            ]
        )
    text = "\n".join(lines)
    return ApprovalCard(
        text=text,
        buttons=[
            {"text": "✅ 批准", "callback_data": f"hmn:approval:approve:{approval.approval_id}"},
            {"text": "❌ 拒绝", "callback_data": f"hmn:approval:reject:{approval.approval_id}"},
        ],
    )


def build_telegram_approval_card(approval: ApprovalRequest) -> TelegramApprovalCard:
    return build_approval_card(approval)


def parse_approval_callback(callback_data: str) -> dict[str, str] | None:
    parts = callback_data.split(":")
    if len(parts) != 4:
        return None
    prefix, category, decision, approval_id = parts
    if prefix != "hmn" or category != "approval" or decision not in {"approve", "reject"}:
        return None
    if not approval_id.startswith("appr_"):
        return None
    return {"decision": decision, "approval_id": approval_id}


def parse_telegram_approval_callback(callback_data: str) -> dict[str, str] | None:
    return parse_approval_callback(callback_data)
