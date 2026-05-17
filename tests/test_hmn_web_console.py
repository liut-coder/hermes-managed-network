import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from hermes_managed_network.api import SESSION_COOKIE_NAME, create_app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


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


def _managed_node(store: SQLiteStore, node_id: str = "node_web") -> Node:
    node = Node(
        node_id=node_id,
        fingerprint=f"sha256:{node_id}",
        hostname=f"{node_id}-host",
        addresses=["100.64.0.20"],
        trust_level="B",
        labels=["worker"],
        status="managed",
        permission_bundles=["observe", "task"],
    )
    store.save_node(node)
    return node


def _write_kuma_db(kuma_db, monitors: list[dict]) -> None:
    import sqlite3

    con = sqlite3.connect(kuma_db)
    con.executescript(
        '''
        CREATE TABLE monitor (
            id INTEGER PRIMARY KEY,
            name VARCHAR(150),
            active BOOLEAN NOT NULL DEFAULT 1,
            url TEXT,
            type VARCHAR(20),
            keyword VARCHAR(255),
            hostname VARCHAR(255),
            port INTEGER,
            description TEXT,
            parent INTEGER
        );
        CREATE TABLE "group" (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            public BOOLEAN NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT 1,
            weight INTEGER NOT NULL DEFAULT 1000,
            status_page_id INTEGER
        );
        CREATE TABLE monitor_group (
            id INTEGER PRIMARY KEY,
            monitor_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1000,
            send_url BOOLEAN NOT NULL DEFAULT 0
        );
        '''
    )
    group_ids: dict[str, int] = {}
    next_group_id = 1
    for monitor in monitors:
        for group_name, _weight in monitor.get("groups", []):
            if group_name not in group_ids:
                group_ids[group_name] = next_group_id
                con.execute('INSERT INTO "group" (id, name, status_page_id) VALUES (?, ?, 1)', (next_group_id, group_name))
                next_group_id += 1
    for monitor in monitors:
        con.execute(
            'INSERT INTO monitor (id, name, active, url, type, keyword, hostname, port, description, parent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)',
            (
                monitor["id"],
                monitor["name"],
                monitor.get("active", 1),
                monitor.get("url"),
                monitor.get("type", "http"),
                monitor.get("keyword"),
                monitor.get("hostname"),
                monitor.get("port"),
                monitor.get("description", ""),
            ),
        )
        for group_name, weight in monitor.get("groups", []):
            con.execute(
                'INSERT INTO monitor_group (monitor_id, group_id, weight, send_url) VALUES (?, ?, ?, 1)',
                (monitor["id"], group_ids[group_name], weight),
            )
    con.commit()
    con.close()


def test_root_dashboard_links_all_control_flows(tmp_path):
    db = tmp_path / "hmn.db"
    kuma_db = tmp_path / "kuma.db"
    _write_kuma_db(
        kuma_db,
        [
            {
                "id": 1,
                "name": "Portal",
                "url": "https://portal.example.com",
                "type": "http",
                "description": "manual monitor",
                "groups": [("业务监控", 1000)],
            }
        ],
    )
    os.environ["HMN_KUMA_DB"] = str(kuma_db)
    store = SQLiteStore(db)
    _managed_node(store)
    store.create_task(node_id="node_web", command="uptime", risk="low", created_by="test")
    store.create_approval_request(
        subject_type="task",
        subject_id="task_request",
        action="task.run",
        risk="high",
        requested_by="test",
        details={"node_id": "node_web", "command": "reboot"},
    )
    client = _auth_client(create_app(db))

    response = client.get("/")

    assert response.status_code == 200
    assert "HMN 控制台" in response.text
    for href in ["/nodes", "/services", "/tasks", "/approvals", "/docs", "/audit", "/components", "/network", "/backups"]:
        assert f'href="{href}"' in response.text
    assert "node_web-host" in response.text
    assert "Portal" in response.text


