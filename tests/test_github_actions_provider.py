import json

import pytest

from hermes_managed_network.github_actions_provider import (
    GitHubActionsConfig,
    GitHubActionsProvider,
    build_github_actions_dispatch_plan,
    build_github_actions_status,
)


def _fixture_payload() -> dict[str, object]:
    return {
        "repo": {
            "owner": "example",
            "name": "demo-app",
            "full_name": "example/demo-app",
            "private": True,
            "html_url": "https://github.com/example/demo-app",
        },
        "workflow": {
            "id": 101,
            "name": "Deploy Demo",
            "path": ".github/workflows/deploy.yml",
            "state": "active",
        },
        "ref": "refs/heads/main",
        "head_sha": "0123456789abcdef0123456789abcdef01234567",
        "inputs": {
            "environment": "production",
            "image_tag": "sha-0123456",
            "api_token": "secret-input-token",
        },
        "runs": [
            {
                "id": 9001,
                "name": "Deploy Demo",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/example/demo-app/actions/runs/9001?token=super-secret",
                "head_sha": "0123456789abcdef0123456789abcdef01234567",
                "event": "workflow_dispatch",
                "created_at": "2026-05-11T09:00:00Z",
                "updated_at": "2026-05-11T09:03:00Z",
            }
        ],
        "jobs": [
            {
                "id": 3001,
                "name": "deploy",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-05-11T09:00:10Z",
                "completed_at": "2026-05-11T09:02:59Z",
            }
        ],
        "checks": [
            {
                "id": 4001,
                "name": "deploy / smoke",
                "status": "completed",
                "conclusion": "success",
                "details_url": "https://github.com/example/demo-app/runs/4001?auth=secret",
            }
        ],
    }


def test_github_actions_config_normalizes_repo_and_masks_token():
    config = GitHubActionsConfig(
        repo=" /example/demo-app/ ",
        workflow=" deploy.yml ",
        token="ghp_super_secret_token",
        ref="refs/heads/main",
    )

    assert config.repo == "example/demo-app"
    assert config.workflow == "deploy.yml"
    assert config.ref == "main"
    assert config.sanitized() == {
        "repo": "example/demo-app",
        "workflow": "deploy.yml",
        "ref": "main",
        "token": "[REDACTED]",
    }



def test_github_actions_config_rejects_invalid_values():
    with pytest.raises(ValueError, match="owner/name"):
        GitHubActionsConfig(repo="demo-app", workflow="deploy.yml", token="x")

    with pytest.raises(ValueError, match="workflow is required"):
        GitHubActionsConfig(repo="example/demo-app", workflow="   ", token="x")

    with pytest.raises(ValueError, match="token is required"):
        GitHubActionsConfig(repo="example/demo-app", workflow="deploy.yml", token="   ")


def test_build_github_actions_status_reads_fixture_and_returns_sanitized_deployment_record(tmp_path):
    fixture_path = tmp_path / "github-actions.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    result = build_github_actions_status(
        fixture_path,
        service_id="svc-demo-web",
        source_label="github-actions-fixture",
    )

    assert result["provider"] == "github-actions"
    assert result["mode"] == "status"
    assert result["write"] is False
    assert result["source"] == f"github-actions-fixture:{fixture_path}"
    assert result["repo"] == "example/demo-app"
    assert result["workflow"] == "deploy.yml"
    assert result["ref"] == "main"
    assert result["run"]["status"] == "completed"
    assert result["run"]["conclusion"] == "success"
    assert result["checks"][0]["name"] == "deploy / smoke"
    assert result["jobs"][0]["name"] == "deploy"

    record = result["deployment_record"]
    assert record["service_id"] == "svc-demo-web"
    assert record["repo"] == "example/demo-app"
    assert record["workflow"] == "deploy.yml"
    assert record["ref"] == "main"
    assert record["status"] == "success"
    assert record["run_url"] == "https://github.com/example/demo-app/actions/runs/9001?[REDACTED]"
    assert record["commit_sha"] == "0123456789ab…01234567"
    assert record["source"] == f"github-actions-fixture:{fixture_path}"

    rendered = json.dumps(result, ensure_ascii=False)
    assert "ghp_super_secret_token" not in rendered
    assert "secret-input-token" not in rendered
    assert "super-secret" not in rendered
    assert "auth=secret" not in rendered
    assert "[REDACTED]" in rendered


