from fastapi.testclient import TestClient

from hermes_managed_network.api import create_app
from hermes_managed_network.storage import SQLiteStore
from hermes_managed_network.tokens import JoinTokenStore


def test_join_endpoint_consumes_token_and_registers_pending_node(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="B", labels=["managed", "region:hk"])
    store.save_token(token)
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/join",
        json={
            "token": token.value,
            "fingerprint": "sha256:abc",
            "hostname": "demo",
            "addresses": ["100.64.0.10"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["trust_level"] == "B"
    assert data["labels"] == ["managed", "region:hk"]

    persisted_token = store.load_token(token.value)
    assert persisted_token.status == "used"
    assert persisted_token.node_fingerprint == "sha256:abc"
    assert persisted_token.used_at is not None
    node = store.load_node(data["node_id"])
    assert node.hostname == "demo"
    assert node.fingerprint == "sha256:abc"


def test_join_script_endpoint_serves_node_bootstrap_script(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/join.sh")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-shellscript")
    assert "HERMES_JOIN_TOKEN is required" in response.text
    assert "/api/v1/join" in response.text


def test_join_endpoint_rejects_reused_token(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="C", labels=[])
    store.save_token(token)
    client = TestClient(create_app(db))
    payload = {"token": token.value, "fingerprint": "sha256:abc", "hostname": "demo", "addresses": []}

    assert client.post("/api/v1/join", json=payload).status_code == 200
    assert client.post("/api/v1/join", json=payload).status_code == 409
