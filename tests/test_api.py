import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from hermes_managed_network.api import SESSION_COOKIE_NAME
from hermes_managed_network.signing import verify_task_signature


TEST_WEB_SECRET = "test-web-secret"
os.environ.setdefault("HMN_WEB_SESSION_SECRET", TEST_WEB_SECRET)


def _auth_client(app):
    client = TestClient(app, base_url="https://testserver")
    issued_at = int(datetime.now(timezone.utc).timestamp())
    payload = f"admin:{issued_at}"
    signature = hmac.new(TEST_WEB_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    cookie = base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii")
    client.cookies.set(SESSION_COOKIE_NAME, cookie, path="/")
    return client


def test_hmn_web_docs_module_serves_docs_index_and_markdown(tmp_path):
    docs_root = tmp_path / "files"
    server_dir = docs_root / "docs" / "server"
    service_dir = docs_root / "service"
    server_dir.mkdir(parents=True)
    service_dir.mkdir(parents=True)
    (server_dir / "README.md").write_text("# Servers\n\n- demo\n", encoding="utf-8")
    (service_dir / "demo.md").write_text("# Demo Service\n", encoding="utf-8")

    client = _auth_client(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    index_response = client.get("/hmn-web/docs")
    assert index_response.status_code == 200
    assert "文件索引" in index_response.text
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
    client = _auth_client(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    response = client.get("/hmn-web/docs/file/../secret.md")

    assert response.status_code in {404, 400}


def test_hmn_web_docs_index_api_lists_server_and_service_docs(tmp_path):
    docs_root = tmp_path / "files"
    (docs_root / "docs" / "server" / "demo").mkdir(parents=True)
    (docs_root / "docs" / "server" / "demo" / "README.md").write_text("# Demo Node", encoding="utf-8")
    (docs_root / "service").mkdir(parents=True)
    (docs_root / "service" / "api.md").write_text("# API", encoding="utf-8")

    client = _auth_client(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    response = client.get("/api/v1/hmn-web/docs/index")

    assert response.status_code == 200
    data = response.json()
    assert data["server_docs"] == [
        {
            "title": "Demo Node",
            "path": "docs/server/demo/README.md",
            "url": "/hmn-web/docs/file/docs/server/demo/README.md",
            "viewer_url": "/hmn-web/docs/view/docs/server/demo/README.md",
            "category": "server",
            "summary": "",
        }
    ]
    assert data["service_docs"] == [
        {
            "title": "API",
            "path": "service/api.md",
            "url": "/hmn-web/docs/file/service/api.md",
            "viewer_url": "/hmn-web/docs/view/service/api.md",
            "category": "service",
            "summary": "",
        }
    ]


def test_hmn_web_docs_view_page_wraps_markdown(tmp_path):
    docs_root = tmp_path / "files"
    (docs_root / "service").mkdir(parents=True)
    (docs_root / "service" / "demo.md").write_text("# Demo\n\nhello", encoding="utf-8")
    client = _auth_client(create_app(tmp_path / "hmn.db", docs_root=docs_root))

    response = client.get("/hmn-web/docs/view/service/demo.md")

    assert response.status_code == 200
    assert "HMN 文档" in response.text
    assert "<h1>Demo</h1>" in response.text
    assert "<p>hello</p>" in response.text


from hermes_managed_network.api import create_app
from hermes_managed_network.storage import SQLiteStore, ServiceRecord
from hermes_managed_network.tokens import JoinTokenStore
from hermes_managed_network.version import current_version_info


def test_join_endpoint_consumes_token_and_registers_pending_node(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="B", labels=["managed", "region:hk"])
    store.save_token(token)
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

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


def test_join_endpoint_rejects_duplicate_fingerprint_without_consuming_token(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_existing",
            fingerprint="sha256:same-machine",
            hostname="existing-node",
            addresses=["10.0.0.10"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe", "task"],
        )
    )
    token = JoinTokenStore().create(trust_level="B", labels=["worker"])
    store.save_token(token)
    client = _auth_client(create_app(db))

    response = client.post(
        "/api/v1/join",
        json={
            "token": token.value,
            "fingerprint": "sha256:same-machine",
            "hostname": "duplicate-node",
            "addresses": ["10.0.0.11"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "node already exists: node_existing"
    assert store.load_token(token.value).status == "pending"
    assert len(store.list_nodes()) == 1


def test_join_endpoint_rejects_duplicate_hostname_and_address_without_consuming_token(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_existing_host_ip",
            fingerprint="sha256:existing",
            hostname="same-host",
            addresses=["100.64.0.10", "10.0.0.10"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe", "task"],
        )
    )
    token = JoinTokenStore().create(trust_level="B", labels=["worker"])
    store.save_token(token)
    client = _auth_client(create_app(db))

    response = client.post(
        "/api/v1/join",
        json={
            "token": token.value,
            "fingerprint": "sha256:new-fingerprint",
            "hostname": "same-host",
            "addresses": ["10.0.0.10"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "node already exists: node_existing_host_ip"
    assert store.load_token(token.value).status == "pending"
    assert len(store.list_nodes()) == 1




def test_join_endpoint_allows_rejoin_when_duplicate_match_is_revoked(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_revoked",
            fingerprint="sha256:revoked-machine",
            hostname="retired-host",
            addresses=["10.0.0.20"],
            trust_level="B",
            labels=["worker"],
            status="revoked",
            permission_bundles=["observe"],
        )
    )
    token = JoinTokenStore().create(trust_level="B", labels=["worker"])
    store.save_token(token)
    client = _auth_client(create_app(db))

    response = client.post(
        "/api/v1/join",
        json={
            "token": token.value,
            "fingerprint": "sha256:revoked-machine",
            "hostname": "retired-host",
            "addresses": ["10.0.0.20"],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "managed"
    assert store.load_token(token.value).status == "used"
    assert len(store.list_nodes()) == 2

def test_console_join_policy_defaults_include_linux_target_os(tmp_path):
    client = _auth_client(create_app(tmp_path / "hmn.db"))

    response = client.get("/api/v1/console/join-policy")

    assert response.status_code == 200
    assert response.json() == {
        "auto_confirm": True,
        "auto_install_worker": True,
        "enable_exec": True,
        "pending_visible": False,
        "permission_bundle": "observe_task",
        "target_os": "linux",
    }


def test_console_join_policy_update_persists_windows_target_os(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    client = _auth_client(create_app(db))

    response = client.put(
        "/api/v1/console/join-policy",
        json={
            "auto_confirm": False,
            "auto_install_worker": False,
            "enable_exec": False,
            "pending_visible": True,
            "permission_bundle": "observe",
            "target_os": "windows",
        },
    )

    assert response.status_code == 200
    assert response.json()["target_os"] == "windows"
    assert store.get_setting("console.join_policy", {})["target_os"] == "windows"


def test_console_join_token_accepts_windows_target_os_and_records_audit(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    client = _auth_client(create_app(db))

    response = client.post(
        "/api/v1/console/join-token",
        json={"trust_level": "B", "labels": ["managed"], "ttl_minutes": 30, "target_os": "windows"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["trust_level"] == "B"
    assert data["labels"] == ["managed"]
    assert data["target_os"] == "windows"
    events = store.list_audit_events()
    assert any(
        event.event_type == "token"
        and event.subject_type == "join_token"
        and event.subject_id == data["token"]
        and event.action == "create"
        and event.outcome == "ok"
        and event.details == {
            "trust_level": "B",
            "labels": ["managed"],
            "ttl_minutes": 30,
            "source": "hmn-web",
            "target_os": "windows",
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
    store.save_node(
        Node(
            node_id="node_waiting_heartbeat",
            fingerprint="sha256:waiting",
            hostname="waiting-heartbeat",
            addresses=["100.64.0.12"],
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
                "capabilities": {
                    "os_family": "linux",
                    "has_sh": True,
                    "has_python3": True,
                    "has_curl": True,
                    "has_systemctl": True,
                    "writable_etc": True,
                },
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
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"] == {
        "online_nodes": 1,
        "total_nodes": 2,
        "managed_nodes": 1,
        "pending_nodes": 1,
        "pending_approvals": 1,
        "running_tasks": 0,
    }
    assert len(data["nodes"]) == 2
    assert data["nodes"][0] == {
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
        "runtime_profile": "full-worker",
        "service_manager": "systemd",
        "worker_mode": "unknown",
        "task_policy": "poll-and-exec",
        "worker_status_hint": "可在主控执行节点级状态检查命令",
        "worker_status_command": "hmn node worker-status node_console",
        "worker_install_hint": None,
        "worker_install_command": None,
        "uninstall_hint": None,
        "uninstall_command": None,
        "node_type_label": "Full worker",
        "windows_beacon_only": False,
    }
    assert data["nodes"][1]["id"] == "node_waiting_heartbeat"
    assert data["nodes"][1]["status"] == "pending"
    assert data["nodes"][1]["hb"] == "无"
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
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/summary")

    assert response.status_code == 200
    node = response.json()["nodes"][0]
    assert node["os"] == "linux"
    assert node["uptime"] == "1d 2h"
    assert node["memory"] == 60
    assert node["disk"] == 42
    assert node["load"] == 0.12
    assert node["exec"] is False


def test_console_summary_exposes_windows_beacon_uninstall_metadata(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_windows_console",
            fingerprint="sha256:windows-console",
            hostname="windows-console-node",
            addresses=["100.64.0.66"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_windows_console",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_protocol_version": "0.1",
                "worker_version": "windows-beacon",
                "worker_mode": "beacon",
                "task_policy": "heartbeat-only",
                "exec_enabled": False,
                "os_release": "Microsoft Windows Server 2022",
                "capabilities": {"os_family": "windows", "has_powershell": True},
            },
        },
    )
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/summary")

    assert response.status_code == 200
    node = response.json()["nodes"][0]
    assert node["runtime_profile"] == "beacon-only"
    assert node["service_manager"] == "windows-task"
    assert node["worker_mode"] == "beacon"
    assert node["task_policy"] == "heartbeat-only"
    assert node["worker_status_command"] == "hmn node worker-status node_windows_console"
    assert "状态检查命令" in node["worker_status_hint"]
    assert node["worker_install_command"] == "hmn node install-heartbeat node_windows_console --service-manager windows-task --runtime beacon-only --beacon-only"
    assert "Worker/心跳安装脚本" in node["worker_install_hint"]
    assert node["node_type_label"] == "Windows beacon-only"
    assert node["windows_beacon_only"] is True
    assert node["uninstall_command"] == "hmn node uninstall-heartbeat node_windows_console --service-manager windows-task"
    assert "节点级卸载命令" in node["uninstall_hint"]


def test_console_summary_exposes_windows_full_worker_metadata(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_windows_full",
            fingerprint="sha256:windows-full",
            hostname="windows-full-node",
            addresses=["100.64.0.77"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_windows_full",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_protocol_version": "0.1",
                "worker_version": "windows-worker",
                "worker_mode": "worker",
                "task_policy": "poll-tasks",
                "can_poll_tasks": True,
                "exec_enabled": False,
                "os_release": "Microsoft Windows Server 2022",
                "capabilities": {"os_family": "windows", "has_powershell": True, "has_curl": True},
            },
        },
    )
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/summary")

    assert response.status_code == 200
    node = response.json()["nodes"][0]
    assert node["runtime_profile"] == "full-worker"
    assert node["service_manager"] == "windows-task"
    assert node["worker_mode"] == "worker"
    assert node["task_policy"] == "poll-tasks"
    assert node["node_type_label"] == "Windows full worker"
    assert node["windows_beacon_only"] is False
    assert node["worker_install_command"] == "hmn node install-heartbeat node_windows_full --service-manager windows-task --runtime full-worker"
    assert "全功能 Worker 安装脚本" in node["worker_install_hint"]
    assert node["uninstall_command"] == "hmn node uninstall-heartbeat node_windows_full --service-manager windows-task"



def test_console_summary_keeps_formal_management_after_later_warn_heartbeat(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_once_ok",
            fingerprint="sha256:once-ok",
            hostname="once-ok-node",
            addresses=["100.64.0.21"],
            trust_level="B",
            labels=["worker"],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_once_ok",
        action="heartbeat",
        outcome="ok",
        details={"status": "ok", "facts": {"worker_protocol_version": "0.1"}, "worker_compatible": True},
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_once_ok",
        action="heartbeat",
        outcome="warn",
        details={"status": "warn", "facts": {}, "worker_compatible": False},
    )
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["managed_nodes"] == 1
    assert data["metrics"]["pending_nodes"] == 0
    assert data["nodes"][0]["status"] == "managed"
    assert data["nodes"][0]["live"] == "stale"


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
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/services")

    assert response.status_code == 200
    data = response.json()
    assert data["services"] == [
        {
            "service_id": "node-api:docker:web",
            "name": "API Web",
            "node_id": "node-api",
            "kind": "docker",
            "runtime": "",
            "domains": ["api.example.com"],
            "ports": [443],
            "status": "active",
            "monitor_enabled": True,
            "docs_path": "service/api-web.md",
            "source": "discovery",
            "business_category": "未分类",
            "asset_category": "main",
            "asset_score": 43,
            "why_asset": [
                "1 domain(s)",
                "1 exposed port(s)",
                "monitoring enabled",
                "discovered source=discovery",
            ],
            "summary": "API Web · api.example.com · ports 443",
            "deployment_type": "",
            "project_name": "",
            "business_name": "",
            "business_purpose": "",
            "public_exposed": False,
            "backup_status": "unknown",
            "tags": [],
        }
    ]
    assert data["business_groups"] == [
        {
            "category": "未分类",
            "count": 1,
            "services": data["services"],
        }
    ]
    assert data["pending_discoveries"] == []
    assert data["system_assets"] == []
    assert data["sheet"]["service_id"] == "node-api:docker:web"
    assert data["create_dialog"]["title"] == "新增服务资产"


def test_control_plane_serves_join_script(tmp_path):
    client = _auth_client(create_app(tmp_path / "hmn.db"))

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
    client = _auth_client(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/worker.sh")

    assert response.status_code == 200
    assert "HMN_ENABLE_EXEC" in response.text
    assert response.headers["content-type"].startswith("text/x-shellscript")


def test_control_plane_serves_worker_lite_script(tmp_path):
    client = _auth_client(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/worker-lite.sh")

    assert response.status_code == 200
    assert response.text.startswith("#!/bin/sh")
    assert "task_policy\":\"heartbeat-only" in response.text
    assert response.headers["content-type"].startswith("text/x-shellscript")


def test_control_plane_serves_worker_windows_script(tmp_path):
    client = _auth_client(create_app(tmp_path / "hmn.db"))

    response = client.get("/scripts/worker-windows.ps1")

    assert response.status_code == 200
    assert "$ErrorActionPreference = 'Stop'" in response.text
    assert "/api/v1/nodes/" in response.text
    assert "Get-NetIPAddress" in response.text
    assert response.headers["content-type"].startswith("text/plain")


def test_control_plane_version_endpoint_reports_protocol_versions(tmp_path):
    client = _auth_client(create_app(tmp_path / "hmn.db"))

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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

    next_response = client.post(
        "/api/v1/nodes/node_task/tasks/next",
        json={"fingerprint": "sha256:task", "worker_protocol_version": current_version_info().worker_protocol_version},
    )

    assert next_response.status_code == 200
    task_payload = next_response.json()
    assert task_payload["task_id"] == task.task_id
    assert task_payload["command"] == "uptime"
    assert task_payload["signature"].startswith("hmac-sha256:")
    assert verify_task_signature(
        node_fingerprint="sha256:task",
        task_id=task_payload["task_id"],
        command=task_payload["command"],
        risk=task_payload["risk"],
        signature=task_payload["signature"],
    )
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


def test_windows_worker_flow_rejects_bad_signature_reports_exec_disabled_and_rotates_fingerprint(tmp_path):
    from hermes_managed_network.inventory import Node

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_windows",
            fingerprint="sha256:old",
            hostname="win-node",
            addresses=[],
            trust_level="B",
            labels=["os:windows"],
            status="managed",
            permission_bundles=["observe"],
        )
    )
    client = _auth_client(create_app(db))
    worker_protocol = current_version_info().worker_protocol_version

    bad_task = store.create_task(node_id="node_windows", command="Write-Output 'hello'", risk="low", created_by="test")
    bad_next = client.post(
        "/api/v1/nodes/node_windows/tasks/next",
        json={"fingerprint": "sha256:old", "worker_protocol_version": worker_protocol},
    )
    assert bad_next.status_code == 200
    bad_payload = bad_next.json()
    assert bad_payload["task_id"] == bad_task.task_id
    assert not verify_task_signature(
        node_fingerprint="sha256:old",
        task_id=bad_payload["task_id"],
        command=bad_payload["command"],
        risk=bad_payload["risk"],
        signature=bad_payload["signature"] + "tampered",
    )
    bad_result = client.post(
        f"/api/v1/tasks/{bad_task.task_id}/result",
        json={"fingerprint": "sha256:old", "exit_code": 127, "stdout": "", "stderr": "task signature mismatch"},
    )
    assert bad_result.status_code == 200
    failed_task = store.load_task(bad_task.task_id)
    assert failed_task.status == "failed"
    assert failed_task.exit_code == 127
    assert failed_task.stderr == "task signature mismatch"
    assert failed_task.failure_reason == "exit_code_nonzero"

    disabled_task = store.create_task(node_id="node_windows", command="Get-Date", risk="low", created_by="test")
    disabled_next = client.post(
        "/api/v1/nodes/node_windows/tasks/next",
        json={"fingerprint": "sha256:old", "worker_protocol_version": worker_protocol},
    )
    assert disabled_next.status_code == 200
    disabled_payload = disabled_next.json()
    assert disabled_payload["task_id"] == disabled_task.task_id
    assert verify_task_signature(
        node_fingerprint="sha256:old",
        task_id=disabled_payload["task_id"],
        command=disabled_payload["command"],
        risk=disabled_payload["risk"],
        signature=disabled_payload["signature"],
    )
    disabled_result = client.post(
        f"/api/v1/tasks/{disabled_task.task_id}/result",
        json={
            "fingerprint": "sha256:old",
            "exit_code": 126,
            "stdout": "",
            "stderr": "execution disabled; set HMN_ENABLE_EXEC=1",
        },
    )
    assert disabled_result.status_code == 200
    blocked_task = store.load_task(disabled_task.task_id)
    assert blocked_task.status == "failed"
    assert blocked_task.exit_code == 126
    assert blocked_task.stderr == "execution disabled; set HMN_ENABLE_EXEC=1"
    assert blocked_task.failure_reason == "exit_code_nonzero"

    rotate_task = store.create_task(
        node_id="node_windows",
        command="hmn:rotate-fingerprint sha256:new",
        risk="low",
        created_by="test",
    )
    rotate_next = client.post(
        "/api/v1/nodes/node_windows/tasks/next",
        json={"fingerprint": "sha256:old", "worker_protocol_version": worker_protocol},
    )
    assert rotate_next.status_code == 200
    rotate_payload = rotate_next.json()
    assert rotate_payload["task_id"] == rotate_task.task_id
    assert rotate_payload["command"] == "hmn:rotate-fingerprint sha256:new"
    assert verify_task_signature(
        node_fingerprint="sha256:old",
        task_id=rotate_payload["task_id"],
        command=rotate_payload["command"],
        risk=rotate_payload["risk"],
        signature=rotate_payload["signature"],
    )

    rotate_response = client.post(
        "/api/v1/nodes/node_windows/rotate-fingerprint",
        json={"fingerprint": "sha256:old", "new_fingerprint": "sha256:new"},
    )
    assert rotate_response.status_code == 200
    rotate_result = client.post(
        f"/api/v1/tasks/{rotate_task.task_id}/result",
        json={"fingerprint": "sha256:new", "exit_code": 0, "stdout": "fingerprint rotated", "stderr": ""},
    )
    assert rotate_result.status_code == 200

    rotated_node = store.load_node("node_windows")
    assert rotated_node.fingerprint == "sha256:new"
    rotated_task = store.load_task(rotate_task.task_id)
    assert rotated_task.status == "succeeded"
    assert rotated_task.stdout == "fingerprint rotated"
    rotate_events = [event for event in store.list_audit_events() if event.action == "rotate_fingerprint"]
    assert rotate_events
    assert rotate_events[-1].details == {"old_fingerprint_sha256": "sha256:old", "new_fingerprint_sha256": "sha256:new"}

    assert client.post(
        "/api/v1/nodes/node_windows/tasks/next",
        json={"fingerprint": "sha256:old", "worker_protocol_version": worker_protocol},
    ).status_code == 403


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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))
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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

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
    client = _auth_client(create_app(db))

    response = client.post(
        "/api/v1/nodes/node_rotate_reject/rotate-fingerprint",
        json={"fingerprint": "sha256:wrong", "new_fingerprint": "sha256:new"},
    )

    assert response.status_code == 403
    assert store.load_node("node_rotate_reject").fingerprint == "sha256:right"
