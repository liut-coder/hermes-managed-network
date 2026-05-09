from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .inventory import NodeRegistry
from .storage import SQLiteStore
from .tokens import JoinTokenStore

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

    @app.post("/api/v1/join", response_model=JoinResponse)
    def join(request: JoinRequest) -> JoinResponse:
        token = store.load_token(request.token)
        if token is None:
            raise HTTPException(status_code=404, detail="join token not found")

        token_store = JoinTokenStore()
        token_store._tokens[token.value] = token  # local lifecycle reuse for the MVP
        consumed = token_store.consume(token.value, node_fingerprint=request.fingerprint)
        if consumed is None:
            store.save_token(token)
            raise HTTPException(status_code=409, detail=f"join token is {token.status}")

        store.save_token(consumed)
        registry = NodeRegistry()
        node = registry.register_pending(
            node_id="node_" + uuid4().hex[:12],
            fingerprint=request.fingerprint,
            hostname=request.hostname,
            addresses=request.addresses,
            trust_level=consumed.trust_level,
            labels=consumed.labels,
        )
        store.save_node(node)
        return JoinResponse(
            node_id=node.node_id,
            status=node.status,
            trust_level=node.trust_level,
            labels=node.labels,
        )

    return app


app = create_app()