def test_nodes_services_docs_and_audit_pages_render_current_state(tmp_path):
    db = tmp_path / "hmn.db"
    kuma_db = tmp_path / "kuma.db"
    _write_kuma_db(
        kuma_db,
        [
            {
                "id": 1,
                "name": "Web",
                "url": "https://web.example.com",
                "type": "http",
                "description": "manual monitor",
                "groups": [("业务监控", 1000)],
            }
        ],
    )
    os.environ["HMN_KUMA_DB"] = str(kuma_db)
    docs_root = tmp_path / "files"
    (docs_root / "service").mkdir(parents=True)
    (docs_root / "docs" / "server").mkdir(parents=True)
    (docs_root / "service" / "web.md").write_text("# Web Service\nsecret=hidden?\n", encoding="utf-8")
    store = SQLiteStore(db)
    _managed_node(store)
    store.record_audit(
        event_type="service",
        subject_type="service",
        subject_id="svc_web",
        action="discover",
        outcome="ok",
        details={"token": "should-not-leak", "safe": "visible"},
    )
    client = _auth_client(create_app(db, docs_root=docs_root))

    assert "node_web-host" in client.get("/nodes").text
    assert "Web" in client.get("/services").text
    docs_index = client.get("/docs")
    assert docs_index.status_code == 200
    assert "/docs/file/service/web.md" in docs_index.text
    doc = client.get("/docs/file/service/web.md")
    assert doc.status_code == 200
    assert "# Web Service" in doc.text
    audit = client.get("/audit")
    assert audit.status_code == 200
    assert "discover" in audit.text
    assert "should-not-leak" not in audit.text
    assert "[REDACTED]" in audit.text


def test_console_task_api_dispatches_low_risk_allowlisted_command(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    client = _auth_client(create_app(db))

    response = client.post("/api/v1/console/tasks", json={"node_id": "node_web", "command": "uptime", "created_by": "Misk"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["approval_id"] is None
    task = store.load_task(body["task_id"])
    assert task.command == "uptime"
    assert task.risk == "low"
    assert task.created_by == "Misk"
    assert "uptime" in client.get(f"/tasks/{task.task_id}").text


def test_console_task_api_creates_approval_for_high_risk_command_and_web_can_approve(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    client = _auth_client(create_app(db))

    create_response = client.post("/api/v1/console/tasks", json={"node_id": "node_web", "command": "reboot", "created_by": "Misk"})

    assert create_response.status_code == 202
    created = create_response.json()
    assert created["task_id"] is None
    assert created["approval_id"].startswith("appr_")
    assert store.list_tasks() == []
    approvals_page = client.get("/approvals")
    assert "reboot" in approvals_page.text
    approve_response = client.post(f"/approvals/{created['approval_id']}/approve", data={"decided_by": "Misk"}, follow_redirects=False)

    assert approve_response.status_code in {303, 307}
    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].command == "reboot"
    assert tasks[0].risk == "high"


def test_console_task_api_blocks_beacon_only_node_dispatch(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_web",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_mode": "beacon",
                "task_policy": "heartbeat-only",
                "exec_enabled": False,
            },
        },
    )
    client = _auth_client(create_app(db))

    response = client.post("/api/v1/console/tasks", json={"node_id": "node_web", "command": "uptime", "created_by": "Misk"})

    assert response.status_code == 409
    assert response.json()["detail"] == "node is beacon-only"
    assert store.list_tasks() == []


def test_web_approval_does_not_dispatch_task_to_heartbeat_only_node(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="console_task_request",
        action="task.run",
        risk="high",
        requested_by="Misk",
        details={
            "node_id": "node_web",
            "command": "reboot",
            "created_by": "Misk",
        },
    )
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id="node_web",
        action="heartbeat",
        outcome="ok",
        details={
            "status": "ok",
            "facts": {
                "worker_mode": "worker",
                "task_policy": "heartbeat-only",
                "exec_enabled": False,
            },
        },
    )
    client = _auth_client(create_app(db))

    approve_response = client.post(f"/approvals/{approval.approval_id}/approve", data={"decided_by": "Misk"}, follow_redirects=False)

    assert approve_response.status_code == 422
    assert "approval cannot be dispatched" in approve_response.text
    assert store.list_tasks() == []
    refreshed = store.load_approval_request(approval.approval_id)
    assert refreshed.status == "approved"
    assert "dispatched_task_id" not in refreshed.details
    events = store.list_audit_events()
    assert events[-1].action == "approval/dispatch"
    assert events[-1].outcome == "failed"
    assert events[-1].details["reason"] == "node heartbeat-only policy blocks task dispatch"


