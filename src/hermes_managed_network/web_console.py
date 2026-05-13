from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .components import ComponentRegistry
from .storage import ApprovalRequest, AuditEvent, SQLiteStore, Task

SECRET_KEYS = {"token", "password", "secret", "api_key", "authorization", "private_key", "cookie", "session"}
LOW_RISK_COMMANDS = {"uptime", "df -h", "hmn health probe"}


class ConsoleTaskCreateRequest(BaseModel):
    node_id: str
    command: str
    created_by: str = "hmn-web"
    executor: str = "worker"


class ConsoleTaskCreateResponse(BaseModel):
    task_id: str | None = None
    approval_id: str | None = None
    status: str
    risk: str


class ComponentActionRequest(BaseModel):
    node_id: str = ""
    action: str = "plan"
    config: dict[str, Any] = {}
    created_by: str = "hmn-web"


class NetworkAclPlanRequest(BaseModel):
    proposed_acl: str = ""
    created_by: str = "hmn-web"


class BackupPlanRequest(BaseModel):
    node_id: str = ""
    target: str = ""
    created_by: str = "hmn-web"


class RestoreRunRequest(BaseModel):
    node_id: str = ""
    backup_id: str = ""
    created_by: str = "hmn-web"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in SECRET_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def risk_for_command(command: str) -> str:
    stripped = " ".join(command.strip().split())
    if stripped in LOW_RISK_COMMANDS or stripped.startswith("systemctl status "):
        return "low"
    return "high"


def html_page(title: str, body: str) -> HTMLResponse:
    nav = """
    <nav class="nav">
      <a href="/">总览</a><a href="/nodes">节点</a><a href="/services">服务</a>
      <a href="/tasks">任务</a><a href="/approvals">审批</a><a href="/docs">文档</a>
      <a href="/audit">审计</a><a href="/components">组件</a><a href="/network">网络</a><a href="/backups">备份</a>
    </nav>
    """
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f6f7f9;color:#111}"
        "main{max-width:980px;margin:auto;padding:16px}.nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}.nav a,.btn{background:#111;color:white;padding:8px 11px;border-radius:12px;text-decoration:none;border:0}"
        ".card{background:white;border-radius:18px;padding:14px;margin:10px 0;box-shadow:0 2px 12px #0001}.muted{color:#666}.badge{display:inline-block;border-radius:999px;padding:2px 8px;background:#eef}"
        "input,select{padding:8px;border-radius:10px;border:1px solid #ccc;max-width:100%}pre{white-space:pre-wrap;background:#111;color:#eee;padding:12px;border-radius:12px;overflow:auto}</style>"
        "</head><body><main>" + nav + body + "</main></body></html>"
    )


def _task_card(task: Task) -> str:
    return (
        "<div class='card'>"
        f"<b><a href='/tasks/{escape(task.task_id)}'>{escape(task.command)}</a></b> "
        f"<span class='badge'>{escape(task.status)}</span> <span class='badge'>{escape(task.risk)}</span>"
        f"<div class='muted'>{escape(task.node_id)} · {escape(task.created_by)}</div>"
        "</div>"
    )


def _approval_card(approval: ApprovalRequest) -> str:
    details = escape(json.dumps(redact(approval.details), ensure_ascii=False, sort_keys=True))
    buttons = ""
    if approval.status == "pending":
        buttons = (
            f"<form method='post' action='/approvals/{escape(approval.approval_id)}/approve' style='display:inline'><input name='decided_by' value='hmn-web' hidden><button class='btn'>允许</button></form> "
            f"<form method='post' action='/approvals/{escape(approval.approval_id)}/reject' style='display:inline'><input name='decided_by' value='hmn-web' hidden><button class='btn'>取消</button></form>"
        )
    return (
        "<div class='card'>"
        f"<b>{escape(approval.action)}</b> <span class='badge'>{escape(approval.risk)}</span> <span class='badge'>{escape(approval.status)}</span>"
        f"<div class='muted'>{escape(approval.approval_id)} · {escape(approval.subject_type)}:{escape(approval.subject_id)}</div>"
        f"<pre>{details}</pre>{buttons}</div>"
    )


