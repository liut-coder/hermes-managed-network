from fastapi.testclient import TestClient


def test_hmn_web_docs_module_serves_docs_index_and_markdown(tmp_path):
    docs_root = tmp_path / "files"
    server_dir = docs_root / "docs" / "server"
    service_dir = docs_root / "service"
    server_dir.mkdir(parents=True)
    service_dir.mkdir(parents=True)
    (server_dir / "README.md").write_text("# Servers\n\n- demo\n", encoding="utf-8")
    (service_dir / "demo.md").write_text("# Demo Service\n", encoding="utf-8")

    client = TestClient(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    index_response = client.get("/hmn-web/docs")
    assert index_response.status_code == 200
    assert "HMN 文档中心" in index_response.text
    assert "/hmn-web/docs/file/docs/server/README.md" in index_response.text
    assert "/hmn-web/docs/file/service/demo.md" in index_response.text

    markdown_response = client.get("/hmn-web/docs/file/docs/server/README.md")
    assert markdown_response.status_code == 200
    assert markdown_response.headers["content-type"].startswith("text/markdown")
    assert markdown_response.text == "# Servers\n\n- demo\n"


def test_hmn_web_docs_module_rejects_path_traversal(tmp_path):
    docs_root = tmp_path / "files"
    docs_root.mkdir()
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    response = client.get("/hmn-web/docs/file/../secret.md")

    assert response.status_code in {404, 400}


from hermes_managed_network.api import create_app
from hermes_managed_network.storage import SQLiteStore, ServiceRecord
from hermes_managed_network.tokens import JoinTokenStore
from hermes_managed_network.version import current_version_info


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
    assert data["status"] == "managed"
    assert data["trust_level"] == "B"
    assert data["labels"] == ["managed", "region:hk"]

    persisted_token = store.load_token(token.value)
    assert persisted_token.status == "used"
    assert persisted_token.node_fingerprint == "sha256:abc"
    assert persisted_token.used_at is not None
    node = store.load_node(data["node_id"])
    assert node.hostname == "demo"
    assert node.fingerprint == "sha256:abc"
    assert node.status == "managed"
    assert node.permission_bundles == ["observe", "task"]


def test_join_endpoint_can_keep_legacy_pending_confirmation_when_requested(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="B", labels=["managed"])
    store.save_token(token)
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/join",
        json={
            "token": token.value,
            "fingerprint": "sha256:legacy",
            "hostname": "legacy-node",
            "addresses": [],
            "auto_confirm": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    node = store.load_node(data["node_id"])
    assert node.status == "pending"
    assert node.permission_bundles == []


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
            "auto_confirm": True,
            "permission_bundles": ["observe", "task"],
        }
        for event in events
    )


def test_console_summary_endpoint_returns_nodes_tasks_and_approvals(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_console",
            fingerprint="sha256:console",
            hostname="console-node",
            addresses=["100.64.0.11"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe", "task"],
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_console",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "os": "Debian",
                "uptime": "3h",
                "cpu_percent": 12,
                "memory_percent": 34,
                "disk_percent": 56,
                "load_average": 0.42,
                "exec_enabled": True,
            },
        },
    )
    task = store.create_task(node_id="node_console", command="uptime", risk="low", created_by="test")
    approval = store.create_approval_request(
        subject_type="task",
        subject_id=task.task_id,
        action="task.run",
        risk="high",
        requested_by="test",
        details={"node_id": "node_console", "command": "reboot"},
    )
    client = TestClient(create_app(db))

    response = client.get("/api/v1/console/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"] == {
        "online_nodes": 1,
        "total_nodes": 1,
        "managed_nodes": 1,
        "pending_nodes": 0,
        "pending_approvals": 1,
        "running_tasks": 0,
    }
    assert data["nodes"] == [
        {
            "id": "node_console",
            "name": "console-node",
            "status": "managed",
            "live": "online",
            "trust": "B",
            "role": "worker",
            "ip": "100.64.0.11",
            "os": "Debian",
            "uptime": "3h",
            "cpu": 12,
            "memory": 34,
            "disk": 56,
            "load": 0.42,
            "hb": "刚刚",
            "exec": True,
        }
    ]
    assert data["tasks"][0]["id"] == task.task_id
    assert data["tasks"][0]["status"] == "pending"
    assert data["approvals"][0]["id"] == approval.approval_id
    assert data["approvals"][0]["status"] == "pending"


