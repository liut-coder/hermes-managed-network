import json
from pathlib import Path

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.docs_sync import build_docs_sync_plan_from_path
from hermes_managed_network.service_registry import ServiceRecord, ServiceRegistry


runner = CliRunner()


def _write_registry(tmp_path: Path) -> Path:
    registry = ServiceRegistry(
        [
            ServiceRecord(
                service_id="edge-01:docker:demo-web",
                name="Demo Web",
                node="edge-01",
                kind="docker",
                domains=["demo.example.com", "www.demo.example.com"],
                ports=[80, 443],
                runtime="nginx:alpine",
                source="docker inspect token=super-secret",
                docs_path="service/demo-web.md",
                monitor={
                    "api_key": "top-secret",
                    "runbook": {
                        "url": "https://internal.example/runbooks/demo-web?token=abc",
                    },
                },
                warnings=["public edge"],
            ),
            ServiceRecord(
                service_id="legacy-01:systemd:../db main",
                name="../db main",
                node="legacy-01",
                kind="systemd",
                domains=["db.example.com"],
                ports=[5432],
                runtime="postgresql.service",
                source="file:/etc/postgres password=hunter2",
                docs_path=None,
                monitor={
                    "password": "hunter2",
                    "notes": "Authorization: Bearer secret-token",
                },
                warnings=[],
            ),
        ]
    )
    registry_path = tmp_path / "service-registry.json"
    registry.save(registry_path)
    return registry_path



def test_docs_sync_plan_outputs_server_and_service_targets_from_registry(tmp_path):
    registry_path = _write_registry(tmp_path)

    plan = build_docs_sync_plan_from_path(registry_path)

    assert plan["dry_run"] is True
    assert plan["service_count"] == 2
    assert plan["server_doc_root"] == "/srv/files/docs/server"
    assert plan["service_doc_root"] == "/srv/files/service"

    server_targets = {item["host"]: item["target_path"] for item in plan["server_docs"]}
    assert server_targets["edge-01"] == "/srv/files/docs/server/edge-01/README.md"
    assert server_targets["legacy-01"] == "/srv/files/docs/server/legacy-01/README.md"

    service_targets = {item["service_id"]: item for item in plan["service_docs"]}
    assert service_targets["edge-01:docker:demo-web"]["target_path"] == "/srv/files/service/edge-01-docker-demo-web/README.md"
    assert service_targets["edge-01:docker:demo-web"]["service_slug"] == "edge-01-docker-demo-web"
    assert service_targets["legacy-01:systemd:../db main"]["target_path"] == "/srv/files/service/legacy-01-systemd-db-main/README.md"
    assert service_targets["legacy-01:systemd:../db main"]["service_slug"] == "legacy-01-systemd-db-main"



def test_docs_sync_plan_generates_server_service_and_domain_indexes(tmp_path):
    registry_path = _write_registry(tmp_path)

    plan = build_docs_sync_plan_from_path(registry_path)

    indexes = plan["indexes"]
    assert indexes["server"]["target_path"] == "/srv/files/docs/server/README.md"
    assert indexes["service"]["target_path"] == "/srv/files/service/README.md"
    assert indexes["domain_mapping"]["target_path"] == "/srv/files/service/domain-mapping.json"
    assert indexes["runbook_mapping"]["target_path"] == "/srv/files/service/runbook-mapping.json"

    assert indexes["server"]["entries"][0]["host"] == "edge-01"
    assert indexes["server"]["entries"][0]["doc_path"] == "/srv/files/docs/server/edge-01/README.md"

    service_index = {item["service_id"]: item for item in indexes["service"]["entries"]}
    assert service_index["edge-01:docker:demo-web"]["doc_path"] == "/srv/files/service/edge-01-docker-demo-web/README.md"

    domain_mapping = indexes["domain_mapping"]["entries"]
    assert domain_mapping["demo.example.com"][0]["service_id"] == "edge-01:docker:demo-web"
    assert domain_mapping["db.example.com"][0]["doc_path"] == "/srv/files/service/legacy-01-systemd-db-main/README.md"

    runbook_mapping = {item["service_id"]: item for item in indexes["runbook_mapping"]["entries"]}
    assert runbook_mapping["edge-01:docker:demo-web"]["host"] == "edge-01"
    assert runbook_mapping["edge-01:docker:demo-web"]["service_doc_path"] == "/srv/files/service/edge-01-docker-demo-web/README.md"
    assert runbook_mapping["edge-01:docker:demo-web"]["server_doc_path"] == "/srv/files/docs/server/edge-01/README.md"