def register_web_console(app, store: SQLiteStore, docs_base: Path) -> None:
    router = APIRouter()

    def docs_file(path: str) -> Path:
        candidate = (docs_base / path).resolve()
        if docs_base not in candidate.parents and candidate != docs_base:
            raise HTTPException(status_code=400, detail="invalid docs path")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="docs file not found")
        if candidate.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml", ".log"}:
            raise HTTPException(status_code=400, detail="unsupported docs file")
        return candidate

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        nodes = store.list_nodes()
        services = store.list_service_records()
        tasks = store.list_tasks()[:6]
        approvals = store.list_approval_requests(status="pending")[:6]
        body = "<h1>HMN 控制台</h1>"
        body += f"<div class='card'>节点 {len(nodes)} · 服务 {len(services)} · 待审批 {len(approvals)} · 任务 {len(store.list_tasks())}</div>"
        body += "<h2>节点</h2>" + "".join(f"<div class='card'><a href='/nodes/{escape(n.node_id)}'>{escape(n.hostname)}</a> <span class='badge'>{escape(n.status)}</span></div>" for n in nodes[:6])
        body += "<h2>服务</h2>" + "".join(f"<div class='card'><a href='/services/{escape(s.service_id)}'>{escape(s.name)}</a> <span class='muted'>{escape(s.node_id)}</span></div>" for s in services[:6])
        body += "<h2>任务</h2>" + "".join(_task_card(t) for t in tasks)
        body += "<h2>审批</h2>" + "".join(_approval_card(a) for a in approvals)
        return html_page("HMN 控制台", body)

    @router.get("/nodes", response_class=HTMLResponse, include_in_schema=False)
    def nodes_page() -> HTMLResponse:
        body = "<h1>节点</h1>" + "".join(
            f"<div class='card'><b><a href='/nodes/{escape(n.node_id)}'>{escape(n.hostname)}</a></b> <span class='badge'>{escape(n.status)}</span><div class='muted'>{escape(n.node_id)} · {escape(', '.join(n.addresses))}</div></div>"
            for n in store.list_nodes()
        )
        return html_page("节点", body)

    @router.get("/nodes/{node_id}", response_class=HTMLResponse, include_in_schema=False)
    def node_detail(node_id: str) -> HTMLResponse:
        node = store.load_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        body = f"<h1>{escape(node.hostname)}</h1><div class='card'><pre>{escape(json.dumps(redact(node.__dict__), ensure_ascii=False, indent=2))}</pre></div>"
        body += f"<p><a class='btn' href='/tasks/new?node_id={escape(node.node_id)}'>下发任务</a></p>"
        return html_page("节点详情", body)

    @router.get("/services", response_class=HTMLResponse, include_in_schema=False)
    def services_page() -> HTMLResponse:
        body = "<h1>服务</h1>" + "".join(
            f"<div class='card'><b><a href='/services/{escape(s.service_id)}'>{escape(s.name)}</a></b> <span class='badge'>{escape(s.status)}</span><div class='muted'>{escape(s.node_id)} · ports {escape(','.join(map(str, s.ports)))}</div></div>"
            for s in store.list_service_records()
        )
        return html_page("服务", body)

    @router.get("/services/{service_id}", response_class=HTMLResponse, include_in_schema=False)
    def service_detail(service_id: str) -> HTMLResponse:
        service = store.load_service_record(service_id)
        if service is None:
            raise HTTPException(status_code=404, detail="service not found")
        body = f"<h1>{escape(service.name)}</h1><div class='card'><pre>{escape(json.dumps(redact(service.__dict__), ensure_ascii=False, indent=2, default=str))}</pre></div>"
        if service.docs_path:
            body += f"<a class='btn' href='/docs/file/{escape(service.docs_path)}'>打开文档</a>"
        return html_page("服务详情", body)

    @router.get("/tasks", response_class=HTMLResponse, include_in_schema=False)
    def tasks_page() -> HTMLResponse:
        nodes = store.list_nodes()
        options = "".join(f"<option value='{escape(n.node_id)}'>{escape(n.hostname)}</option>" for n in nodes)
        body = "<h1>任务</h1><div class='card'><form method='post' action='/tasks'><select name='node_id'>" + options + "</select> <input name='command' value='uptime'> <input name='created_by' value='hmn-web' hidden><button class='btn'>运行</button></form></div>"
        body += "".join(_task_card(task) for task in store.list_tasks())
        return html_page("任务", body)

    @router.get("/tasks/new", response_class=HTMLResponse, include_in_schema=False)
    def task_new(node_id: str = "") -> HTMLResponse:
        body = f"<h1>新任务</h1><div class='card'><form method='post' action='/tasks'><input name='node_id' value='{escape(node_id)}'> <input name='command' value='uptime'> <input name='created_by' value='hmn-web'><button class='btn'>运行</button></form></div>"
        return html_page("新任务", body)

    @router.post("/tasks", include_in_schema=False)
    def task_create_form(node_id: str = Form(...), command: str = Form(...), created_by: str = Form("hmn-web")):
        result = create_console_task(ConsoleTaskCreateRequest(node_id=node_id, command=command, created_by=created_by))
        target = f"/tasks/{result.task_id}" if result.task_id else "/approvals"
        return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)

    @router.get("/tasks/{task_id}", response_class=HTMLResponse, include_in_schema=False)
    def task_detail(task_id: str) -> HTMLResponse:
        task = store.load_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        body = f"<h1>任务 {escape(task.task_id)}</h1><div class='card'><pre>{escape(json.dumps(redact(task.__dict__), ensure_ascii=False, indent=2, default=str))}</pre></div>"
        return html_page("任务详情", body)

    def create_console_task(request: ConsoleTaskCreateRequest) -> ConsoleTaskCreateResponse:
        node = store.load_node(request.node_id)
        if node is None or node.status != "managed":
            raise HTTPException(status_code=404, detail="managed node not found")
        risk = risk_for_command(request.command)
        if risk == "low":
            task = store.create_task(
                node_id=request.node_id,
                command=request.command,
                risk=risk,
                created_by=request.created_by,
                executor=request.executor,
            )
            return ConsoleTaskCreateResponse(task_id=task.task_id, status=task.status, risk=task.risk)
        approval = store.create_approval_request(
            subject_type="task",
            subject_id="console_task_request",
            action="task.run",
            risk=risk,
            requested_by=request.created_by,
            details={"node_id": request.node_id, "command": request.command, "created_by": request.created_by, "executor": request.executor},
        )
        return ConsoleTaskCreateResponse(approval_id=approval.approval_id, status="pending_approval", risk=risk)

    @router.post("/api/v1/console/tasks", response_model=ConsoleTaskCreateResponse)
    def console_task_create(request: ConsoleTaskCreateRequest):
        result = create_console_task(request)
        if result.approval_id:
            from fastapi.responses import JSONResponse
            return JSONResponse(result.model_dump(), status_code=202)
        return result

    @router.get("/approvals", response_class=HTMLResponse, include_in_schema=False)
    def approvals_page() -> HTMLResponse:
        body = "<h1>审批</h1>" + "".join(_approval_card(a) for a in store.list_approval_requests())
        return html_page("审批", body)

    @router.get("/approvals/{approval_id}", response_class=HTMLResponse, include_in_schema=False)
    def approval_detail(approval_id: str) -> HTMLResponse:
        approval = store.load_approval_request(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        return html_page("审批详情", "<h1>审批详情</h1>" + _approval_card(approval))

    def _decision(approval_id: str, decision: str, decided_by: str):
        from .api import ApprovalDecisionRequest  # avoid circular import at module load
        existing = store.load_approval_request(approval_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="approval not found")
        if existing.status != "pending":
            raise HTTPException(status_code=409, detail=f"approval is {existing.status}")
        approval = store.resolve_approval_request(approval_id, status=decision, decided_by=decided_by)
        if decision == "approved" and approval and approval.subject_type == "task" and approval.action == "task.run":
            dispatched = store.dispatch_approved_task_request(approval.approval_id)
            if dispatched is None:
                raise HTTPException(status_code=422, detail="approval cannot be dispatched")
        if decision == "approved" and approval and approval.subject_type == "component_run" and approval.action.startswith("component."):
            store.dispatch_approved_component_action(approval.approval_id)
        return RedirectResponse("/approvals", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/approvals/{approval_id}/approve", include_in_schema=False)
    def approval_approve_form(approval_id: str, decided_by: str = Form("hmn-web")):
        return _decision(approval_id, "approved", decided_by)

    @router.post("/approvals/{approval_id}/reject", include_in_schema=False)
    def approval_reject_form(approval_id: str, decided_by: str = Form("hmn-web")):
        return _decision(approval_id, "rejected", decided_by)

    @router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def docs_index() -> HTMLResponse:
        markdown_files = sorted(docs_base.rglob("*.md")) if docs_base.exists() else []
        links = "".join(
            f'<li><a href="/docs/file/{escape(path.relative_to(docs_base).as_posix())}">{escape(path.relative_to(docs_base).as_posix())}</a></li>'
            for path in markdown_files
        )
        return html_page("文档", f"<h1>文档</h1><div class='card'><ul>{links}</ul></div>")

    @router.get("/docs/file/{doc_path:path}", include_in_schema=False)
    def docs_markdown(doc_path: str) -> FileResponse:
        return FileResponse(docs_file(doc_path), media_type="text/markdown; charset=utf-8")

    @router.get("/audit", response_class=HTMLResponse, include_in_schema=False)
    def audit_page() -> HTMLResponse:
        rows = "".join(
            f"<div class='card'><b>{escape(e.action)}</b> <span class='badge'>{escape(e.outcome)}</span><div class='muted'>{escape(e.event_type)} · {escape(e.subject_id)}</div><pre>{escape(json.dumps(redact(e.details), ensure_ascii=False, sort_keys=True))}</pre></div>"
            for e in reversed(store.list_audit_events())
        )
        return html_page("审计", "<h1>审计</h1>" + rows)

    def _component_plan(component_id: str, request: ComponentActionRequest) -> dict[str, Any]:
        return {"component_id": component_id, "node_id": request.node_id, "action": request.action, "dry_run": True, "approval_required": request.action in {"apply", "uninstall"}, "config": redact(request.config)}

    @router.get("/components", response_class=HTMLResponse, include_in_schema=False)
    def components_page() -> HTMLResponse:
        components = store.list_components() or ComponentRegistry.from_builtin().list()
        body = "<h1>组件</h1>" + "".join(f"<div class='card'><b>{escape(c.id)}</b><div>{escape(c.summary)}</div><span class='badge'>{escape(c.risk)}</span></div>" for c in components)
        return html_page("组件", body)

    @router.post("/api/v1/console/components/{component_id}/plan")
    def component_plan(component_id: str, request: ComponentActionRequest) -> dict[str, Any]:
        return _component_plan(component_id, request)

    @router.post("/api/v1/console/components/{component_id}/run")
    def component_run(component_id: str, request: ComponentActionRequest):
        plan = _component_plan(component_id, request)
        risk = "high" if plan["approval_required"] else "low"
        run = store.record_component_run(component_id=component_id, node_id=request.node_id, action=request.action, risk=risk, status="pending_approval" if risk != "low" else "planned", plan=plan, created_by=request.created_by)
        if risk == "low":
            return {"run_id": run.run_id, "status": run.status, "approval_id": None}
        approval = store.create_approval_request(subject_type="component_run", subject_id=run.run_id, action=f"component.{request.action}", risk=risk, requested_by=request.created_by, details={"run_id": run.run_id, "component_id": component_id, "node_id": request.node_id, "action": request.action, "risk": risk, "config": request.config})
        from fastapi.responses import JSONResponse
        return JSONResponse({"run_id": run.run_id, "status": "pending_approval", "approval_id": approval.approval_id}, status_code=202)

    @router.get("/network", response_class=HTMLResponse, include_in_schema=False)
    def network_page() -> HTMLResponse:
        return html_page("网络", "<h1>网络 / ACL</h1><div class='card'>ACL 计划、diff 和审批入口</div>")

    @router.post("/api/v1/console/network/acl/plan")
    def network_acl_plan(request: NetworkAclPlanRequest) -> dict[str, Any]:
        return {"approval_required": True, "risk": "critical", "diff": request.proposed_acl, "dry_run": True}

    @router.get("/backups", response_class=HTMLResponse, include_in_schema=False)
    def backups_page() -> HTMLResponse:
        return html_page("备份", "<h1>备份 / 恢复</h1><div class='card'>备份 dry-run、恢复审批、恢复点追踪</div>")

    @router.post("/api/v1/console/backups/plan")
    def backup_plan(request: BackupPlanRequest) -> dict[str, Any]:
        return {"node_id": request.node_id, "target": request.target, "dry_run": True, "approval_required": False, "steps": ["scan", "archive", "checksum"]}

    @router.post("/api/v1/console/restore/run")
    def restore_run(request: RestoreRunRequest):
        approval = store.create_approval_request(subject_type="restore", subject_id=request.backup_id, action="restore.run", risk="critical", requested_by=request.created_by, details={"node_id": request.node_id, "backup_id": request.backup_id})
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "pending_approval", "approval_id": approval.approval_id}, status_code=202)

    app.include_router(router)
