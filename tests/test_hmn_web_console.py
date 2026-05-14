import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from hermes_managed_network.api import SESSION_COOKIE_NAME, create_app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore, ServiceRecord


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


def test_root_dashboard_links_all_control_flows(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    store.save_service_record(ServiceRecord(service_id="svc_web", name="Web", node_id="node_web", ports=[443]))
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
    assert "Web" in response.text


def test_nodes_services_docs_and_audit_pages_render_current_state(tmp_path):
    db = tmp_path / "hmn.db"
    docs_root = tmp_path / "files"
    (docs_root / "service").mkdir(parents=True)
    (docs_root / "docs" / "server").mkdir(parents=True)
    (docs_root / "service" / "web.md").write_text("# Web Service\nsecret=hidden?\n", encoding="utf-8")
    store = SQLiteStore(db)
    _managed_node(store)
    store.save_service_record(
        ServiceRecord(service_id="svc_web", name="Web", node_id="node_web", ports=[443], docs_path="service/web.md")
    )
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


def test_service_assets_page_groups_business_pending_and_system_assets(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _managed_node(store)
    store.save_service_record(
        ServiceRecord(
            service_id="svc_portal",
            name="客户门户",
            node_id="node_web",
            kind="web",
            ports=[443],
            source="manual",
            status="active",
            metadata={"business_category": "客户体验"},
        )
    )
    store.save_service_record(
        ServiceRecord(
            service_id="svc_pending",
            name="待确认 Redis",
            node_id="node_web",
            kind="cache",
            ports=[6379],
            source="discovery",
            status="discovered",
            metadata={"business_category": "基础设施"},
        )
    )
    store.save_service_record(
        ServiceRecord(
            service_id="svc_system",
            name="系统监控",
            node_id="node_web",
            kind="system",
            ports=[9090],
            source="system",
            status="active",
            metadata={"business_category": "平台"},
        )
    )
    client = _auth_client(create_app(db))

    response = client.get("/api/v1/console/services")

    assert response.status_code == 200
    body = response.json()
    assert body["business_groups"][0]["category"] == "客户体验"
    assert body["business_groups"][0]["services"][0]["service_id"] == "svc_portal"
    assert body["pending_discoveries"][0]["service_id"] == "svc_pending"
    assert body["system_assets"][0]["service_id"] == "svc_system"
    assert body["sheet"]["service_id"] == "svc_portal"
    assert body["create_dialog"]["defaults"]["source"] == "manual"

    html = client.get("/services").text
    assert "客户体验" in html
    assert "待确认发现项" in html
    assert "系统资产" in html
    assert "data-services-payload" in html
