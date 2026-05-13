import json
from pathlib import Path

from typer.testing import CliRunner

from hermes_managed_network.cli import app
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


def test_docs_sync_apply_defaults_to_dry_run_and_does_not_create_files(tmp_path):
    registry_path = _write_registry(tmp_path)
    root = tmp_path / "docs-center"

    result = runner.invoke(
        app,
        [
            "docs",
            "sync",
            "apply",
            "--service-registry",
            str(registry_path),
            "--root",
            str(root),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)

    assert payload["dry_run"] is True
    assert payload["execute"] is False
    assert payload["written"] == []
    assert payload["changed"] == 0
    assert payload["skipped"] >= 7
    assert not (root / "docs").exists()
    assert not (root / "service").exists()


def test_docs_sync_apply_execute_writes_server_service_indexes_and_redacts_secrets(tmp_path):
    registry_path = _write_registry(tmp_path)
    root = tmp_path / "docs-center"

    result = runner.invoke(
        app,
        [
            "docs",
            "sync",
            "apply",
            "--service-registry",
            str(registry_path),
            "--root",
            str(root),
            "--execute",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)

    assert payload["dry_run"] is False
    assert payload["execute"] is True
    assert payload["changed"] >= 9
    assert payload["audit"]["event_type"] == "docs_sync"
    assert payload["audit"]["action"] == "apply"
    assert payload["audit"]["outcome"] == "success"

    expected_files = [
        root / "docs" / "README.md",
        root / "docs" / "index.json",
        root / "docs" / "server" / "README.md",
        root / "docs" / "server" / "edge-01" / "README.md",
        root / "docs" / "server" / "legacy-01" / "README.md",
        root / "service" / "README.md",
        root / "service" / "index.json",
        root / "service" / "edge-01-docker-demo-web" / "README.md",
        root / "service" / "legacy-01-systemd-db-main" / "README.md",
        root / "service" / "domain-mapping.json",
        root / "service" / "runbook-mapping.json",
    ]
    for path in expected_files:
        assert path.exists(), path

    service_doc = (root / "service" / "edge-01-docker-demo-web" / "README.md").read_text(encoding="utf-8")
    assert "super-secret" not in service_doc
    assert "top-secret" not in service_doc
    assert "[REDACTED]" in service_doc

    index_payload = json.loads((root / "service" / "index.json").read_text(encoding="utf-8"))
    rendered = json.dumps(index_payload, ensure_ascii=False)
    assert index_payload["services"][0]["doc_path"].startswith(str(root / "service"))
    assert index_payload["domain_mapping"]["demo.example.com"][0]["service_id"] == "edge-01:docker:demo-web"
    assert index_payload["runbook_mapping"][0]["service_id"] == "edge-01:docker:demo-web"
    assert "hunter2" not in rendered
    assert "secret-token" not in rendered

    domain_mapping_payload = json.loads((root / "service" / "domain-mapping.json").read_text(encoding="utf-8"))
    assert domain_mapping_payload["db.example.com"][0]["service_id"] == "legacy-01:systemd:../db main"

    runbook_mapping_payload = json.loads((root / "service" / "runbook-mapping.json").read_text(encoding="utf-8"))
    assert runbook_mapping_payload[0]["service_id"] == "edge-01:docker:demo-web"

    server_doc = (root / "docs" / "server" / "edge-01" / "README.md").read_text(encoding="utf-8")
    assert "[`edge-01:docker:demo-web`](../../../service/edge-01-docker-demo-web/README.md)" in server_doc


def test_docs_sync_apply_sanitizes_host_and_service_paths_without_escape(tmp_path):
    registry_path = _write_registry(tmp_path)
    root = tmp_path / "docs-center"

    result = runner.invoke(
        app,
        [
            "docs",
            "sync",
            "apply",
            "--service-registry",
            str(registry_path),
            "--root",
            str(root),
            "--rename-host",
            "legacy-01=../../prod/db?01",
            "--execute",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)

    written_paths = [Path(path).resolve() for path in payload["written"]]
    root_resolved = root.resolve()
    assert written_paths
    for path in written_paths:
        assert root_resolved == path or root_resolved in path.parents

    assert not (root.parent / "prod").exists()
    assert (root / "docs" / "server" / "prod-db-01" / "README.md").exists()
    assert (root / "service" / "legacy-01-systemd-db-main" / "README.md").exists()


def test_docs_sync_apply_json_output_is_parseable_and_keeps_plan_command_compatible(tmp_path):
    registry_path = _write_registry(tmp_path)
    root = tmp_path / "docs-center"

    apply_result = runner.invoke(
        app,
        [
            "docs",
            "sync",
            "apply",
            "--service-registry",
            str(registry_path),
            "--root",
            str(root),
            "--json",
        ],
    )
    plan_result = runner.invoke(
        app,
        [
            "docs",
            "sync",
            "plan",
            "--service-registry",
            str(registry_path),
            "--json",
        ],
    )

    assert apply_result.exit_code == 0, apply_result.stdout
    assert plan_result.exit_code == 0, plan_result.stdout

    apply_payload = json.loads(apply_result.stdout)
    plan_payload = json.loads(plan_result.stdout)
    assert {"changed", "written", "skipped", "audit"}.issubset(apply_payload)
    assert plan_payload["mode"] == "docs-sync-plan"
    assert plan_payload["dry_run"] is True
