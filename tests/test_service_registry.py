import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.storage import SQLiteStore, ServiceRecord


def test_service_registry_assets_classify_manual_business_as_main_view(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)

    record = ServiceRecord(
        service_id="svc_billing",
        name="Billing",
        node_id="node1",
        kind="docker",
        runtime="compose",
        source="manual",
        status="active",
        metadata={"asset_category": "business"},
    )

    saved = store.save_service_record(record)
    loaded = store.load_service_record("svc_billing")

    assert saved.asset_category == "main"
    assert saved.asset_score >= 70
    assert any("manual" in reason.lower() for reason in saved.why_asset)
    assert loaded is not None
    assert loaded.asset_category == "main"
    assert loaded.asset_score == saved.asset_score
    assert loaded.why_asset == saved.why_asset


def test_service_registry_assets_mark_system_service_as_system_asset(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)

    saved = store.save_service_record(
        ServiceRecord(
            service_id="svc_hmn_control",
            name="HMN Control Plane",
            node_id="node-control",
            kind="systemd",
            runtime="systemd",
            source="discovery",
            status="active",
        )
    )

    assert saved.asset_category == "system"
    assert saved.asset_score < 40
    assert saved.why_asset



def _coolify_fixture() -> dict[str, object]:
    return {
        "server": {"name": "edge-01"},
        "project": {"name": "demo-project"},
        "environment": {"name": "production"},
        "applications": [
            {
                "uuid": "app-1",
                "name": "demo-web",
                "status": "running",
                "domains": [{"domain": "demo.example.com"}],
                "ports": [80, 443],
                "git_repository": "https://token@github.com/example/demo-web.git",
                "git_branch": "main",
                "deploy_target": {"destination": "root@10.0.0.8:/srv/demo", "authorization": "Bearer secret"},
                "env": {"API_KEY": "secret-key"},
            }
        ],
    }


def test_service_registry_save_load_list_roundtrip(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)

    record = ServiceRecord(
        service_id="svc_mailgw",
        name="Mail Gateway",
        node_id="node1",
        kind="docker",
        runtime="compose",
        domains=["mail.example.invalid"],
        ports=[443, 8080],
        deploy_path="/srv/mailgw",
        config_paths=["/srv/mailgw/docker-compose.yml"],
        env_paths=["/srv/mailgw/.env"],
        data_paths=["/srv/mailgw/data"],
        health_check_url="https://mail.example.invalid/healthz",
        monitor_enabled=True,
        docs_path="/srv/files/service/svc_mailgw/README.md",
        source="manual",
        status="active",
        metadata={"image": "example/mailgw:latest"},
    )

    saved = store.save_service_record(record)
    loaded = store.load_service_record("svc_mailgw")
    listed = store.list_service_records()

    assert saved.service_id == "svc_mailgw"
    assert loaded == saved
    assert listed == [saved]


def test_service_registry_upsert_updates_existing_record(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_service_record(ServiceRecord(service_id="svc_web", name="Web", node_id="node1", kind="systemd", ports=[80]))

    updated = store.save_service_record(
        ServiceRecord(
            service_id="svc_web",
            name="Web App",
            node_id="node1",
            kind="systemd",
            ports=[80, 443],
            domains=["web.example.invalid"],
        )
    )

    assert updated.name == "Web App"
    assert store.load_service_record("svc_web").ports == [80, 443]
    assert len(store.list_service_records()) == 1


def test_service_add_list_show_roundtrip(tmp_path):
    db = tmp_path / "hmn.db"
    runner = CliRunner()

    added = runner.invoke(
        app,
        [
            "service",
            "add",
            "svc_mailgw",
            "--db",
            str(db),
            "--name",
            "Mail Gateway",
            "--node",
            "node1",
            "--kind",
            "docker",
            "--runtime",
            "compose",
            "--domain",
            "mail.example.invalid",
            "--port",
            "443",
            "--deploy-path",
            "/srv/mailgw",
            "--config-path",
            "/srv/mailgw/docker-compose.yml",
            "--env-path",
            "/srv/mailgw/.env",
            "--data-path",
            "/srv/mailgw/data",
            "--health-check-url",
            "https://mail.example.invalid/healthz",
            "--monitor-enabled",
        ],
    )
    assert added.exit_code == 0
    assert "service saved: svc_mailgw" in added.stdout

    listed = runner.invoke(app, ["service", "list", "--db", str(db)])
    assert listed.exit_code == 0
    assert "svc_mailgw" in listed.stdout
    assert "Mail Gateway" in listed.stdout
    assert "mail.example.invalid" in listed.stdout

    shown = runner.invoke(app, ["service", "show", "svc_mailgw", "--db", str(db)])
    assert shown.exit_code == 0
    assert "service: svc_mailgw" in shown.stdout
    assert "node: node1" in shown.stdout
    assert "kind: docker" in shown.stdout
    assert "runtime: compose" in shown.stdout
    assert "domains: mail.example.invalid" in shown.stdout
    assert "ports: 443" in shown.stdout


def test_service_coolify_sync_apply_writes_registry_and_audit(tmp_path):
    db = tmp_path / "hmn.db"
    fixture = tmp_path / "coolify.json"
    fixture.write_text(json.dumps(_coolify_fixture()), encoding="utf-8")

    result = CliRunner().invoke(app, ["service", "coolify-sync", "--db", str(db), "--fixture", str(fixture), "--apply", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is False
    assert payload["apply"] is True
    assert payload["provider"] == "coolify"
    assert payload["service_count"] == 1

    store = SQLiteStore(db)
    saved = store.load_service_record("svc_edge-01_demo-web")
    assert saved is not None
    assert saved.node_id == "edge-01"
    assert saved.deploy_path == "root@10.0.0.8:/srv/demo"
    assert saved.metadata["coolify"]["repo"] == "[REDACTED]"

    audits = store.list_audit_events()
    assert len(audits) == 1
    assert audits[0].action == "coolify_registry_sync"
    assert audits[0].subject_id == "coolify"


def test_service_coolify_sync_defaults_to_dry_run_without_writes(tmp_path):
    db = tmp_path / "hmn.db"
    fixture = tmp_path / "coolify.json"
    fixture.write_text(json.dumps(_coolify_fixture()), encoding="utf-8")

    result = CliRunner().invoke(app, ["service", "coolify-sync", "--db", str(db), "--fixture", str(fixture), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["apply"] is False
    assert payload["write"] is False
    assert payload["service_count"] == 1

    store = SQLiteStore(db)
    assert store.list_service_records() == []
    assert store.list_audit_events() == []