def test_console_summary_maps_nested_worker_heartbeat_facts(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_nested_facts",
            fingerprint="sha256:nested",
            hostname="nested-node",
            addresses=["100.64.0.12"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_nested_facts",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "capabilities": {"os_family": "linux"},
                "uptime": {"seconds": 93780},
                "load_average": {"1m": "0.12", "5m": "0.08", "15m": "0.05"},
                "memory": {"total_kb": 1000, "available_kb": 400, "free_kb": 200},
                "disk": {"path": "/", "total_bytes": 1000, "used_bytes": 420, "free_bytes": 580},
                "exec_enabled": False,
            },
        },
    )
    client = TestClient(create_app(db))

    response = client.get("/api/v1/console/summary")

    assert response.status_code == 200
    node = response.json()["nodes"][0]
    assert node["os"] == "linux"
    assert node["uptime"] == "1d 2h"
    assert node["memory"] == 60
    assert node["disk"] == 42
    assert node["load"] == 0.12
    assert node["exec"] is False


def test_console_services_endpoint_returns_db_service_record_summaries(tmp_path):
    db = tmp_path / "hmn.db"
    SQLiteStore(db).save_service_record(
        ServiceRecord(
            service_id="node-api:docker:web",
            name="API Web",
            node_id="node-api",
            kind="docker",
            runtime="nginx",
            domains=["api.example.com"],
            ports=[443],
            monitor_enabled=True,
            docs_path="service/api-web.md",
            source="discovery",
            status="active",
        )
    )
    client = TestClient(create_app(db))

    response = client.get("/api/v1/console/services")

    assert response.status_code == 200
    assert response.json() == {
        "services": [
            {
                "service_id": "node-api:docker:web",
                "name": "API Web",
                "node_id": "node-api",
                "kind": "docker",
                "domains": ["api.example.com"],
                "ports": [443],
                "status": "active",
                "monitor_enabled": True,
                "docs_path": "service/api-web.md",
                "source": "discovery",
            }
        ]
    }


def test_control_plane_serves_join_script(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/join.sh")

    assert response.status_code == 200
    assert "HERMES_JOIN_TOKEN" in response.text
    assert "HERMES_AUTO_CONFIRM=\"${HERMES_AUTO_CONFIRM:-1}\"" in response.text
    assert "HERMES_AUTO_INSTALL_WORKER=\"${HERMES_AUTO_INSTALL_WORKER:-1}\"" in response.text
    assert "HMN_ENABLE_EXEC=\"${HMN_ENABLE_EXEC:-1}\"" in response.text
    assert "install_worker" in response.text
    assert "systemctl enable --now hermes-managed-network-heartbeat.timer" in response.text
    assert response.headers["content-type"].startswith("text/x-shellscript")


def test_control_plane_serves_worker_script(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/worker.sh")

    assert response.status_code == 200
    assert "HMN_ENABLE_EXEC" in response.text
    assert response.headers["content-type"].startswith("text/x-shellscript")


def test_control_plane_serves_worker_lite_script(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/worker-lite.sh")

    assert response.status_code == 200
    assert response.text.startswith("#!/bin/sh")
    assert "task_policy\":\"heartbeat-only" in response.text
    assert response.headers["content-type"].startswith("text/x-shellscript")


def test_control_plane_version_endpoint_reports_protocol_versions(tmp_path):
    client = TestClient(create_app(tmp_path / "hmn.db"))

    response = client.get("/api/v1/version")

    assert response.status_code == 200
    data = response.json()
    assert data["api_version"] == current_version_info().api_version
    assert data["worker_protocol_version"] == current_version_info().worker_protocol_version
    assert "package_version" in data


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
    assert response.json()["worker_compatible"] is True
    events = store.list_audit_events()
    assert events[-1].action == "heartbeat"
    assert events[-1].subject_id == "node_hb"
    assert events[-1].outcome == "ok"
    assert events[-1].details["facts"] == {"uptime": "1 day"}
    assert events[-1].details["worker_compatible"] is True


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
        json={"fingerprint": "sha256:task", "worker_protocol_version": current_version_info().worker_protocol_version},
    )

    assert next_response.status_code == 200
    assert next_response.json()["task_id"] == task.task_id
    assert next_response.json()["command"] == "uptime"
    assert next_response.json()["signature"].startswith("hmac-sha256:")
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


def test_task_next_expires_stuck_before_returning_pending_task(tmp_path):
    from hermes_managed_network.inventory import Node
    from datetime import datetime, timezone

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_watchdog_api",
            fingerprint="sha256:watchdog-api",
            hostname="watchdog-api",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    stuck = store.create_task(node_id="node_watchdog_api", command="stuck")
    store.claim_next_task("node_watchdog_api", lease_seconds=1)
    pending = store.create_task(node_id="node_watchdog_api", command="pending")
    with store.connect() as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
            (datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat(), stuck.task_id),
        )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_watchdog_api/tasks/next",
        json={"fingerprint": "sha256:watchdog-api", "worker_protocol_version": current_version_info().worker_protocol_version},
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == pending.task_id
    assert store.load_task(stuck.task_id).status == "failed"
    assert store.load_task(stuck.task_id).failure_reason == "worker_lease_expired"
    assert store.load_task(pending.task_id).status == "running"


def test_late_task_result_is_rejected_after_watchdog_failure(tmp_path):
    from hermes_managed_network.inventory import Node
    from datetime import datetime, timedelta, timezone

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_late_result",
            fingerprint="sha256:late-result",
            hostname="late-result",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    task = store.create_task(node_id="node_late_result", command="slow")
    store.claim_next_task("node_late_result", lease_seconds=1)
    store.expire_stuck_tasks(now=datetime.now(timezone.utc) + timedelta(seconds=2))
    client = TestClient(create_app(db))

    response = client.post(
        f"/api/v1/tasks/{task.task_id}/result",
        json={"fingerprint": "sha256:late-result", "exit_code": 0, "stdout": "late", "stderr": ""},
    )

    assert response.status_code == 409
    assert store.load_task(task.task_id).status == "failed"
    assert store.load_task(task.task_id).failure_reason == "worker_lease_expired"
    assert store.load_task(task.task_id).stdout == ""


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
        json={"fingerprint": "sha256:empty", "worker_protocol_version": current_version_info().worker_protocol_version},
    )

    assert response.status_code == 200
    assert response.json() == {"task": None}


