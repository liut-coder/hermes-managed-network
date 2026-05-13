import json
from pathlib import Path

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.restore import build_restore_plan_from_path
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
                domains=["demo.example.com"],
                ports=[80, 443],
                runtime="nginx:alpine",
                source="discovery:demo token=super-secret",
                docs_path="service/demo-web.md",
                monitor={
                    "backup": {
                        "adapter": "restic",
                        "repository": "s3:https://backup.example.com/demo-web-prod",
                        "password": "restic-password",
                        "verify": {
                            "mode": "restic-check",
                            "checksum": "sha256-manifest",
                        },
                    },
                    "restore": {
                        "target_path": "/srv/demo",
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
                domains=[],
                ports=[5432],
                runtime="postgresql.service",
                source="discovery:demo password=hunter2",
                docs_path="service/demo-db.md",
                monitor={
                    "backup": {
                        "adapter": "borgmatic",
                        "repository": "ssh://backup@vault.example.net/./repo/demo-db",
                        "ssh_key": "-----BEGIN KEY-----secret-----END KEY-----",
                    },
                    "restore": {
                        "target_path": "/var/lib/postgresql",
                        "snapshot": "latest",
                        "service_stop_required": True,
                    },
                },
                warnings=[],
            ),
            ServiceRecord(
                service_id="svc-no-restore",
                name="stateless",
                node="edge-03",
                kind="docker",
                domains=[],
                ports=[],
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


def test_registry_metadata_builds_restore_plan(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_restore_plan_from_path(registry_path)

    assert payload["mode"] == "plan"
    assert payload["service_count"] == 3
    service = next(item for item in payload["services"] if item["service_id"] == "svc-demo-web")
    assert service["node"] == "edge-01"
    assert service["adapter"] == "restic"
    assert service["repository"] == "[REDACTED]"
    assert service["snapshot_selector"] == "[REDACTED]"
    assert service["target_node"] == "edge-01"
    assert service["target_path"] == "[REDACTED]"
    assert service["source_selector"] == "[REDACTED]"
    assert service["restore_steps"]


def test_restore_plan_preflight_checks_include_required_safety_items(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_restore_plan_from_path(registry_path, service_id="svc-demo-web")

    checks = payload["services"][0]["preflight_checks"]
    names = {item["name"] for item in checks}
    assert {"disk_space", "path_exists_or_empty", "permissions", "port_conflicts", "service_stop_required", "domain_cutover"} <= names



def test_restore_plan_verification_steps_and_rollback_hint_are_present(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_restore_plan_from_path(registry_path, service_id="svc-demo-web")

    service = payload["services"][0]
    verification_names = {item["name"] for item in service["verification_steps"]}
    assert {"checksum", "healthcheck", "port", "domain", "smoke"} <= verification_names
    assert service["rollback_hint"]



def test_restore_plan_apply_is_approval_gate_not_executed(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_restore_plan_from_path(registry_path, service_id="svc-demo-web")

    assert payload["services"][0]["apply"] == {
        "approval_required": True,
        "not_executed": True,
        "machine_changed": False,
    }



def test_missing_restore_or_backup_metadata_warns_without_failure(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_restore_plan_from_path(registry_path)

    service = next(item for item in payload["services"] if item["service_id"] == "svc-no-restore")
    assert service["adapter"] is None
    assert service["repository"] is None
    assert service["snapshot_selector"] == "latest"
    assert any("missing restore metadata" in warning or "missing backup metadata" in warning for warning in service["warnings"])



def test_restore_plan_supports_service_id_target_node_and_snapshot_filters(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_restore_plan_from_path(
        registry_path,
        service_id="svc-demo-db",
        target_node="restore-db-01",
        snapshot="snapshot-20260511",
    )

    assert payload["service_count"] == 1
    service = payload["services"][0]
    assert service["service_id"] == "svc-demo-db"
    assert service["target_node"] == "restore-db-01"
    assert service["snapshot_selector"] == "[REDACTED]"



def test_restore_plan_cli_json_is_parseable(tmp_path):
    registry_path = _write_registry(tmp_path)

    result = runner.invoke(
        app,
        [
            "restore",
            "plan",
            "--service-registry",
            str(registry_path),
            "--service-id",
            "svc-demo-web",
            "--target-node",
            "restore-edge-01",
            "--snapshot",
            "latest",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["service_count"] == 1
    assert payload["services"][0]["service_id"] == "svc-demo-web"
    assert payload["services"][0]["target_node"] == "restore-edge-01"



def test_restore_plan_masks_sensitive_fields_in_json_output(tmp_path):
    registry_path = _write_registry(tmp_path)

    result = runner.invoke(
        app,
        [
            "restore",
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

    assert service["repository"] == "[REDACTED]"
    assert service["target_path"] == "[REDACTED]"
    assert service["source_selector"] == "[REDACTED]"
    assert "super-secret" not in rendered
    assert "hunter2" not in rendered
    assert "restic-password" not in rendered
    assert "worker-secret" not in rendered
    assert "snap-prod-20260511" not in rendered
    assert "/srv/demo" not in rendered
    assert rendered.count("[REDACTED]") >= 4
