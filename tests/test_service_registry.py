from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.storage import SQLiteStore, ServiceRecord


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