def test_build_github_actions_dispatch_plan_is_dry_run_and_requires_approval(tmp_path):
    fixture_path = tmp_path / "github-actions.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    result = build_github_actions_dispatch_plan(
        fixture_path,
        service_id="svc-demo-web",
        requested_by="hmn",
        source_label="github-actions-fixture",
        inputs={
            "environment": "production",
            "release_token": "token-value",
        },
    )

    assert result["provider"] == "github-actions"
    assert result["mode"] == "dispatch-plan"
    assert result["write"] is False
    assert result["approval_required"] is True
    assert result["executed"] is False
    assert result["result"] == {
        "approval_required": True,
        "not_executed": True,
        "machine_changed": False,
    }
    assert result["plan"]["action"] == "workflow_dispatch"
    assert result["plan"]["repo"] == "example/demo-app"
    assert result["plan"]["workflow"] == "deploy.yml"
    assert result["plan"]["ref"] == "main"
    assert result["plan"]["inputs"]["environment"] == "production"
    assert result["plan"]["inputs"]["release_token"] == "[REDACTED]"
    assert result["plan"]["requested_by"] == "hmn"
    assert result["deployment_record"]["status"] == "approval_required"
    assert result["deployment_record"]["run_url"] is None
    assert result["deployment_record"]["commit_sha"] == "0123456789ab…01234567"

    rendered = json.dumps(result, ensure_ascii=False)
    assert "token-value" not in rendered
    assert "secret-input-token" not in rendered


def test_provider_status_wraps_builder_result(tmp_path):
    fixture_path = tmp_path / "github-actions.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    provider = GitHubActionsProvider(
        GitHubActionsConfig(
            repo="example/demo-app",
            workflow="deploy.yml",
            token="ghp_secret_token",
            ref="main",
        )
    )

    result = provider.status(fixture_path, service_id="svc-demo-web")

    assert result["provider"] == "github-actions"
    assert result["mode"] == "status"
    assert result["deployment_record"]["service_id"] == "svc-demo-web"



def test_provider_dispatch_returns_skeleton_without_transport(tmp_path):
    fixture_path = tmp_path / "github-actions.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    provider = GitHubActionsProvider(
        GitHubActionsConfig(
            repo="example/demo-app",
            workflow="deploy.yml",
            token="ghp_secret_token",
            ref="main",
        )
    )

    result = provider.dispatch_plan(
        fixture_path,
        service_id="svc-demo-web",
        requested_by="hmn",
        inputs={"release_token": "token-value"},
    )

    assert result["approval_required"] is True
    assert result["executed"] is False
    assert result["result"]["not_executed"] is True
    assert result["plan"]["dry_run"] is True
    assert result["plan"]["mutating"] is False
    assert result["plan"]["inputs"]["release_token"] == "[REDACTED]"



def test_provider_rejects_fixture_mismatch(tmp_path):
    fixture_path = tmp_path / "github-actions.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    provider = GitHubActionsProvider(
        GitHubActionsConfig(
            repo="example/other-app",
            workflow="deploy.yml",
            token="ghp_secret_token",
            ref="main",
        )
    )

    with pytest.raises(ValueError, match="fixture repo mismatch"):
        provider.status(fixture_path, service_id="svc-demo-web")



def test_dispatch_plan_inputs_override_fixture_values(tmp_path):
    fixture_path = tmp_path / "github-actions.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    result = build_github_actions_dispatch_plan(
        fixture_path,
        service_id="svc-demo-web",
        requested_by="hmn",
        source_label="github-actions-fixture",
        inputs={
            "environment": "staging",
            "image_tag": "sha-override",
        },
    )

    assert result["plan"]["inputs"]["environment"] == "staging"
    assert result["plan"]["inputs"]["image_tag"] == "sha-override"
    assert result["plan"]["inputs"]["api_token"] == "[REDACTED]"



def test_status_raises_for_invalid_json_fixture(tmp_path):
    fixture_path = tmp_path / "github-actions-invalid.json"
    fixture_path.write_text("{not-json}", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        build_github_actions_status(
            fixture_path,
            service_id="svc-demo-web",
            source_label="github-actions-fixture",
        )



def test_status_handles_missing_optional_fields_with_safe_defaults(tmp_path):
    fixture_path = tmp_path / "github-actions-minimal.json"
    fixture_path.write_text(
        json.dumps(
            {
                "repo": {"owner": "example", "name": "demo-app"},
                "workflow": {"name": "Deploy Demo"},
                "runs": [],
            }
        ),
        encoding="utf-8",
    )

    result = build_github_actions_status(
        fixture_path,
        service_id="svc-demo-web",
        source_label="github-actions-fixture",
    )

    assert result["repo"] == "example/demo-app"
    assert result["workflow"] == "Deploy Demo"
    assert result["ref"] == "main"
    assert result["run"]["status"] == "unknown"
    assert result["run"]["html_url"] is None
    assert result["deployment_record"]["status"] == "unknown"
    assert result["deployment_record"]["commit_sha"] == "unknown"
    assert result["checks"] == []
    assert result["jobs"] == []
