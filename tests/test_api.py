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


def test_control_plane_serves_join_script(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/join.sh")

    assert response.status_code == 200
    assert "HERMES_JOIN_TOKEN" in response.text
    assert response.headers["content-type"].startswith("text/x-shellscript")


def test_control_plane_serves_worker_script(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/worker.sh")

    assert response.status_code == 200
    assert "HMN_ENABLE_EXEC" in response.text
    assert response.headers["content-type"].startswith("text/x-shellscript")



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


def test_task_lifecycle_assigns_next_task_and_records_result(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_task",
            fingerprint="sha256:task",
            hostname="task-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    task = store.create_task(node_id="node_task", command="uptime", risk="low", created_by="test")
    client = TestClient(create_app(db))

    next_response = client.post(
        "/api/v1/nodes/node_task/tasks/next",
        json={"fingerprint": "sha256:task"},
    )

    assert next_response.status_code == 200
    assert next_response.json()["task_id"] == task.task_id
    assert next_response.json()["command"] == "uptime"
    assert store.load_task(task.task_id).status == "running"

    result_response = client.post(
        f"/api/v1/tasks/{task.task_id}/result",
        json={"fingerprint": "sha256:task", "exit_code": 0, "stdout": "ok", "stderr": ""},
    )

    assert result_response.status_code == 200
    completed = store.load_task(task.task_id)
    assert completed.status == "succeeded"
    assert completed.exit_code == 0
    assert completed.stdout == "ok"
    assert store.list_audit_events()[-1].action == "task_result"


def test_task_next_returns_no_task_when_queue_empty(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_empty",
            fingerprint="sha256:empty",
            hostname="empty-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_empty/tasks/next",
        json={"fingerprint": "sha256:empty"},
    )

    assert response.status_code == 200
    assert response.json() == {"task": None}


def test_join_endpoint_rejects_reused_token(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="C", labels=[])
    store.save_token(token)
    client = TestClient(create_app(db))
    payload = {"token": token.value, "fingerprint": "sha256:abc", "hostname": "demo", "addresses": []}

    assert client.post("/api/v1/join", json=payload).status_code == 200
    assert client.post("/api/v1/join", json=payload).status_code == 409
