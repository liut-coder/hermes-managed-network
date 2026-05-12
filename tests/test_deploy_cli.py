import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.service_registry import ServiceRecord, ServiceRegistry


runner = CliRunner()


def _registry_payload() -> ServiceRegistry:
    return ServiceRegistry(
        [
            ServiceRecord(
                service_id="svc-demo-web",
                name="demo-web",
                node="edge-01",
                kind="docker",
                domains=["demo.example.com"],
                ports=[80, 443],
                runtime="nginx:alpine",
                source="discovery:demo",
                docs_path="service/demo-web.md",
                monitor={
                    "providers": {
                        "github_actions": {
                            "fixture": "github-actions.json",
                        },
                        "coolify": {
                            "fixture": "coolify.json",
                        },
                        "uptime": {
                            "enabled": True,
                        },
                    }
                },
                warnings=[],
            ),
            ServiceRecord(
                service_id="svc-demo-worker",
                name="demo-worker",
                node="edge-01",
                kind="worker",
                domains=[],
                ports=[],
                runtime="python app.py",
                source="discovery:demo",
                docs_path="service/demo-worker.md",
                monitor={
                    "providers": {
                        "github_actions": {
                            "fixture": "missing-github-actions.json",
                        }
                    },
                    "api_key": "worker-secret-key",
                },
                warnings=["missing ingress"],
            ),
        ]
    )


def _github_actions_fixture() -> dict[str, object]:
    return {
        "repo": {
            "owner": "example",
            "name": "demo-app",
            "full_name": "example/demo-app",
        },
        "workflow": {
            "name": "Deploy Demo",
            "path": ".github/workflows/deploy.yml",
        },
        "ref": "refs/heads/main",
        "head_sha": "0123456789abcdef0123456789abcdef01234567",
        "inputs": {
            "environment": "production",
            "api_token": "secret-input-token",
        },
        "runs": [
            {
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/example/demo-app/actions/runs/9001?token=super-secret",
                "head_sha": "0123456789abcdef0123456789abcdef01234567",
            }
        ],
        "jobs": [{"name": "deploy", "status": "completed", "conclusion": "success"}],
        "checks": [{"name": "deploy / smoke", "status": "completed", "conclusion": "success"}],
    }


def _coolify_fixture() -> dict[str, object]:
    return {
        "server": {"name": "edge-01"},
        "project": {"uuid": "project-1", "name": "demo-project"},
        "environment": {"name": "production"},
        "applications": [
            {
                "uuid": "app-1",
                "name": "demo-web",
                "status": "running",
                "domains": [{"domain": "demo.example.com"}],
                "ports": [80, 443],
                "git_repository": "https://github.com/example/demo-web.git",
                "git_branch": "main",
                "deploy_target": {
                    "name": "edge-01-docker",
                    "authorization": "Bearer super-secret-token",
                },
                "env": {
                    "APP_ENV": "prod",
                    "API_KEY": "secret-api-key",
                },
            }
        ],
    }


def _write_registry_and_fixtures(tmp_path):
    registry_path = tmp_path / "service-registry.json"
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    _registry_payload().save(registry_path)
    (fixture_dir / "github-actions.json").write_text(json.dumps(_github_actions_fixture()), encoding="utf-8")
    (fixture_dir / "coolify.json").write_text(json.dumps(_coolify_fixture()), encoding="utf-8")
    return registry_path, fixture_dir


