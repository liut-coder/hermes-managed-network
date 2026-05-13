import json
from pathlib import Path

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.migration import build_migration_plan_from_path
from hermes_managed_network.service_registry import ServiceRecord, ServiceRegistry


runner = CliRunner()


def _write_registry(tmp_path: Path) -> Path:
    registry = ServiceRegistry(
        [
            ServiceRecord(
                service_id="svc-demo-web",
                name="demo-web",
                node="edge-01",
                kind="docker",
                domains=["demo.example.com", "www.demo.example.com"],
                ports=[80, 443],
                runtime="nginx:alpine",
                source="discovery:demo token=super-secret",
                docs_path="service/demo-web.md",
                monitor={
                    "backup": {
                        "adapter": "restic",
                        "include_paths": ["/srv/demo", "/srv/demo/uploads"],
                        "exclude_patterns": ["cache/**"],
                        "repository": "s3:https://backup.example.com/demo-web-prod",
                        "password": "restic-password",
                        "verify": {
                            "mode": "restic-check",
                            "checksum": "sha256-manifest",
                        },
                    },
                    "restore": {
                        "target_path": "/srv/demo",
                        "config_paths": ["/etc/demo/config.yaml"],
                        "snapshot": "snap-prod-20260511",
                        "service_stop_required": True,
                        "domain_cutover_hint": "切换 demo.example.com 到恢复节点后再放量",
                        "healthcheck": "https://demo.example.com/healthz",
                        "smoke": ["curl -fsS https://demo.example.com/", "docker ps --filter name=demo-web"],
                    },
                },
                warnings=[],
            ),
            ServiceRecord(
                service_id="svc-demo-db",
                name="demo-db",
                node="db-01",
                kind="systemd",
                domains=["db.example.com"],
                ports=[5432],
                runtime="postgresql.service",
                source="discovery:demo password=hunter2",
                docs_path="service/demo-db.md",
                monitor={
                    "backup": {
                        "adapter": "borgmatic",
                        "include_paths": ["/var/lib/postgresql"],
                        "repository": "ssh://backup@vault.example.net/./repo/demo-db",
                        "ssh_key": "-----BEGIN KEY-----secret-----END KEY-----",
                    },
                    "restore": {
                        "target_path": "/var/lib/postgresql",
                        "config_paths": ["/etc/postgresql/15/main/postgresql.conf"],
                        "snapshot": "latest",
                        "service_stop_required": True,
                    },
                },
                warnings=[],
            ),
            ServiceRecord(
                service_id="svc-stateless",
                name="stateless",
                node="edge-03",
                kind="docker",
                domains=["stateless.example.com"],
                ports=[8080],
                runtime="busybox",
                source="discovery:demo api_key=worker-secret",
                docs_path="service/stateless.md",
                monitor={"providers": {"uptime": {"enabled": True}}},
                warnings=["no persistent volume"],
            ),
        ]
    )
    registry_path = tmp_path / "service-registry.json"
    registry.save(registry_path)
    return registry_path


def test_registry_builds_migration_plan_from_backup_and_restore_metadata(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_migration_plan_from_path(registry_path)

    assert payload["mode"] == "plan"
    assert payload["dry_run"] is True
    assert payload["service_count"] == 3

    service = next(item for item in payload["services"] if item["service_id"] == "svc-demo-web")
    assert service["source_node"] == "edge-01"
    assert service["target_node"] == "edge-01"
    assert service["strategy"] == "backup-restore"
    assert service["data_paths"] == ["/srv/demo", "/srv/demo/uploads"]
    assert service["config_paths"] == ["/etc/demo/config.yaml"]
    assert service["docs_path"] == "service/demo-web.md"
    assert service["ports"] == [80, 443]
    assert service["domains"] == ["demo.example.com", "www.demo.example.com"]


def test_migration_plan_supports_service_source_target_and_strategy_filters(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_migration_plan_from_path(
        registry_path,
        service_id="svc-demo-db",
        source_node="db-01",
        target_node="db-02",
        strategy="manual-copy",
    )

    assert payload["service_count"] == 1
    service = payload["services"][0]
    assert service["service_id"] == "svc-demo-db"
    assert service["source_node"] == "db-01"
    assert service["target_node"] == "db-02"
    assert service["strategy"] == "manual-copy"


def test_migration_plan_includes_prerequisites_conflicts_verification_and_rollback(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_migration_plan_from_path(registry_path, service_id="svc-demo-web", target_node="edge-02")

    service = payload["services"][0]
    conflict_names = {item["name"] for item in service["conflict_checks"]}
    prerequisite_names = {item["name"] for item in service["prerequisites"]}
    verification_names = {item["name"] for item in service["verification_steps"]}
    rollback_names = {item["name"] for item in service["rollback_steps"]}

    assert {"ports", "domains"} <= conflict_names
    assert {"backup_metadata", "restore_metadata", "target_node"} <= prerequisite_names
    assert {"healthcheck", "port_smoke", "domain_smoke", "checksum"} <= verification_names
    assert {"repoint_dns", "revert_proxy", "restore_source_service"} <= rollback_names
    assert service["dns_cutover_hint"] == "切换 demo.example.com 到恢复节点后再放量"
    assert service["reverse_proxy_cutover_hint"]


def test_migration_plan_risk_flags_and_apply_skeleton_do_not_promise_zero_downtime(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_migration_plan_from_path(registry_path, service_id="svc-demo-web")

    service = payload["services"][0]
    assert service["risk_flags"] == {
        "no_zero_downtime_guarantee": True,
        "requires_maintenance_window": True,
        "dry_run_only": True,
    }
    assert service["apply"] == {
        "approval_required": True,
        "not_executed": True,
        "machine_changed": False,
    }


def test_missing_target_node_adds_warning_without_failure(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_migration_plan_from_path(registry_path, service_id="svc-demo-web", target_node="")

    assert payload["service_count"] == 1
    service = payload["services"][0]
    assert service["target_node"] == "edge-01"
    assert any("target node not specified" in warning for warning in service["warnings"])


def test_migration_plan_cli_json_is_parseable(tmp_path):
    registry_path = _write_registry(tmp_path)

    result = runner.invoke(
        app,
        [
            "migration",
            "plan",
            "--service-registry",
            str(registry_path),
            "--service-id",
            "svc-demo-web",
            "--source-node",
            "edge-01",
            "--target-node",
            "edge-02",
            "--strategy",
            "redeploy",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["service_count"] == 1
    assert payload["services"][0]["strategy"] == "redeploy"
    assert payload["services"][0]["target_node"] == "edge-02"


def test_migration_plan_masks_sensitive_fields_in_json_output(tmp_path):
    registry_path = _write_registry(tmp_path)

    result = runner.invoke(
        app,
        [
            "migration",
            "plan",
            "--service-registry",
            str(registry_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    rendered = result.stdout
    payload = json.loads(rendered)
    service = next(item for item in payload["services"] if item["service_id"] == "svc-demo-web")

    assert service["backup_prerequisite"]["repository"] == "[REDACTED]"
    assert service["backup_prerequisite"]["snapshot"] == "[REDACTED]"
    assert "super-secret" not in rendered
    assert "hunter2" not in rendered
    assert "restic-password" not in rendered
    assert "worker-secret" not in rendered
    assert "snap-prod-20260511" not in rendered
    assert rendered.count("[REDACTED]") >= 4