def test_task_next_ignores_pending_ssh_tasks(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_ssh_only",
            fingerprint="sha256:ssh-only",
            hostname="ssh-only-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    store.create_task(node_id="node_ssh_only", command="uptime", risk="low", created_by="test", executor="ssh")
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_ssh_only/tasks/next",
        json={"fingerprint": "sha256:ssh-only", "worker_protocol_version": current_version_info().worker_protocol_version},
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


def test_task_next_rejects_incompatible_worker_protocol(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_old_worker",
            fingerprint="sha256:old-worker",
            hostname="old-worker-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    store.create_task(node_id="node_old_worker", command="uptime", risk="low", created_by="test")
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_old_worker/tasks/next",
        json={"fingerprint": "sha256:old-worker", "worker_protocol_version": "99.0"},
    )

    assert response.status_code == 426


def test_node_rotate_fingerprint_endpoint_updates_auth_and_records_audit(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_rotate",
            fingerprint="sha256:old",
            hostname="rotate-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_rotate/rotate-fingerprint",
        json={"fingerprint": "sha256:old", "new_fingerprint": "sha256:new"},
    )

    assert response.status_code == 200
    assert response.json() == {"node_id": "node_rotate", "status": "rotated"}
    assert store.load_node("node_rotate").fingerprint == "sha256:new"
    assert client.post(
        "/api/v1/nodes/node_rotate/heartbeat",
        json={"fingerprint": "sha256:old", "status": "ok", "facts": {}},
    ).status_code == 403
    assert client.post(
        "/api/v1/nodes/node_rotate/heartbeat",
        json={"fingerprint": "sha256:new", "status": "ok", "facts": {}},
    ).status_code == 200
    rotate_event = [event for event in store.list_audit_events() if event.action == "rotate_fingerprint"][-1]
    assert rotate_event.outcome == "ok"
    assert rotate_event.details == {"old_fingerprint_sha256": "sha256:old", "new_fingerprint_sha256": "sha256:new"}


def test_node_rotate_fingerprint_rejects_wrong_current_fingerprint(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_rotate_reject",
            fingerprint="sha256:right",
            hostname="rotate-reject-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    client = TestClient(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_rotate_reject/rotate-fingerprint",
        json={"fingerprint": "sha256:wrong", "new_fingerprint": "sha256:new"},
    )

    assert response.status_code == 403
    assert store.load_node("node_rotate_reject").fingerprint == "sha256:right"
