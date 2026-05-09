from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from .storage import SQLiteStore

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


def create_app(db_path: str | Path = DEFAULT_DB) -> FastAPI:
    app = FastAPI(title="Hermes Managed Network", version="0.2.0")
    store = SQLiteStore(db_path)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/scripts/join.sh", include_in_schema=False)
    def join_script() -> Response:
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "join.sh"
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="join script not found")
        return Response(script_path.read_text(), media_type="text/x-shellscript")

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
        return JoinResponse(
            node_id=node.node_id,
            status=node.status,
            trust_level=node.trust_level,
            labels=node.labels,
        )

    return app


app = create_app()
