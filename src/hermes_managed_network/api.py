from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import hermes_managed_network
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Any

from .storage import SQLiteStore
from .version import current_version_info, is_worker_compatible

DEFAULT_DB = Path("~/.hmn/control-plane.db").expanduser()


class JoinRequest(BaseModel):
    token: str
    fingerprint: str
    hostname: str
    addresses: list[str] = Field(default_factory=list)


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


class NodeAuthRequest(BaseModel):
    fingerprint: str
    worker_protocol_version: str | None = None


class TaskResponse(BaseModel):
    task_id: str
    command: str
    risk: str


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


def create_app(db_path: str | Path = DEFAULT_DB) -> FastAPI:
    app = FastAPI(title="Hermes Managed Network", version="0.2.0")
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

    def _asset_script(name: str) -> Response:
        script_path = Path(hermes_managed_network.__file__).resolve().parent / "assets" / name
        if not script_path.exists():
            raise HTTPException(status_code=404, detail=f"{name} not found")
        return Response(script_path.read_text(), media_type="text/x-shellscript")

    @app.get("/scripts/join.sh", include_in_schema=False)
    def join_script() -> Response:
        return _asset_script("join.sh")

    @app.get("/scripts/worker.sh", include_in_schema=False)
    def worker_script() -> Response:
        return _asset_script("worker.sh")

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
        if node.fingerprint != request.fingerprint:
            raise HTTPException(status_code=403, detail="node fingerprint mismatch")
        if not is_worker_compatible(current_version_info().worker_protocol_version, request.worker_protocol_version):
            raise HTTPException(status_code=426, detail="worker protocol version mismatch; update node worker")
        task = store.next_pending_task(node_id)
        if task is None:
            return NoTaskResponse(task=None)
        return TaskResponse(task_id=task.task_id, command=task.command, risk=task.risk)

    @app.post("/api/v1/tasks/{task_id}/result", response_model=TaskResultResponse)
    def task_result(task_id: str, request: TaskResultRequest) -> TaskResultResponse:
        task = store.load_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        node = store.load_node(task.node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="node not found")
        if node.fingerprint != request.fingerprint:
            raise HTTPException(status_code=403, detail="node fingerprint mismatch")
        updated = store.complete_task(task_id, exit_code=request.exit_code, stdout=request.stdout, stderr=request.stderr)
        return TaskResultResponse(task_id=updated.task_id, status=updated.status)

    return app


app = create_app()
