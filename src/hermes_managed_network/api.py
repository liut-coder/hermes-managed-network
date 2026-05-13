from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import hermes_managed_network
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any

from .signing import sign_task_payload
from .storage import Notification, SQLiteStore
from .approval_telegram_flow import handle_telegram_approval_callback
from .network_acl import dispatch_approved_network_acl_apply
from .network_base import NetworkProviderError
from .version import current_version_info, is_worker_compatible

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()


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
    domains: list[str]
    ports: list[int]
    status: str
    monitor_enabled: bool
    docs_path: str
    source: str


class ConsoleServicesResponse(BaseModel):
    services: list[ConsoleServiceResponse]


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
        os=str(facts.get("os") or facts.get("platform") or "unknown"),
        uptime=str(facts.get("uptime") or "-"),
        cpu=_fact_number(facts, "cpu_percent", "cpu"),
        memory=_fact_number(facts, "memory_percent", "memory"),
        disk=_fact_number(facts, "disk_percent", "disk"),
        load=_fact_number(facts, "load_average", "load"),
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


def create_app(db_path: str | Path = DEFAULT_DB) -> FastAPI:
    app = FastAPI(title="Hermes Managed Network", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = SQLiteStore(db_path)

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
    def console_summary() -> ConsoleSummaryResponse:
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
                )
                for approval in approvals
            ],
        )

    def _asset_script(name: str) -> Response:
        script_path = Path(hermes_managed_network.__file__).resolve().parent / "assets" / name
        if not script_path.exists():
            raise HTTPException(status_code=404, detail=f"{name} not found")
        return Response(script_path.read_text(), media_type="text/x-shellscript")

    @app.get("/api/v1/console/services", response_model=ConsoleServicesResponse)
    def console_services() -> ConsoleServicesResponse:
        return ConsoleServicesResponse(
            services=[
                ConsoleServiceResponse(
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
                )
                for service in store.list_service_records()
            ]
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
