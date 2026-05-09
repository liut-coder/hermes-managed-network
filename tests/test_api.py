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


def test_join_endpoint_records_node_join_audit_event(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="B", labels=["backup", "worker"])
    store.save_token(token)
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/join",
        json={
            "token": token.value,
            "fingerprint": "sha256:join-audit",
            "hostname": "joined-node",
            "addresses": ["10.0.0.8"],
        },
    )

    assert response.status_code == 200
    node_id = response.json()["node_id"]
    events = store.list_audit_events()
    assert any(
        event.event_type == "node"
        and event.subject_type == "node"
        and event.subject_id == node_id
        and event.action == "join"
        and event.outcome == "ok"
        and event.details == {
            "hostname": "joined-node",
            "addresses": ["10.0.0.8"],
            "trust_level": "B",
            "labels": ["backup", "worker"],
        }
        for event in events
    )


def test_join_script_endpoint_serves_node_bootstrap_script(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/join.sh")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-shellscript")
    assert "HERMES_JOIN_TOKEN is required" in response.text
    assert "/api/v1/join" in response.text


def test_node_heartbeat_endpoint_updates_status_and_records_audit(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_hb",
            fingerprint="sha256:hb",
            hostname="heartbeat-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_hb/heartbeat",
        json={"fingerprint": "sha256:hb", "status": "ok", "facts": {"uptime": "1 day"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    events = store.list_audit_events()
    assert events[-1].action == "heartbeat"
    assert events[-1].subject_id == "node_hb"
    assert events[-1].outcome == "ok"
    assert events[-1].details["facts"] == {"uptime": "1 day"}


def test_node_heartbeat_rejects_wrong_fingerprint(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_hb",
            fingerprint="sha256:right",
            hostname="heartbeat-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_hb/heartbeat",
        json={"fingerprint": "sha256:wrong", "status": "ok", "facts": {}},
    )

    assert response.status_code == 403


def test_join_endpoint_rejects_reused_token(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="C", labels=[])
    store.save_token(token)
    client = TestClient(create_app(db))
    payload = {"token": token.value, "fingerprint": "sha256:abc", "hostname": "demo", "addresses": []}

    assert client.post("/api/v1/join", json=payload).status_code == 200
    assert client.post("/api/v1/join", json=payload).status_code == 409