def test_docs_sync_plan_includes_hostname_rename_move_and_update_actions(tmp_path):
    registry_path = _write_registry(tmp_path)

    plan = build_docs_sync_plan_from_path(registry_path, rename_hosts={"legacy-01": "db-prod-01"})

    rename_actions = plan["rename_actions"]
    assert rename_actions == [
        {
            "action": "move",
            "from": "/srv/files/docs/server/legacy-01",
            "to": "/srv/files/docs/server/db-prod-01",
            "host": "legacy-01",
            "new_host": "db-prod-01",
        },
        {
            "action": "update_references",
            "paths": [
                "/srv/files/docs/server/README.md",
                "/srv/files/service/README.md",
                "/srv/files/service/domain-mapping.json",
                "/srv/files/service/runbook-mapping.json",
                "/srv/files/service/legacy-01-systemd-db-main/README.md",
            ],
            "host": "legacy-01",
            "new_host": "db-prod-01",
        },
    ]

    renamed_server = {item["host"]: item for item in plan["server_docs"]}
    assert renamed_server["db-prod-01"]["source_host"] == "legacy-01"
    assert renamed_server["db-prod-01"]["target_path"] == "/srv/files/docs/server/db-prod-01/README.md"



def test_docs_sync_plan_uses_safe_service_slug_and_never_traverses_parent_paths(tmp_path):
    registry_path = _write_registry(tmp_path)

    plan = build_docs_sync_plan_from_path(registry_path)

    unsafe_service = next(item for item in plan["service_docs"] if item["service_id"] == "legacy-01:systemd:../db main")
    assert unsafe_service["service_slug"] == "legacy-01-systemd-db-main"
    assert ".." not in unsafe_service["service_slug"]
    assert "/../" not in unsafe_service["target_path"]
    assert unsafe_service["target_path"].startswith("/srv/files/service/")



def test_docs_sync_plan_cli_json_is_parseable_and_redacts_sensitive_fields(tmp_path):
    registry_path = _write_registry(tmp_path)

    result = runner.invoke(
        app,
        [
            "docs",
            "sync",
            "plan",
            "--service-registry",
            str(registry_path),
            "--rename-host",
            "legacy-01=db-prod-01",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    rendered = result.stdout

    assert payload["dry_run"] is True
    assert payload["rename_actions"][0]["to"] == "/srv/files/docs/server/db-prod-01"
    assert "super-secret" not in rendered
    assert "hunter2" not in rendered
    assert "secret-token" not in rendered
    assert "top-secret" not in rendered
    assert rendered.count("[REDACTED]") >= 4


def test_docs_sync_apply_requires_approval_and_does_not_write_docs(tmp_path):
    registry_path = _write_registry(tmp_path)
    db = tmp_path / "hmn.db"
    server_root = tmp_path / "server-docs"
    service_root = tmp_path / "service-docs"

    result = runner.invoke(
        app,
        [
            "docs",
            "sync",
            "apply",
            "--db",
            str(db),
            "--service-registry",
            str(registry_path),
            "--server-doc-root",
            str(server_root),
            "--service-doc-root",
            str(service_root),
        ],
    )

    assert result.exit_code != 0
    assert "需要审批" in result.stdout
    assert "未写入 docs-center" in result.stdout
    assert not server_root.exists()
    assert not service_root.exists()
    from hermes_managed_network.storage import SQLiteStore

    approval = SQLiteStore(db).list_approval_requests(status="pending")[0]
    assert approval.subject_type == "docs_sync"
    assert approval.action == "docs.sync.apply"
    assert approval.risk == "high"
    assert approval.details["plan"]["dry_run"] is True
    assert approval.details["plan"]["server_count"] == 2
