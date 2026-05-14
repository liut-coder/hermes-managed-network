from __future__ import annotations

import base64
import crypt
import hashlib
import hmac
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from uuid import uuid4

import hermes_managed_network
from markdown_it import MarkdownIt
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from typing import Any

from .signing import sign_task_payload
from .storage import Notification, SQLiteStore
from .approval_telegram_flow import handle_telegram_approval_callback
from .network_acl import dispatch_approved_network_acl_apply
from .network_base import NetworkProviderError
from .version import current_version_info, is_worker_compatible
from .web_console import register_web_console

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()
DEFAULT_DOCS_ROOT = Path("/srv/files")
SESSION_COOKIE_NAME = "hmn_web_session"
SESSION_TTL_SECONDS = 60 * 60 * 12
PBKDF2_PREFIX = "pbkdf2_sha256"


class JoinRequest(BaseModel):
    token: str
    fingerprint: str
    hostname: str
    addresses: list[str] = Field(default_factory=list)
    auto_confirm: bool = True


class JoinResponse(BaseModel):
    node_id: str
    status: str
    trust_level: str
    labels: list[str]


class HeartbeatRequest(BaseModel):
    fingerprint: str
    status: str = "ok"
    facts: dict[str, Any] = Field(default_factory=dict)


class HeartbeatResponse(BaseModel):
    node_id: str
    status: str
    master_version: str
    worker_compatible: bool = True


class RotateFingerprintRequest(BaseModel):
    fingerprint: str
    new_fingerprint: str


class RotateFingerprintResponse(BaseModel):
    node_id: str
    status: str


class VersionResponse(BaseModel):
    package_version: str
    api_version: str
    worker_protocol_version: str


class ConsoleNodeResponse(BaseModel):
    id: str
    name: str
    status: str
    live: str
    trust: str
    role: str
    ip: str
    os: str
    uptime: str
    cpu: int | float
    memory: int | float
    disk: int | float
    load: int | float
    hb: str
    exec: bool


class ConsoleTaskResponse(BaseModel):
    id: str
    node_id: str
    node_name: str
    command: str
    risk: str
    status: str
    created_by: str
    created_at: str


class ConsoleApprovalResponse(BaseModel):
    id: str
    subject_type: str
    subject_id: str
    action: str
    risk: str
    status: str
    requested_by: str
    created_at: str
    task_name: str | None = None
    task_description: str | None = None


class ConsoleMetricsResponse(BaseModel):
    online_nodes: int
    total_nodes: int
    managed_nodes: int
    pending_nodes: int
    pending_approvals: int
    running_tasks: int


class ConsoleServiceResponse(BaseModel):
    service_id: str
    name: str
    node_id: str
    kind: str
    runtime: str = ""
    domains: list[str]
    ports: list[int]
    status: str
    monitor_enabled: bool
    docs_path: str
    source: str
    business_category: str = "未分类"
    asset_category: str = "pending"
    asset_score: int = 0
    why_asset: list[str] = Field(default_factory=list)
    summary: str = ""
    deployment_type: str = ""
    project_name: str = ""
    business_name: str = ""
    business_purpose: str = ""
    public_exposed: bool = False
    backup_status: str = "unknown"
    tags: list[str] = Field(default_factory=list)


class ConsoleServiceGroupResponse(BaseModel):
    category: str
    services: list[ConsoleServiceResponse]
    count: int


class ConsoleServiceSheetResponse(BaseModel):
    service_id: str | None = None
    title: str
    business_category: str
    asset_category: str
    asset_score: int
    why_asset: list[str] = Field(default_factory=list)
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class ConsoleServiceCreateDialogResponse(BaseModel):
    title: str
    defaults: dict[str, Any]
    options: dict[str, list[str]]


class ConsoleServicesResponse(BaseModel):
    services: list[ConsoleServiceResponse]
    business_groups: list[ConsoleServiceGroupResponse]
    pending_discoveries: list[ConsoleServiceResponse]
    system_assets: list[ConsoleServiceResponse]
    sheet: ConsoleServiceSheetResponse
    create_dialog: ConsoleServiceCreateDialogResponse


class ConsoleSummaryResponse(BaseModel):
    metrics: ConsoleMetricsResponse
    nodes: list[ConsoleNodeResponse]
    tasks: list[ConsoleTaskResponse]
    approvals: list[ConsoleApprovalResponse]


class NodeAuthRequest(BaseModel):
    fingerprint: str
    worker_protocol_version: str | None = None


class TaskResponse(BaseModel):
    task_id: str
    command: str
    risk: str
    signature: str


class NoTaskResponse(BaseModel):
    task: None


class TaskResultRequest(BaseModel):
    fingerprint: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class TaskResultResponse(BaseModel):
    task_id: str
    status: str


class ApprovalDecisionRequest(BaseModel):
    decided_by: str = "telegram"


class ApprovalDecisionResponse(BaseModel):
    approval_id: str
    status: str
    dispatched_task_id: str | None = None


class NotificationResponse(BaseModel):
    notification_id: str
    channel: str
    subject_type: str
    subject_id: str
    status: str
    payload: dict[str, Any]


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]


class NotificationStatusResponse(BaseModel):
    notification_id: str
    status: str


class TelegramCallbackRequest(BaseModel):
    callback_data: str
    decided_by: str = "telegram"


class ApprovalGatewayCallbackRequest(BaseModel):
    client: str = "telegram"
    callback_data: str
    decided_by: str = "gateway"


class TelegramCallbackResponse(BaseModel):
    ok: bool
    message: str
    approval_id: str | None = None
    status: str | None = None
    dispatched_task_id: str | None = None