def test_deploy_plan_outputs_service_level_plan_from_registry(tmp_path):
    registry_path, fixture_dir = _write_registry_and_fixtures(tmp_path)

    result = runner.invoke(
        app,
        [
            "deploy",
            "plan",
            "--service-registry",
            str(registry_path),
            "--provider-fixture-dir",
            str(fixture_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["mode"] == "plan"
    assert payload["service_count"] == 2
    service = payload["services"][0]
    assert service["service_id"] == "svc-demo-web"
    assert service["host"] == "edge-01"
    assert service["ports"] == [80, 443]
    assert service["domains"] == ["demo.example.com"]
    assert service["docs_path"] == "service/demo-web.md"
    assert service["providers"]["github_actions"]["plan"]["provider"] == "github-actions"
    assert service["providers"]["coolify"]["plan"]["provider"] == "coolify"
    assert service["providers"]["uptime"]["plan"]["monitor"]["url"] == "https://demo.example.com"



def test_deploy_status_aggregates_provider_fixture_status(tmp_path):
    registry_path, fixture_dir = _write_registry_and_fixtures(tmp_path)

    result = runner.invoke(
        app,
        [
            "deploy",
            "status",
            "--service-registry",
            str(registry_path),
            "--provider-fixture-dir",
            str(fixture_dir),
            "--service-id",
            "svc-demo-web",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["mode"] == "status"
    assert payload["service_count"] == 1
    service = payload["services"][0]
    assert service["service_id"] == "svc-demo-web"
    assert service["providers"]["github_actions"]["status"]["deployment_record"]["status"] == "success"
    assert service["providers"]["coolify"]["status"]["service"]["service_id"] == "coolify:edge-01:app-1"
    assert service["providers"]["uptime"]["status"]["monitor"]["type"] == "http"



def test_deploy_plan_apply_and_rollback_are_approval_gate_skeletons(tmp_path):
    registry_path, fixture_dir = _write_registry_and_fixtures(tmp_path)

    result = runner.invoke(
        app,
        [
            "deploy",
            "plan",
            "--service-registry",
            str(registry_path),
            "--provider-fixture-dir",
            str(fixture_dir),
            "--service-id",
            "svc-demo-web",
            "--json",
        ],
    )

    payload = json.loads(result.stdout)
    actions = payload["services"][0]["actions"]
    assert actions["apply"] == {
        "approval_required": True,
        "not_executed": True,
        "machine_changed": False,
    }
    assert actions["rollback"] == {
        "approval_required": True,
        "not_executed": True,
        "machine_changed": False,
    }



def test_deploy_status_missing_provider_fixture_returns_warning_instead_of_failure(tmp_path):
    registry_path, fixture_dir = _write_registry_and_fixtures(tmp_path)

    result = runner.invoke(
        app,
        [
            "deploy",
            "status",
            "--service-registry",
            str(registry_path),
            "--provider-fixture-dir",
            str(fixture_dir),
            "--service-id",
            "svc-demo-worker",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    service = payload["services"][0]
    github_status = service["providers"]["github_actions"]
    assert github_status["status"] is None
    assert "missing provider fixture" in github_status["warnings"][0]



def test_deploy_outputs_mask_sensitive_fields(tmp_path):
    registry_path, fixture_dir = _write_registry_and_fixtures(tmp_path)

    result = runner.invoke(
        app,
        [
            "deploy",
            "status",
            "--service-registry",
            str(registry_path),
            "--provider-fixture-dir",
            str(fixture_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    rendered = result.stdout
    assert "secret-input-token" not in rendered
    assert "super-secret" not in rendered
    assert "super-secret-token" not in rendered
    assert "secret-api-key" not in rendered
    assert "worker-secret-key" not in rendered
    assert "[REDACTED]" in rendered



def test_deploy_cli_json_output_is_parseable(tmp_path):
    registry_path, fixture_dir = _write_registry_and_fixtures(tmp_path)

    plan_result = runner.invoke(
        app,
        [
            "deploy",
            "plan",
            "--service-registry",
            str(registry_path),
            "--provider-fixture-dir",
            str(fixture_dir),
            "--json",
        ],
    )
    status_result = runner.invoke(
        app,
        [
            "deploy",
            "status",
            "--service-registry",
            str(registry_path),
            "--provider-fixture-dir",
            str(fixture_dir),
            "--json",
        ],
    )

    assert json.loads(plan_result.stdout)["mode"] == "plan"
    assert json.loads(status_result.stdout)["mode"] == "status"
