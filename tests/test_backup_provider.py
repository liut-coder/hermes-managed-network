import json
from pathlib import Path

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.backup_provider import build_backup_plan_from_path
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
                        "include_paths": ["/srv/demo", "/etc/demo/config.yaml"],
                        "exclude_patterns": ["*.tmp", "cache/**"],
                        "repository": "s3:https://backup.example.com/demo-web-prod",
                        "password": "restic-password",
                        "retention": {
                            "keep_daily": 7,
                            "keep_weekly": 4,
                            "keep_monthly": 6,
                        },
                        "schedule": "daily 03:30",
                        "verify": {
                            "mode": "restic-check",
                            "checksum": "sha256-manifest",
                        },
                    }
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
                        "include_paths": ["/var/lib/postgresql"],
                        "exclude_patterns": ["pg_wal/**"],
                        "repository": "ssh://backup@vault.example.net/./repo/demo-db",
                        "ssh_key": "-----BEGIN KEY-----secret-----END KEY-----",
                        "retention": {
                            "keep_daily": 14,
                        },
                        "schedule": "daily 02:15",
                        "verify": {
                            "mode": "borg-check",
                            "checksum": "manifest-digest",
                        },
                    }
                },
                warnings=[],
            ),
            ServiceRecord(
                service_id="svc-demo-worker",
                name="demo-worker",
                node="edge-02",
                kind="worker",
                domains=[],
                ports=[],
                runtime="python app.py",
                source="discovery:demo api_key=worker-secret",
                docs_path="service/demo-worker.md",
                monitor={
                    "backup": {
                        "adapter": "kopia",
                        "include_paths": ["/opt/demo-worker/data"],
                        "exclude_patterns": ["tmp/**"],
                        "repository": "kopia://bucket/tenant/demo-worker",
                        "token": "kopia-secret-token",
                        "retention": {
                            "keep_latest": 10,
                        },
                        "schedule": "hourly",
                        "verify": {
                            "mode": "snapshot-verify",
                            "checksum": "metadata-hash",
                        },
                    }
                },
                warnings=[],
            ),
            ServiceRecord(
                service_id="svc-no-backup",
                name="stateless",
                node="edge-03",
                kind="docker",
                domains=[],
                ports=[],
                runtime="busybox",
                source="discovery:demo",
                docs_path="service/stateless.md",
                monitor={"providers": {"uptime": {"enabled": True}}},
                warnings=["no persistent volume"],
            ),
        ]
    )
    registry_path = tmp_path / "service-registry.json"
    registry.save(registry_path)
    return registry_path



def test_registry_backup_metadata_builds_restic_service_plan(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_backup_plan_from_path(registry_path)

    assert payload["mode"] == "plan"
    assert payload["service_count"] == 4
    assert payload["warning_count"] == 1

    service = next(item for item in payload["services"] if item["service_id"] == "svc-demo-web")
    assert service["node"] == "edge-01"
    assert service["adapter"] == "restic"
    assert service["include_paths"] == ["/srv/demo", "/etc/demo/config.yaml"]
    assert service["exclude_patterns"] == ["*.tmp", "cache/**"]
    assert service["retention_policy"] == {
        "keep_daily": 7,
        "keep_weekly": 4,
        "keep_monthly": 6,
    }
    assert service["schedule_hint"] == "daily 03:30"
    assert service["verify_plan"] == {
        "mode": "restic-check",
        "checksum": "sha256-manifest",
    }
    assert service["repository"] == "[REDACTED]"
    assert service["approval"] == {
        "approval_required": True,
        "not_executed": True,
        "machine_changed": False,
    }



def test_backup_provider_supports_borgmatic_and_kopia_adapter_skeletons(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_backup_plan_from_path(registry_path)
    services = {item["service_id"]: item for item in payload["services"]}

    assert services["svc-demo-db"]["adapter"] == "borgmatic"
    assert services["svc-demo-db"]["verify_plan"]["mode"] == "borg-check"
    assert services["svc-demo-worker"]["adapter"] == "kopia"
    assert services["svc-demo-worker"]["verify_plan"]["mode"] == "snapshot-verify"



def test_backup_provider_missing_backup_metadata_returns_warning_without_failure(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_backup_plan_from_path(registry_path)

    service = next(item for item in payload["services"] if item["service_id"] == "svc-no-backup")
    assert service["adapter"] is None
    assert service["repository"] is None
    assert service["approval"] == {
        "approval_required": True,
        "not_executed": True,
        "machine_changed": False,
    }
    assert "missing backup metadata" in service["warnings"][0]



def test_backup_provider_supports_service_id_and_adapter_filters(tmp_path):
    registry_path = _write_registry(tmp_path)

    payload = build_backup_plan_from_path(registry_path, service_id="svc-demo-db", adapter="borgmatic")

    assert payload["service_count"] == 1
    assert payload["services"][0]["service_id"] == "svc-demo-db"
    assert payload["services"][0]["adapter"] == "borgmatic"



def test_backup_plan_cli_json_is_parseable_and_masks_sensitive_fields(tmp_path):
    registry_path = _write_registry(tmp_path)

    result = runner.invoke(
        app,
        [
            "backup",
            "plan",
            "--service-registry",
            str(registry_path),
            "--service-id",
            "svc-demo-web",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    rendered = result.stdout
    service = payload["services"][0]

    assert payload["service_count"] == 1
    assert service["service_id"] == "svc-demo-web"
    assert service["repository"] == "[REDACTED]"
    assert "super-secret" not in rendered
    assert "hunter2" not in rendered
    assert "restic-password" not in rendered
    assert "worker-secret" not in rendered
    assert "kopia-secret-token" not in rendered
    assert rendered.count("[REDACTED]") >= 1