def test_component_network_and_backup_web_flows_create_dry_run_or_approval_records(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    client = _auth_client(create_app(db))

    component_plan = client.post("/api/v1/console/components/backup/plan", json={"node_id": "node_web", "action": "apply"})
    assert component_plan.status_code == 200
    assert component_plan.json()["dry_run"] is True

    component_run = client.post("/api/v1/console/components/backup/run", json={"node_id": "node_web", "action": "apply", "created_by": "Misk"})
    assert component_run.status_code == 202
    assert component_run.json()["approval_id"].startswith("appr_")

    network_plan = client.post("/api/v1/console/network/acl/plan", json={"proposed_acl": "{\"acls\":[]}"})
    assert network_plan.status_code == 200
    assert network_plan.json()["approval_required"] is True

    backup_plan = client.post("/api/v1/console/backups/plan", json={"node_id": "node_web", "target": "/srv/files"})
    assert backup_plan.status_code == 200
    assert backup_plan.json()["dry_run"] is True

    restore_run = client.post("/api/v1/console/restore/run", json={"node_id": "node_web", "backup_id": "backup_1", "created_by": "Misk"})
    assert restore_run.status_code == 202
    assert restore_run.json()["approval_id"].startswith("appr_")

    assert "backup" in client.get("/components").text
    assert "ACL" in client.get("/network").text
    assert "恢复" in client.get("/backups").text

def test_services_console_groups_kuma_business_and_system_assets(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    kuma_db = tmp_path / "kuma.db"
    con = sqlite3.connect(kuma_db)
    con.executescript(
        '''
        CREATE TABLE monitor (
            id INTEGER PRIMARY KEY,
            name VARCHAR(150),
            active BOOLEAN NOT NULL DEFAULT 1,
            url TEXT,
            type VARCHAR(20),
            keyword VARCHAR(255),
            hostname VARCHAR(255),
            port INTEGER,
            description TEXT,
            parent INTEGER
        );
        CREATE TABLE "group" (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            public BOOLEAN NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT 1,
            weight INTEGER NOT NULL DEFAULT 1000,
            status_page_id INTEGER
        );
        CREATE TABLE monitor_group (
            id INTEGER PRIMARY KEY,
            monitor_id INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1000,
            send_url BOOLEAN NOT NULL DEFAULT 0
        );
        '''
    )
    con.execute('INSERT INTO "group" (id, name, status_page_id) VALUES (1, ?, 1)', ("业务监控",))
    con.execute(
        'INSERT INTO monitor (id, name, active, url, type, keyword, description, parent) VALUES (1, ?, 1, ?, ?, NULL, ?, NULL)',
        ("Portal", "https://portal.example.com", "http", "manual monitor"),
    )
    con.execute(
        'INSERT INTO monitor (id, name, active, url, type, keyword, description, parent) VALUES (2, ?, 1, ?, ?, NULL, ?, NULL)',
        ("系统监控", "http://127.0.0.1:9090/metrics", "http", "system monitor"),
    )
    con.execute('INSERT INTO monitor_group (monitor_id, group_id, weight, send_url) VALUES (1, 1, 1000, 1)')
    con.commit()
    con.close()
    monkeypatch.setenv("HMN_KUMA_DB", str(kuma_db))
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/services")

    assert response.status_code == 200
    body = response.json()
    assert body["business_groups"][0]["category"] == "业务监控"
    assert body["business_groups"][0]["services"][0]["service_id"] == "kuma:1"
    assert body["pending_discoveries"] == []
    assert body["system_assets"][0]["service_id"] == "kuma:2"
    assert body["sheet"]["service_id"] == "kuma:1"
    assert body["create_dialog"]["defaults"]["source"] == "manual"

    html = client.get("/services").text
    assert "Portal" in html
    assert "系统监控" in html
    assert "data-services-payload" in html


def test_services_console_deduplicates_multi_group_kuma_monitors(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    kuma_db = tmp_path / "kuma.db"
    _write_kuma_db(
        kuma_db,
        [
            {
                "id": 1,
                "name": "Portal",
                "url": "https://portal.example.com",
                "type": "http",
                "description": "manual monitor",
                "groups": [("核心服务 SLA", 2000), ("自动发现服务", 1500)],
            }
        ],
    )
    monkeypatch.setenv("HMN_KUMA_DB", str(kuma_db))
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/services")

    assert response.status_code == 200
    body = response.json()
    assert len(body["services"]) == 1
    assert body["services"][0]["business_category"] == "核心服务 SLA"
    assert body["business_groups"] == [
        {
            "category": "核心服务 SLA",
            "count": 1,
            "services": body["services"],
        }
    ]

    html = client.get("/").text
    assert "Portal" in html
    assert "服务 1" in html


def test_services_page_renders_only_machine_grouping_without_business_sections(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    kuma_db = tmp_path / "kuma.db"
    _write_kuma_db(
        kuma_db,
        [
            {
                "id": 1,
                "name": "文件中心",
                "url": "https://file.misk.cc",
                "type": "http",
                "description": "manual monitor",
                "groups": [("核心服务 SLA", 1000)],
            },
            {
                "id": 2,
                "name": "模型代理",
                "url": "https://cpa.misk.cc",
                "type": "http",
                "description": "manual monitor",
                "groups": [("自动发现服务", 1000)],
            },
        ],
    )
    monkeypatch.setenv("HMN_KUMA_DB", str(kuma_db))
    client = _auth_client(create_app(db))

    response = client.get("/services")

    assert response.status_code == 200
    assert "机器视图" in response.text
    assert "file.misk.cc" in response.text
    assert "cpa.misk.cc" in response.text
    assert "/services/nodes/file.misk.cc" in response.text
    assert "/services/nodes/cpa.misk.cc" in response.text
    assert "data-services-payload" in response.text
    assert "<h2>核心服务 SLA</h2>" not in response.text
    assert "<h2>自动发现服务</h2>" not in response.text
    assert "<h2>待确认发现项</h2>" not in response.text
    assert "分组：" not in response.text


def test_services_node_detail_page_lists_only_that_machine_assets(tmp_path, monkeypatch):
    db = tmp_path / "hmn.db"
    kuma_db = tmp_path / "kuma.db"
    _write_kuma_db(
        kuma_db,
        [
            {
                "id": 1,
                "name": "文件中心",
                "url": "https://file.misk.cc",
                "type": "http",
                "description": "manual monitor",
                "groups": [("核心服务 SLA", 1000)],
            },
            {
                "id": 2,
                "name": "文件中心管理后台",
                "url": "https://file.misk.cc/admin",
                "type": "http",
                "description": "manual monitor",
                "groups": [("核心服务 SLA", 1000)],
            },
            {
                "id": 3,
                "name": "模型代理",
                "url": "https://cpa.misk.cc",
                "type": "http",
                "description": "manual monitor",
                "groups": [("自动发现服务", 1000)],
            },
        ],
    )
    monkeypatch.setenv("HMN_KUMA_DB", str(kuma_db))
    client = _auth_client(create_app(db))

    response = client.get("/services/nodes/file.misk.cc")

    assert response.status_code == 200
    assert "file.misk.cc" in response.text
    assert "文件中心" in response.text
    assert "文件中心管理后台" in response.text
    assert "模型代理" not in response.text
    assert "返回机器视图" in response.text
    assert "data-services-payload" in response.text

    missing = client.get("/services/nodes/not-found.example")
    assert missing.status_code == 404