def _iso(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _latest_heartbeat_event(store: SQLiteStore, node_id: str):
    for event in reversed(store.list_audit_events()):
        if event.event_type == "node" and event.subject_id == node_id and event.action == "heartbeat":
            return event
    return None


def _heartbeat_label(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "无"
    if age_seconds < 60:
        return "刚刚"
    if age_seconds < 3600:
        return f"{age_seconds // 60} 分钟前"
    return f"{age_seconds // 3600} 小时前"


def _fact_number(facts: dict[str, Any], *keys: str, default: int | float = 0) -> int | float:
    for key in keys:
        value = facts.get(key)
        if isinstance(value, (int, float)):
            return value
    return default


def _percent(used: int | float, total: int | float) -> int:
    if not total:
        return 0
    return round(max(0, min(100, (used / total) * 100)))


def _memory_percent(facts: dict[str, Any]) -> int | float:
    value = _fact_number(facts, "memory_percent")
    if value:
        return value
    memory = facts.get("memory")
    if isinstance(memory, dict):
        total = memory.get("total_kb") or memory.get("total_bytes")
        available = memory.get("available_kb") or memory.get("free_kb") or memory.get("available_bytes")
        if isinstance(total, (int, float)) and isinstance(available, (int, float)):
            return _percent(total - available, total)
    return _fact_number(facts, "memory", default=0)


def _disk_percent(facts: dict[str, Any]) -> int | float:
    value = _fact_number(facts, "disk_percent")
    if value:
        return value
    disk = facts.get("disk")
    if isinstance(disk, dict):
        used = disk.get("used_bytes")
        total = disk.get("total_bytes")
        if isinstance(used, (int, float)) and isinstance(total, (int, float)):
            return _percent(used, total)
    return _fact_number(facts, "disk", default=0)


def _load_value(facts: dict[str, Any]) -> int | float:
    value = facts.get("load_average") or facts.get("load")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        for key in ("1m", "1", "one"):
            item = value.get(key)
            try:
                return round(float(item), 2)
            except (TypeError, ValueError):
                pass
    return 0


def _uptime_text(facts: dict[str, Any]) -> str:
    value = facts.get("uptime")
    if isinstance(value, dict) and isinstance(value.get("seconds"), (int, float)):
        seconds = int(value["seconds"])
    elif isinstance(value, (int, float)):
        seconds = int(value)
    elif isinstance(value, str):
        return value
    else:
        return "-"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _os_text(facts: dict[str, Any]) -> str:
    capabilities = facts.get("capabilities")
    os_family = capabilities.get("os_family") if isinstance(capabilities, dict) else None
    return str(facts.get("os_release") or facts.get("os") or facts.get("platform") or os_family or "unknown")


def _console_node_response(store: SQLiteStore, node) -> ConsoleNodeResponse:
    event = _latest_heartbeat_event(store, node.node_id)
    facts = event.details.get("facts", {}) if event else {}
    facts = facts if isinstance(facts, dict) else {}
    now = datetime.now(timezone.utc)
    age_seconds = int((now - event.created_at).total_seconds()) if event else None
    if node.status == "pending":
        live = "unknown"
    elif event is not None and event.outcome == "ok" and age_seconds is not None and age_seconds <= 300:
        live = "online"
    elif event is not None and age_seconds is not None and age_seconds <= 900:
        live = "stale"
    else:
        live = "offline"
    return ConsoleNodeResponse(
        id=node.node_id,
        name=node.hostname,
        status=node.status,
        live=live,
        trust=node.trust_level,
        role=(node.labels[0] if node.labels else "node"),
        ip=(node.network_ip or (node.addresses[0] if node.addresses else "-")),
        os=_os_text(facts),
        uptime=_uptime_text(facts),
        cpu=_fact_number(facts, "cpu_percent", "cpu"),
        memory=_memory_percent(facts),
        disk=_disk_percent(facts),
        load=_load_value(facts),
        hb=_heartbeat_label(age_seconds),
        exec=bool(facts.get("exec_enabled") or "task" in node.permission_bundles),
    )



def _notification_response(notification: Notification) -> NotificationResponse:
    return NotificationResponse(
        notification_id=notification.notification_id,
        channel=notification.channel,
        subject_type=notification.subject_type,
        subject_id=notification.subject_id,
        status=notification.status,
        payload=notification.payload,
    )


def _extract_doc_title_and_summary(path: Path, *, fallback_title: str) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = fallback_title
    summary = ""
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if heading:
                title = heading
            continue
        summary = line
        break
    return title, summary


def _list_docs(root: Path, subdir: str, *, category: str) -> list[dict[str, str]]:
    base = root / subdir
    if not base.exists():
        return []
    items: list[dict[str, str]] = []
    for path in sorted(base.rglob("*.md")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        fallback_title = path.relative_to(base).as_posix()
        title, summary = _extract_doc_title_and_summary(path, fallback_title=fallback_title)
        items.append(
            {
                "title": title,
                "path": relative,
                "url": f"/hmn-web/docs/file/{relative}",
                "viewer_url": f"/hmn-web/docs/view/{relative}",
                "category": category,
                "summary": summary,
            }
        )
    return items


def _docs_index_payload(root: Path) -> dict[str, list[dict[str, str]]]:
    return {
        "server_docs": _list_docs(root, "docs/server", category="server"),
        "service_docs": _list_docs(root, "service", category="service"),
    }


def _render_docs_index_html(payload: dict[str, list[dict[str, str]]]) -> str:
    def build_tree(items: list[dict[str, str]]) -> dict[str, Any]:
        tree: dict[str, Any] = {}
        for item in items:
            parts = item["path"].split("/")
            cursor = tree
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor.setdefault("__files__", []).append(item)
        return tree

    def render_tree(node: dict[str, Any]) -> str:
        sections: list[str] = []
        for name in sorted(key for key in node.keys() if key != "__files__"):
            sections.append(
                "<details class='tree-node' open>"
                f"<summary>{escape(name)}</summary>"
                f"<div class='tree-children'>{render_tree(node[name])}</div>"
                "</details>"
            )
        for item in sorted(node.get("__files__", []), key=lambda entry: entry["path"]):
            sections.append(
                "<div class='tree-file'>"
                f"<a class='tree-file-name' href=\"{escape(item['viewer_url'])}\">{escape(item['title'])}</a>"
                f"<code>{escape(item['path'])}</code>"
                f"<div class='tree-links'><a href=\"{escape(item['viewer_url'])}\">阅读</a><a href=\"{escape(item['url'])}\">原文</a></div>"
                "</div>"
            )
        return "".join(sections) or "<div class='tree-empty'>暂无文档</div>"

    def section(title: str, items: list[dict[str, str]]) -> str:
        tree_html = render_tree(build_tree(items))
        return f"<section><h2>{escape(title)}</h2><div class='docs-tree'>{tree_html}</div></section>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>文件索引</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ margin: 0; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0b1020; color: #e5e7eb; }}
    main {{ max-width: 1100px; margin: 0 auto; }}
    .nav {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 18px; }}
    .nav a {{ display: inline-flex; align-items: center; padding: 8px 12px; border-radius: 999px; background: #111827; border: 1px solid #1f2937; color: #cbd5e1; text-decoration: none; }}
    h1 {{ font-size: 30px; margin: 0 0 8px; }}
    p {{ color: #94a3b8; }}
    section {{ background: #111827; border: 1px solid #1f2937; border-radius: 20px; padding: 18px; margin: 16px 0; box-shadow: 0 12px 32px rgba(0,0,0,.28); }}
    h2 {{ font-size: 18px; margin: 0 0 12px; }}
    .docs-tree {{ display: grid; gap: 10px; }}
    .tree-node {{ margin: 0; padding-left: 12px; border-left: 1px solid #334155; }}
    .tree-node > summary {{ cursor: pointer; list-style: none; font-weight: 700; color: #f8fafc; margin: 6px 0; }}
    .tree-node > summary::-webkit-details-marker {{ display: none; }}
    .tree-node > summary::before {{ content: '▾'; display: inline-block; width: 14px; margin-right: 8px; color: #93c5fd; }}
    .tree-node:not([open]) > summary::before {{ content: '▸'; }}
    .tree-children {{ display: grid; gap: 8px; margin: 0 0 4px 8px; }}
    .tree-file {{ margin-left: 22px; padding: 10px 12px; border-radius: 14px; background: #0f172a; border: 1px solid #1e293b; }}
    .tree-file-name {{ display: inline-block; color: #f8fafc; font-size: 15px; font-weight: 700; text-decoration: none; margin-bottom: 6px; }}
    .tree-links {{ display: flex; gap: 12px; margin-top: 8px; }}
    .tree-links a {{ color: #60a5fa; text-decoration: none; }}
    .tree-empty {{ color: #94a3b8; }}
    code {{ display: block; color: #93c5fd; word-break: break-all; font-size: 12px; }}
  </style>
</head>
<body>
  <main>
    <nav class="nav">
      <a href="/">总览</a>
      <a href="/services">服务</a>
      <a href="/approvals">审批</a>
      <a href="/docs">旧版文档列表</a>
    </nav>
    <h1>文件索引</h1>
    <p>按目录层级展示文档文件名，支持阅读与原文入口。</p>
    {section("机器文档", payload["server_docs"])}
    {section("服务文档", payload["service_docs"])}
  </main>
</body>
</html>"""


def _render_markdown_viewer(relative_path: str, content: str) -> str:
    rendered = MarkdownIt("commonmark", {"html": False, "linkify": True, "typographer": False}).render(content)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HMN 文档 - {escape(relative_path)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --hmn-bg: #090a0f;
      --hmn-bg-soft: #10121a;
      --hmn-surface: rgba(22,24,33,.82);
      --hmn-panel: rgba(28,30,40,.76);
      --hmn-panel-solid: #171923;
      --hmn-elevated: rgba(34,37,49,.9);
      --hmn-line: rgba(255,255,255,.105);
      --hmn-line-strong: rgba(255,255,255,.16);
      --hmn-primary: #8f8cf8;
      --hmn-primary-2: #b7a7ff;
      --hmn-accent: #6f72d9;
      --hmn-text: #f5f5f7;
      --hmn-muted: #a7a9b8;
      --hmn-muted-2: #777b8d;
      --hmn-info: #64d2ff;
      --hmn-shadow: 0 18px 54px rgba(0,0,0,.36),0 2px 10px rgba(0,0,0,.24);
      --hmn-shadow-soft: 0 12px 32px rgba(0,0,0,.24);
      --hmn-blur: saturate(190%) blur(24px);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{
      margin: 0;
      font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
      background:
        radial-gradient(circle at 20% -10%, color-mix(in srgb, var(--hmn-primary) 18%, transparent), transparent 30%),
        radial-gradient(circle at 110% 8%, rgba(100,210,255,.10), transparent 28%),
        linear-gradient(180deg, var(--hmn-bg), color-mix(in srgb, var(--hmn-bg) 90%, #000 10%));
      color: var(--hmn-text);
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image: linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.014) 1px, transparent 1px);
      background-size: 48px 48px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,.65), transparent 70%);
    }}
    a {{ color: inherit; }}
    .shell {{ max-width: 1160px; margin: 0 auto; padding: 28px; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; margin-bottom: 20px; flex-wrap: wrap; }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 14px;
      border: 1px solid var(--hmn-line);
      border-radius: 999px;
      padding: 8px 12px;
      background: linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.03));
      color: var(--hmn-primary-2);
      text-decoration: none;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 10px 26px rgba(2,6,23,.10);
      backdrop-filter: var(--hmn-blur);
      -webkit-backdrop-filter: var(--hmn-blur);
    }}
    .title {{ margin: 0; font-size: clamp(2rem, 4vw, 2.7rem); line-height: 1.05; letter-spacing: -.03em; }}
    .subtitle {{ color: var(--hmn-muted); margin: 10px 0 0; font-size: .98rem; }}
    .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 42px;
      border-radius: 16px;
      border: 1px solid transparent;
      padding: 10px 16px;
      font-size: 13px;
      font-weight: 700;
      text-decoration: none;
      color: #fff;
      background: linear-gradient(135deg, rgba(139,92,246,.96), rgba(34,211,238,.9));
      box-shadow: 0 18px 36px rgba(76,29,149,.34), inset 0 1px 0 rgba(255,255,255,.28);
    }}
    .button.secondary {{
      color: var(--hmn-text);
      background: linear-gradient(180deg, rgba(255,255,255,.09), rgba(255,255,255,.04));
      border-color: rgba(255,255,255,.12);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.08);
    }}
    .hero {{
      border: 1px solid var(--hmn-line);
      border-radius: 26px;
      padding: 22px;
      background: linear-gradient(180deg, color-mix(in srgb, var(--hmn-surface) 92%, transparent), color-mix(in srgb, var(--hmn-panel) 70%, transparent));
      box-shadow: var(--hmn-shadow-soft);
      backdrop-filter: var(--hmn-blur);
      -webkit-backdrop-filter: var(--hmn-blur);
    }}
    .hero-meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
    .meta-chip {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      border: 1px solid var(--hmn-line);
      padding: 7px 11px;
      font-size: 12px;
      color: var(--hmn-muted);
      background: linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.03));
    }}
    .meta-chip strong {{ color: var(--hmn-text); font-weight: 700; }}
    .panel {{
      margin-top: 18px;
      border: 1px solid var(--hmn-line);
      border-radius: 26px;
      overflow: hidden;
      background: linear-gradient(180deg, color-mix(in srgb, var(--hmn-surface) 94%, transparent), color-mix(in srgb, var(--hmn-surface) 76%, transparent));
      box-shadow: var(--hmn-shadow-soft);
      backdrop-filter: var(--hmn-blur);
      -webkit-backdrop-filter: var(--hmn-blur);
    }}
    .panel-body {{ padding: 28px; line-height: 1.78; color: var(--hmn-text); }}
    .panel-body h1, .panel-body h2, .panel-body h3, .panel-body h4 {{ line-height: 1.25; margin: 1.4em 0 .7em; color: var(--hmn-text); }}
    .panel-body h1:first-child, .panel-body h2:first-child, .panel-body h3:first-child, .panel-body h4:first-child {{ margin-top: 0; }}
    .panel-body p, .panel-body li {{ color: #dde2ee; }}
    .panel-body a {{ color: var(--hmn-info); }}
    .panel-body strong {{ color: var(--hmn-text); }}
    .panel-body code {{
      background: color-mix(in srgb, var(--hmn-panel-solid) 86%, transparent);
      border: 1px solid color-mix(in srgb, var(--hmn-info) 18%, var(--hmn-line));
      border-radius: 10px;
      padding: .16em .45em;
      color: #f8fbff;
      font: .95em ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .panel-body pre {{
      margin: 1rem 0;
      background: #0f172a;
      border: 1px solid color-mix(in srgb, var(--hmn-info) 18%, var(--hmn-line));
      border-radius: 18px;
      padding: 18px;
      overflow-x: auto;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.045);
    }}
    .panel-body pre code {{ background: transparent; border: 0; padding: 0; color: #f8fbff; }}
    .panel-body ul, .panel-body ol {{ padding-left: 1.45em; }}
    .panel-body blockquote {{
      margin: 1rem 0;
      padding: 12px 16px;
      border-left: 3px solid color-mix(in srgb, var(--hmn-primary) 60%, transparent);
      background: color-mix(in srgb, var(--hmn-primary) 8%, transparent);
      border-radius: 0 16px 16px 0;
      color: var(--hmn-muted);
    }}
    .panel-body table {{ width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 16px; }}
    .panel-body th, .panel-body td {{ border: 1px solid var(--hmn-line); padding: 10px 12px; text-align: left; vertical-align: top; }}
    .panel-body th {{ background: color-mix(in srgb, var(--hmn-muted) 9%, var(--hmn-panel-solid)); }}
    @media (max-width: 780px) {{
      .shell {{ padding: 16px; }}
      .topbar {{ gap: 14px; }}
      .hero, .panel-body {{ padding: 18px; }}
      .actions {{ width: 100%; }}
      .actions .button {{ width: 100%; }}
      .title {{ font-size: 1.75rem; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div style="flex:1 1 680px; min-width:0;">
        <a class="eyebrow" href="/#docs">← 返回 HMN Console 文档中心</a>
        <section class="hero">
          <h1 class="title">HMN 文档</h1>
          <p class="subtitle">{escape(relative_path)}</p>
          <div class="hero-meta">
            <span class="meta-chip">文档查看页</span>
            <span class="meta-chip">来源 <strong>/srv/files</strong></span>
            <span class="meta-chip">阅读模式 <strong>Markdown 渲染</strong></span>
          </div>
        </section>
      </div>
      <div class="actions">
        <a class="button" href="/hmn-web/docs/file/{escape(relative_path)}">原始 Markdown</a>
      </div>
    </div>
    <section class="panel">
      <div class="panel-body markdown-body">{rendered}</div>
    </section>
  </div>
</body>
</html>"""


def _derive_password_hash(password: str, *, salt: bytes | None = None, iterations: int = 260000) -> str:
    actual_salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, iterations)
    return f"{PBKDF2_PREFIX}${iterations}${actual_salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith(f"{PBKDF2_PREFIX}$"):
        try:
            prefix, iterations_raw, salt_hex, digest_hex = stored_hash.split("$", 3)
            if prefix != PBKDF2_PREFIX:
                return False
            iterations = int(iterations_raw)
            salt = bytes.fromhex(salt_hex)
        except (ValueError, TypeError):
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(digest.hex(), digest_hex)
    if stored_hash.startswith(("$2a$", "$2b$", "$2y$", "$2x$")):
        try:
            return crypt.crypt(password, stored_hash) == stored_hash
        except Exception:
            return False
    return False


def _session_secret() -> str:
    secret = os.environ.get("HMN_WEB_SESSION_SECRET", "").strip()
    if secret:
        return secret
    admin_hash = os.environ.get("HMN_WEB_PASSWORD_HASH", "").strip()
    if admin_hash:
        return admin_hash
    raise RuntimeError("HMN_WEB_SESSION_SECRET or HMN_WEB_PASSWORD_HASH is required")


def _session_cookie_value(username: str, issued_at: int, secret: str) -> str:
    payload = f"{username}:{issued_at}"
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}:{signature}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii")


def _decode_session_cookie(value: str) -> tuple[str, int, str] | None:
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        username, issued_at_raw, signature = raw.rsplit(":", 2)
        return username, int(issued_at_raw), signature
    except Exception:
        return None


def _is_authenticated(request: Request) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return False
    parsed = _decode_session_cookie(cookie)
    if not parsed:
        return False
    username, issued_at, signature = parsed
    if issued_at + SESSION_TTL_SECONDS < int(datetime.now(timezone.utc).timestamp()):
        return False
    payload = f"{username}:{issued_at}"
    expected = hmac.new(_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _login_page(error: str = "") -> str:
    error_block = f"<div class='login-alert'>{escape(error)}</div>" if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HMN Web 登录</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg0:#040814;
      --bg1:#0a1024;
      --line:rgba(255,255,255,.14);
      --text:#eef2ff;
      --muted:rgba(226,232,240,.72);
      --danger:#fb7185;
      --shadow:0 32px 120px rgba(15,23,42,.52);
      --blur:28px;
      font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; min-height:100vh; color:var(--text);
      background:
        radial-gradient(circle at 18% 20%, rgba(139,92,246,.28), transparent 34%),
        radial-gradient(circle at 82% 16%, rgba(34,211,238,.18), transparent 30%),
        radial-gradient(circle at 50% 85%, rgba(56,189,248,.14), transparent 28%),
        linear-gradient(160deg, var(--bg0), var(--bg1) 58%, #020617 100%);
      overflow-x:hidden;
      overflow-y:auto;
    }}
    body::before, body::after {{ content:""; position:fixed; inset:auto; border-radius:999px; filter:blur(26px); opacity:.78; pointer-events:none; }}
    body::before {{ width:38vw; height:38vw; left:-10vw; top:-12vw; background:radial-gradient(circle, rgba(167,139,250,.34), transparent 62%); animation:floatA 18s ease-in-out infinite; }}
    body::after {{ width:34vw; height:34vw; right:-8vw; bottom:-10vw; background:radial-gradient(circle, rgba(34,211,238,.22), transparent 60%); animation:floatB 22s ease-in-out infinite; }}
    .grid {{ position:fixed; inset:0; background-image:linear-gradient(rgba(255,255,255,.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.03) 1px, transparent 1px); background-size:28px 28px; mask-image:radial-gradient(circle at center, black, transparent 78%); opacity:.22; }}
    .shell {{ position:relative; z-index:1; min-height:100vh; display:grid; place-items:center; padding:28px 18px; }}
    .panel {{
      position:relative; overflow:hidden;
      width:min(760px,100%); min-height:min(780px,calc(100vh - 56px));
      display:flex; flex-direction:column; justify-content:space-between; align-items:center; gap:28px;
      padding:42px 28px 30px;
      border-radius:36px; border:1px solid var(--line);
      background:linear-gradient(145deg, rgba(255,255,255,.12), rgba(255,255,255,.04));
      box-shadow:var(--shadow); backdrop-filter:blur(var(--blur)) saturate(150%); -webkit-backdrop-filter:blur(var(--blur)) saturate(150%);
      text-align:center;
    }}
    .panel::before {{ content:""; position:absolute; inset:12% -16% auto auto; width:260px; height:260px; border-radius:50%; background:radial-gradient(circle, rgba(139,92,246,.22), transparent 62%); filter:blur(10px); }}
    .panel::after {{ content:""; position:absolute; inset:auto auto -12% -14%; width:220px; height:220px; border-radius:50%; background:radial-gradient(circle, rgba(34,211,238,.16), transparent 64%); filter:blur(14px); }}
    .brand-wrap {{ position:relative; z-index:1; width:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:18px; padding-top:10px; }}
    .brand {{ display:flex; flex-direction:column; align-items:center; gap:14px; }}
    .brand-badge {{ width:58px; height:58px; border-radius:20px; display:grid; place-items:center; background:linear-gradient(135deg, rgba(139,92,246,.9), rgba(34,211,238,.78)); color:white; box-shadow:0 14px 30px rgba(76,29,149,.35); font-weight:800; letter-spacing:.08em; }}
    .brand-text strong {{ display:block; font-size:clamp(26px,3.2vw,34px); line-height:1.08; letter-spacing:-.03em; }}
    .brand-text small {{ display:block; margin-top:8px; font-size:11px; text-transform:uppercase; letter-spacing:.24em; color:var(--muted); }}
    .brand-note {{ max-width:420px; margin:0; color:var(--muted); font-size:14px; line-height:1.8; }}
    .card {{
      position:relative; z-index:1; width:min(420px,100%);
      padding:30px 26px; display:flex; flex-direction:column; justify-content:center;
      border-radius:28px; border:1px solid rgba(255,255,255,.12);
      background:linear-gradient(180deg, rgba(255,255,255,.09), rgba(255,255,255,.04));
      backdrop-filter:blur(22px); -webkit-backdrop-filter:blur(22px);
      text-align:left;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.08);
    }}
    .eyebrow {{ display:inline-flex; width:max-content; align-items:center; gap:8px; padding:8px 12px; border-radius:999px; background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.09); color:#cbd5e1; font-size:12px; letter-spacing:.04em; align-self:center; }}
    .card h2 {{ margin:18px 0 8px; font-size:30px; line-height:1.08; letter-spacing:-.03em; text-align:center; }}
    .card p {{ margin:0; color:var(--muted); line-height:1.7; font-size:14px; text-align:center; }}
    form {{ margin-top:24px; display:grid; gap:16px; }}
    label {{ display:grid; gap:8px; font-size:13px; color:#dbe4f3; }}
    .field {{ position:relative; }}
    input {{
      width:100%; border:1px solid rgba(255,255,255,.12); border-radius:18px; background:rgba(8,15,32,.42);
      color:var(--text); padding:16px 18px; font-size:15px; outline:none;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.06), 0 8px 24px rgba(2,6,23,.18);
      transition:border-color .22s ease, box-shadow .22s ease, transform .22s ease, background .22s ease;
    }}
    input::placeholder {{ color:rgba(226,232,240,.36); }}
    input:focus {{ border-color:rgba(139,92,246,.65); box-shadow:0 0 0 4px rgba(139,92,246,.14), inset 0 1px 0 rgba(255,255,255,.08); background:rgba(8,15,32,.56); transform:translateY(-1px); }}
    .submit {{
      border:0; border-radius:18px; padding:16px 18px; font-size:15px; font-weight:700; color:white; cursor:pointer;
      background:linear-gradient(135deg, rgba(139,92,246,.96), rgba(34,211,238,.9));
      box-shadow:0 18px 36px rgba(76,29,149,.34), inset 0 1px 0 rgba(255,255,255,.28);
      transition:transform .22s ease, box-shadow .22s ease, filter .22s ease;
    }}
    .submit:hover {{ transform:translateY(-1px); filter:brightness(1.04); box-shadow:0 22px 42px rgba(76,29,149,.42), inset 0 1px 0 rgba(255,255,255,.3); }}
    .submit:active {{ transform:translateY(0); }}
    .helper {{ display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-top:2px; color:var(--muted); font-size:12px; }}
    .login-alert {{ margin-top:18px; padding:12px 14px; border-radius:16px; border:1px solid rgba(251,113,133,.24); background:rgba(251,113,133,.1); color:#fecdd3; font-size:13px; text-align:center; }}
    .footnote {{ margin-top:18px; color:rgba(226,232,240,.52); font-size:12px; line-height:1.6; text-align:center; }}
    @keyframes floatA {{ 0%,100% {{ transform:translate3d(0,0,0); }} 50% {{ transform:translate3d(6vw,4vh,0); }} }}
    @keyframes floatB {{ 0%,100% {{ transform:translate3d(0,0,0); }} 50% {{ transform:translate3d(-5vw,-3vh,0); }} }}
    @media (max-width: 920px) {{
      .shell {{ padding:18px 12px; }}
      .panel {{ min-height:auto; gap:24px; padding:28px 18px 22px; border-radius:28px; }}
      .brand-text strong {{ font-size:28px; }}
      .brand-note {{ font-size:13px; line-height:1.7; }}
      .card {{ width:100%; padding:24px 18px; border-radius:22px; }}
      .card h2 {{ font-size:26px; }}
      .helper {{ justify-content:center; }}
    }}
  </style>
</head>
<body>
  <div class="grid" aria-hidden="true"></div>
  <div class="shell">
    <section class="panel">
      <div class="brand-wrap">
        <div class="brand">
          <div class="brand-badge">HMN</div>
          <div class="brand-text">
            <strong>Hermes Managed Network</strong>
            <small>Infrastructure Control Plane</small>
          </div>
        </div>
      </div>
      <div class="card">
        <span class="eyebrow">Secure Access · HMN Web</span>
        <h2>登录</h2>
        {error_block}
        <form method="post" action="/login">
          <label>
            用户名
            <div class="field"><input name="username" type="text" value="" autocomplete="username" placeholder="请输入用户名" required></div>
          </label>
          <label>
            密码
            <div class="field"><input name="password" type="password" autocomplete="current-password" placeholder="请输入密码" required></div>
          </label>
          <button class="submit" type="submit">进入 HMN Web</button>
        </form>
      </div>
    </section>
  </div>
</body>
</html>"""


def create_app(db_path: str | Path = DEFAULT_DB, *, docs_root: str | Path = DEFAULT_DOCS_ROOT) -> FastAPI:
    app = FastAPI(title="Hermes Managed Network", version="0.2.0", docs_url="/api/docs", redoc_url="/api/redoc")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code != 401:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        path = request.url.path
        if path.startswith("/api/") or path.startswith("/scripts/") or path == "/healthz":
            return JSONResponse(status_code=401, content={"detail": exc.detail})
        return RedirectResponse("/login", status_code=303)

    store = SQLiteStore(db_path)
    docs_base = Path(docs_root).expanduser().resolve()
    register_web_console(app, store, docs_base)
    admin_username = os.environ.get("HMN_WEB_USERNAME", "admin").strip() or "admin"
    admin_password_hash = os.environ.get("HMN_WEB_PASSWORD_HASH", "").strip()

    def _require_session(request: Request) -> None:
        if not _is_authenticated(request):
            raise HTTPException(status_code=401, detail="login required")

    def _login_response(target: str = "/") -> RedirectResponse:
        issued_at = int(datetime.now(timezone.utc).timestamp())
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            _session_cookie_value(admin_username, issued_at, _session_secret()),
            max_age=SESSION_TTL_SECONDS,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False, response_model=None)
    def login_page(request: Request, error: int = 0):
        if _is_authenticated(request):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_login_page("密码不正确，请重试。" if error else ""))

    @app.post("/login", include_in_schema=False)
    def login_submit(username: str = Form("admin"), password: str = Form("")) -> RedirectResponse:
        if not admin_password_hash:
            raise HTTPException(status_code=503, detail="hmn web password hash not configured")
        if username != admin_username or not _verify_password(password, admin_password_hash):
            return RedirectResponse("/login?error=1", status_code=303)
        return _login_response()

    @app.get("/logout", include_in_schema=False)
    def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return response

    @app.get("/api/v1/session")
    def session_status(request: Request) -> JSONResponse:
        if not _is_authenticated(request):
            raise HTTPException(status_code=401, detail="login required")
        return JSONResponse({"authenticated": True, "username": admin_username, "expires_in": SESSION_TTL_SECONDS})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/version", response_model=VersionResponse)
    def version() -> VersionResponse:
        info = current_version_info()
        return VersionResponse(
            package_version=info.package_version,
            api_version=info.api_version,
            worker_protocol_version=info.worker_protocol_version,
        )

    @app.get("/api/v1/console/summary", response_model=ConsoleSummaryResponse)
    def console_summary(request: Request) -> ConsoleSummaryResponse:
        _require_session(request)
        nodes = [_console_node_response(store, node) for node in store.list_nodes()]
        tasks = store.list_tasks()[:8]
        approvals = store.list_approval_requests()[:8]
        node_names = {node.id: node.name for node in nodes}
        return ConsoleSummaryResponse(
            metrics=ConsoleMetricsResponse(
                online_nodes=sum(1 for node in nodes if node.live == "online"),
                total_nodes=len(nodes),
                managed_nodes=sum(1 for node in nodes if node.status == "managed"),
                pending_nodes=sum(1 for node in nodes if node.status == "pending"),
                pending_approvals=sum(1 for approval in approvals if approval.status == "pending"),
                running_tasks=sum(1 for task in tasks if task.status == "running"),
            ),
            nodes=nodes,
            tasks=[
                ConsoleTaskResponse(
                    id=task.task_id,
                    node_id=task.node_id,
                    node_name=node_names.get(task.node_id, task.node_id),
                    command=task.command,
                    risk=task.risk,
                    status=task.status,
                    created_by=task.created_by,
                    created_at=_iso(task.created_at),
                )
                for task in tasks
            ],
            approvals=[
                ConsoleApprovalResponse(
                    id=approval.approval_id,
                    subject_type=approval.subject_type,
                    subject_id=approval.subject_id,
                    action=approval.action,
                    risk=approval.risk,
                    status=approval.status,
                    requested_by=approval.requested_by,
                    created_at=_iso(approval.created_at),
                    task_name=str(approval.details.get("task_name")) if approval.details.get("task_name") is not None else None,
                    task_description=(
                        str(approval.details.get("task_description"))
                        if approval.details.get("task_description") is not None
                        else None
                    ),
                )
                for approval in approvals
            ],
        )

    def _asset_script(name: str) -> Response:
        script_path = Path(hermes_managed_network.__file__).resolve().parent / "assets" / name
        if not script_path.exists():
            raise HTTPException(status_code=404, detail=f"{name} not found")
        return Response(script_path.read_text(), media_type="text/x-shellscript")

    def _docs_file(path: str) -> Path:
        candidate = (docs_base / path).resolve()
        if docs_base not in candidate.parents and candidate != docs_base:
            raise HTTPException(status_code=400, detail="invalid docs path")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="docs file not found")
        return candidate

    @app.get("/api/v1/hmn-web/docs/index")
    def hmn_web_docs_index_api(request: Request) -> dict[str, list[dict[str, str]]]:
        _require_session(request)
        return _docs_index_payload(docs_base)

    @app.get("/hmn-web/docs", response_class=HTMLResponse, include_in_schema=False, response_model=None)
    def hmn_web_docs_index(request: Request):
        if not _is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(_render_docs_index_html(_docs_index_payload(docs_base)))

    @app.get("/hmn-web/docs/file/{doc_path:path}", include_in_schema=False)
    def hmn_web_docs_file(request: Request, doc_path: str) -> FileResponse:
        _require_session(request)
        return FileResponse(_docs_file(doc_path), media_type="text/markdown; charset=utf-8")

    @app.get("/hmn-web/docs/view/{doc_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def hmn_web_docs_view(request: Request, doc_path: str) -> HTMLResponse:
        _require_session(request)
        path = _docs_file(doc_path)
        return HTMLResponse(_render_markdown_viewer(doc_path, path.read_text(encoding="utf-8")))

    def _service_business_category(service) -> str:
        metadata = service.metadata or {}
        for key in ("business_category", "business", "category", "biz_category"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "未分类"

    def _service_asset_category(service) -> str:
        metadata = service.metadata or {}
        value = metadata.get("asset_category") or getattr(service, "asset_category", "")
        if isinstance(value, str) and value.strip():
            return value.strip()
        if service.source == "system" or service.kind in {"system", "platform", "infra"}:
            return "system"
        if service.source == "discovery" or service.status in {"discovered", "pending", "pending_review"}:
            return "pending"
        return "main"

    def _service_asset_score(service) -> int:
        metadata = service.metadata or {}
        value = metadata.get("asset_score") or getattr(service, "asset_score", 0)
        return int(value) if isinstance(value, (int, float, str)) and str(value).lstrip("-").isdigit() else 0

    def _service_why_asset(service) -> list[str]:
        metadata = service.metadata or {}
        value = metadata.get("why_asset") or getattr(service, "why_asset", [])
        return [str(item) for item in value] if isinstance(value, list) else []

    def _service_summary(service) -> str:
        parts = [service.name]
        if service.domains:
            parts.append(", ".join(service.domains))
        if service.ports:
            parts.append("ports " + ",".join(map(str, service.ports)))
        return " · ".join(parts)

    def _service_response(service) -> ConsoleServiceResponse:
        return ConsoleServiceResponse(
            service_id=service.service_id,
            name=service.name,
            node_id=service.node_id,
            kind=service.kind,
            domains=list(service.domains),
            ports=list(service.ports),
            status=service.status,
            monitor_enabled=service.monitor_enabled,
            docs_path=service.docs_path,
            source=service.source,
            business_category=_service_business_category(service),
            asset_category=_service_asset_category(service),
            asset_score=_service_asset_score(service),
            why_asset=_service_why_asset(service),
            summary=_service_summary(service),
        )

    @app.get("/api/v1/console/services", response_model=ConsoleServicesResponse)
    def console_services(request: Request) -> ConsoleServicesResponse:
        _require_session(request)
        services = [_service_response(service) for service in store.list_service_records()]
        business_groups_map: dict[str, list[ConsoleServiceResponse]] = {}
        pending_discoveries: list[ConsoleServiceResponse] = []
        system_assets: list[ConsoleServiceResponse] = []
        for service in services:
            if service.asset_category == "pending":
                pending_discoveries.append(service)
            elif service.asset_category == "system":
                system_assets.append(service)
            else:
                business_groups_map.setdefault(service.business_category, []).append(service)
        business_groups = [
            ConsoleServiceGroupResponse(category=category, services=group, count=len(group))
            for category, group in sorted(business_groups_map.items(), key=lambda item: item[0])
        ]
        selected = next((service for service in services if service.asset_category == "main"), services[0] if services else None)
        sheet = ConsoleServiceSheetResponse(
            service_id=selected.service_id if selected else None,
            title=selected.name if selected else "服务详情",
            business_category=selected.business_category if selected else "未分类",
            asset_category=selected.asset_category if selected else "main",
            asset_score=selected.asset_score if selected else 0,
            why_asset=list(selected.why_asset) if selected else [],
            summary=selected.summary if selected else "暂无服务",
            details=(selected.model_dump() if selected else {}),
        )
        create_dialog = ConsoleServiceCreateDialogResponse(
            title="新增服务资产",
            defaults={
                "source": "manual",
                "status": "active",
                "business_category": selected.business_category if selected else "未分类",
                "asset_category": "main",
            },
            options={
                "business_categories": [group.category for group in business_groups] or ["未分类"],
                "asset_categories": ["main", "pending", "system"],
                "sources": ["manual", "discovery", "system"],
            },
        )
        return ConsoleServicesResponse(
            services=services,
            business_groups=business_groups,
            pending_discoveries=pending_discoveries,
            system_assets=system_assets,
            sheet=sheet,
            create_dialog=create_dialog,
        )

    @app.get("/scripts/join.sh", include_in_schema=False)
    def join_script() -> Response:
        return _asset_script("join.sh")

    @app.get("/scripts/worker.sh", include_in_schema=False)
    def worker_script() -> Response:
        return _asset_script("worker.sh")

    @app.get("/scripts/worker-lite.sh", include_in_schema=False)
    def worker_lite_script() -> Response:
        return _asset_script("worker-lite.sh")

    @app.post("/api/v1/join", response_model=JoinResponse)
    def join(request: JoinRequest) -> JoinResponse:
        token = store.load_token(request.token)
        if token is None:
            raise HTTPException(status_code=404, detail="join token not found")
        consumed = store.consume_token(request.token, node_fingerprint=request.fingerprint)
        if consumed is None:
            refreshed = store.load_token(request.token)
            status = refreshed.status if refreshed is not None else "unknown"
            raise HTTPException(status_code=409, detail=f"join token is {status}")

        node_id = "node_" + uuid4().hex[:12]
        node = store.register_pending_node(
            node_id=node_id,
            fingerprint=request.fingerprint,
            hostname=request.hostname,
            addresses=request.addresses,
            trust_level=consumed.trust_level,
            labels=consumed.labels,
        )
        permission_bundles: list[str] = []
        if request.auto_confirm:
            permission_bundles = ["observe", "task"]
            node.status = "managed"
            node.permission_bundles = permission_bundles
            store.save_node(node)
        store.record_audit(
            event_type="node",
            subject_type="node",
            subject_id=node.node_id,
            action="join",
            outcome="ok",
            details={
                "hostname": node.hostname,
                "addresses": node.addresses,
                "trust_level": node.trust_level,
                "labels": node.labels,
                "auto_confirm": request.auto_confirm,
                "permission_bundles": permission_bundles,
            },
        )
        return JoinResponse(
            node_id=node.node_id,
            status=node.status,
            trust_level=node.trust_level,
            labels=node.labels,
        )

    @app.post("/api/v1/nodes/{node_id}/heartbeat", response_model=HeartbeatResponse)
    def heartbeat(node_id: str, request: HeartbeatRequest) -> HeartbeatResponse:
        node = store.load_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        if node.status == "revoked":
            raise HTTPException(status_code=403, detail="node is revoked")
        if node.status != "managed":
            raise HTTPException(status_code=403, detail="node is not managed")
        if node.fingerprint != request.fingerprint:
            raise HTTPException(status_code=403, detail="node fingerprint mismatch")
        outcome = "ok" if request.status == "ok" else "warn"
        worker_protocol = request.facts.get("worker_protocol_version") if isinstance(request.facts, dict) else None
        worker_compatible = is_worker_compatible(current_version_info().worker_protocol_version, worker_protocol)
        store.record_audit(
            event_type="node",
            subject_type="node",
            subject_id=node.node_id,
            action="heartbeat",
            outcome=outcome if worker_compatible else "warn",
            details={"status": request.status, "facts": request.facts, "worker_compatible": worker_compatible},
        )
        return HeartbeatResponse(
            node_id=node.node_id,
            status=request.status,
            master_version=current_version_info().worker_protocol_version,
            worker_compatible=worker_compatible,
        )

    @app.post("/api/v1/nodes/{node_id}/rotate-fingerprint", response_model=RotateFingerprintResponse)
    def rotate_fingerprint(node_id: str, request: RotateFingerprintRequest) -> RotateFingerprintResponse:
        node = store.load_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        if node.fingerprint != request.fingerprint:
            raise HTTPException(status_code=403, detail="node fingerprint mismatch")
        updated = store.rotate_node_fingerprint(
            node_id,
            current_fingerprint=request.fingerprint,
            new_fingerprint=request.new_fingerprint,
        )
        if updated is None:
            raise HTTPException(status_code=403, detail="node fingerprint mismatch")
        return RotateFingerprintResponse(node_id=updated.node_id, status="rotated")

    @app.post("/api/v1/nodes/{node_id}/tasks/next", response_model=TaskResponse | NoTaskResponse)
    def next_task(node_id: str, request: NodeAuthRequest) -> TaskResponse | NoTaskResponse:
        node = store.load_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        if node.status == "revoked":
            raise HTTPException(status_code=403, detail="node is revoked")
        if node.status != "managed":
            raise HTTPException(status_code=403, detail="node is not managed")
        if node.fingerprint != request.fingerprint:
            raise HTTPException(status_code=403, detail="node fingerprint mismatch")
        if not is_worker_compatible(current_version_info().worker_protocol_version, request.worker_protocol_version):
            raise HTTPException(status_code=426, detail="worker protocol version mismatch; update node worker")
        store.expire_stuck_tasks()
        task = store.claim_next_task(node_id, executor="worker")
        if task is None:
            return NoTaskResponse(task=None)
        return TaskResponse(
            task_id=task.task_id,
            command=task.command,
            risk=task.risk,
            signature=sign_task_payload(
                node_fingerprint=node.fingerprint,
                task_id=task.task_id,
                command=task.command,
                risk=task.risk,
            ),
        )

    @app.post("/api/v1/tasks/{task_id}/result", response_model=TaskResultResponse)
    def task_result(task_id: str, request: TaskResultRequest) -> TaskResultResponse:
        task = store.load_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        node = store.load_node(task.node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        if node.status == "revoked":
            raise HTTPException(status_code=403, detail="node is revoked")
        if node.status != "managed":
            raise HTTPException(status_code=403, detail="node is not managed")
        if node.fingerprint != request.fingerprint:
            raise HTTPException(status_code=403, detail="node fingerprint mismatch")
        updated = store.complete_task(task_id, exit_code=request.exit_code, stdout=request.stdout, stderr=request.stderr)
        if updated is None:
            raise HTTPException(status_code=409, detail="task is already terminal")
        return TaskResultResponse(task_id=updated.task_id, status=updated.status)

    def _resolve_approval(
        approval_id: str,
        *,
        status: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        existing = store.load_approval_request(approval_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="approval not found")
        if existing.status != "pending":
            raise HTTPException(status_code=409, detail=f"approval is {existing.status}")
        approval = store.resolve_approval_request(approval_id, status=status, decided_by=request.decided_by)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        dispatched_task_id = None
        if status == "approved" and approval.subject_type == "task" and approval.action == "task.run":
            task = store.dispatch_approved_task_request(approval.approval_id)
            if task is None:
                raise HTTPException(status_code=422, detail="approval cannot be dispatched")
            dispatched_task_id = task.task_id
        if status == "approved" and approval.subject_type == "component_run" and approval.action.startswith("component."):
            run = store.dispatch_approved_component_action(approval.approval_id)
            if run is None:
                raise HTTPException(status_code=422, detail="approval cannot be dispatched")
        if status == "approved" and approval.subject_type == "network_acl" and approval.action == "network.acl.apply":
            try:
                dispatched = dispatch_approved_network_acl_apply(store, approval.approval_id)
            except NetworkProviderError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if not dispatched:
                raise HTTPException(status_code=422, detail="approval cannot be dispatched")
        return ApprovalDecisionResponse(
            approval_id=approval.approval_id,
            status=approval.status,
            dispatched_task_id=dispatched_task_id,
        )

    @app.post("/api/v1/approvals/{approval_id}/approve", response_model=ApprovalDecisionResponse)
    def approve_approval(approval_id: str, request: ApprovalDecisionRequest) -> ApprovalDecisionResponse:
        return _resolve_approval(approval_id, status="approved", request=request)

    @app.post("/api/v1/approvals/{approval_id}/reject", response_model=ApprovalDecisionResponse)
    def reject_approval(approval_id: str, request: ApprovalDecisionRequest) -> ApprovalDecisionResponse:
        return _resolve_approval(approval_id, status="rejected", request=request)

    def _gateway_notifications(client: str) -> NotificationListResponse:
        notifications = [
            _notification_response(notification)
            for notification in store.list_notifications(status="pending")
            if notification.channel == client
        ]
        return NotificationListResponse(notifications=notifications)

    def _gateway_notification_delivered(notification_id: str) -> NotificationStatusResponse:
        notification = store.mark_notification_delivered(notification_id)
        if notification is None:
            raise HTTPException(status_code=404, detail="notification not found")
        return NotificationStatusResponse(notification_id=notification.notification_id, status=notification.status)

    def _gateway_callback(callback_data: str, *, decided_by: str) -> TelegramCallbackResponse:
        result = handle_telegram_approval_callback(store, callback_data, decided_by=decided_by)
        if not result.ok:
            status_code = 409 if result.status in {"approved", "rejected"} else 400
            raise HTTPException(
                status_code=status_code,
                detail={
                    "ok": result.ok,
                    "message": result.message,
                    "approval_id": result.approval_id,
                    "status": result.status,
                    "dispatched_task_id": result.dispatched_task_id,
                },
            )
        return TelegramCallbackResponse(
            ok=result.ok,
            message=result.message,
            approval_id=result.approval_id,
            status=result.status,
            dispatched_task_id=result.dispatched_task_id,
        )

    @app.get("/api/v1/gateway/approval/notifications", response_model=NotificationListResponse)
    def approval_gateway_notifications(client: str = "telegram") -> NotificationListResponse:
        return _gateway_notifications(client)

    @app.post(
        "/api/v1/gateway/approval/notifications/{notification_id}/delivered",
        response_model=NotificationStatusResponse,
    )
    def approval_gateway_notification_delivered(notification_id: str) -> NotificationStatusResponse:
        return _gateway_notification_delivered(notification_id)

    @app.post("/api/v1/gateway/approval/callback", response_model=TelegramCallbackResponse)
    def approval_gateway_callback(request: ApprovalGatewayCallbackRequest) -> TelegramCallbackResponse:
        if request.client != "telegram":
            raise HTTPException(status_code=400, detail="unsupported approval gateway client")
        return _gateway_callback(request.callback_data, decided_by=request.decided_by)

    @app.get("/api/v1/gateway/telegram/notifications", response_model=NotificationListResponse)
    def telegram_notifications() -> NotificationListResponse:
        return _gateway_notifications("telegram")

    @app.post(
        "/api/v1/gateway/telegram/notifications/{notification_id}/delivered",
        response_model=NotificationStatusResponse,
    )
    def telegram_notification_delivered(notification_id: str) -> NotificationStatusResponse:
        return _gateway_notification_delivered(notification_id)

    @app.post("/api/v1/gateway/telegram/callback", response_model=TelegramCallbackResponse)
    def telegram_callback(request: TelegramCallbackRequest) -> TelegramCallbackResponse:
        return _gateway_callback(request.callback_data, decided_by=request.decided_by)

    return app


app = create_app()
