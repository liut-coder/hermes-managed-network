import json

from hermes_managed_network.coolify_provider import (
    CoolifyApplicationSpec,
    CoolifyConfig,
    CoolifyProvider,
    build_coolify_sync_dry_run,
    discover_coolify_services_from_fixture,
    sync_coolify_registry_from_fixture,
)


def test_coolify_config_normalizes_base_url_and_exposes_api_root():
    config = CoolifyConfig(
        base_url="https://coolify.example.com///",
        api_token="secret-token",
        project_uuid="project-1",
        environment_name="production",
    )

    assert config.base_url == "https://coolify.example.com"
    assert config.api_root == "https://coolify.example.com/api/v1"



def test_coolify_provider_builds_bearer_auth_headers_and_payload():
    provider = CoolifyProvider(
        CoolifyConfig(
            base_url="https://coolify.example.com",
            api_token="secret-token",
            project_uuid="project-1",
            environment_name="production",
        )
    )
    spec = CoolifyApplicationSpec(
        name="demo-app",
        git_repository="https://github.com/example/demo.git",
        git_branch="main",
        domains=["demo.example.com"],
        ports=[3000],
        env={"APP_ENV": "prod"},
    )

    assert provider.headers == {
        "Authorization": "Bearer secret-token",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    assert provider.build_application_payload(spec) == {
        "name": "demo-app",
        "project_uuid": "project-1",
        "environment_name": "production",
        "source": {
            "type": "git",
            "repository": "https://github.com/example/demo.git",
            "branch": "main",
        },
        "domains": ["demo.example.com"],
        "ports": [3000],
        "env": {"APP_ENV": "prod"},
    }



def test_coolify_provider_request_uses_injected_transport_with_normalized_url():
    calls = []

    def transport(method: str, url: str, *, headers: dict[str, str], json: dict | None = None):
        calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return {"ok": True}

    provider = CoolifyProvider(
        CoolifyConfig(
            base_url="https://coolify.example.com/",
            api_token="secret-token",
            project_uuid="project-1",
            environment_name="production",
        ),
        transport=transport,
    )

    response = provider.request("GET", "/applications")

    assert response == {"ok": True}
    assert calls == [
        {
            "method": "GET",
            "url": "https://coolify.example.com/api/v1/applications",
            "headers": provider.headers,
            "json": None,
        }
    ]



def _fixture_payload() -> dict[str, object]:
    return {
        "server": {"name": "edge-01"},
        "project": {"uuid": "project-1", "name": "demo-project"},
        "environment": {"name": "production"},
        "applications": [
            {
                "uuid": "app-1",
                "name": "demo-web",
                "status": "running",
                "domains": [
                    {"domain": "demo.example.com"},
                    {"domain": "www.demo.example.com"},
                ],
                "ports": [80, "443"],
                "git_repository": "https://user:repo-token@github.com/example/demo-web.git",
                "git_branch": "main",
                "deploy_target": {
                    "name": "edge-01-docker",
                    "destination": "root@10.0.0.8:/srv/demo",
                    "authorization": "Bearer super-secret-token",
                },
                "env": {
                    "APP_ENV": "prod",
                    "API_KEY": "secret-api-key",
                    "DATABASE_PASSWORD": "super-secret-password",
                    "AUTHORIZATION": "Bearer top-secret",
                },
            },
            {
                "uuid": "app-2",
                "name": "worker",
                "status": "stopped",
                "domains": [],
                "ports": [],
                "git_repository": "https://github.com/example/worker.git",
                "git_branch": "develop",
                "deploy_target": {"name": "edge-01-docker"},
                "env": {"REFRESH_TOKEN": "refresh-secret"},
            },
        ],
    }



def test_discover_coolify_services_from_fixture_maps_apps_to_registry(tmp_path):
    fixture_path = tmp_path / "coolify.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    registry = discover_coolify_services_from_fixture(fixture_path)
    services = registry.list_services()

    assert len(services) == 2

    web = services[0]
    assert web.service_id == "coolify:edge-01:app-1"
    assert web.name == "demo-web"
    assert web.node == "edge-01"
    assert web.kind == "coolify"
    assert web.domains == ["demo.example.com", "www.demo.example.com"]
    assert web.ports == [80, 443]
    assert web.runtime == "repo:[REDACTED]#main"
    assert web.source == f"coolify-fixture:{fixture_path}"
    assert web.monitor["status"] == "running"
    assert web.monitor["project"] == "demo-project"
    assert web.monitor["environment"] == "production"
    assert web.monitor["deploy_target"]["name"] == "edge-01-docker"
    assert web.monitor["env_summary"]["APP_ENV"] == "prod"
    assert web.monitor["env_summary"]["API_KEY"] == "[REDACTED]"
    assert web.monitor["env_summary"]["DATABASE_PASSWORD"] == "[REDACTED]"
    assert web.monitor["deploy_target"]["authorization"] == "[REDACTED]"
    assert web.warnings == []

    worker = services[1]
    assert worker.service_id == "coolify:edge-01:app-2"
    assert worker.monitor["env_summary"]["REFRESH_TOKEN"] == "[REDACTED]"
    assert "coolify app worker has no domains" in worker.warnings
    assert "coolify app worker has no ports" in worker.warnings
    assert "coolify app worker status=stopped" in worker.warnings



def test_build_coolify_sync_dry_run_returns_sanitized_summary(tmp_path):
    fixture_path = tmp_path / "coolify.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    result = build_coolify_sync_dry_run(fixture_path)

    assert result["provider"] == "coolify"
    assert result["mode"] == "dry-run"
    assert result["write"] is False
    assert result["service_count"] == 2
    assert result["source"] == f"coolify-fixture:{fixture_path}"

    services = result["services"]
    assert services[0]["name"] == "demo-web"
    assert services[0]["kind"] == "coolify"
    assert services[0]["monitor"]["env_summary"]["API_KEY"] == "[REDACTED]"
    assert services[0]["monitor"]["deploy_target"]["authorization"] == "[REDACTED]"

    rendered = json.dumps(result, ensure_ascii=False)
    assert "secret-api-key" not in rendered
    assert "super-secret-password" not in rendered
    assert "super-secret-token" not in rendered
    assert "top-secret" not in rendered
    assert "refresh-secret" not in rendered
    assert "repo-token" not in rendered
    assert "[REDACTED]" in rendered


def test_sync_coolify_registry_from_fixture_maps_to_hmn_storage_records_and_audits(tmp_path):
    fixture_path = tmp_path / "coolify.json"
    fixture_path.write_text(json.dumps(_fixture_payload()), encoding="utf-8")

    db = tmp_path / "hmn.db"
    from hermes_managed_network.storage import SQLiteStore

    store = SQLiteStore(db)

    payload = sync_coolify_registry_from_fixture(store, fixture_path, source_label="coolify-sync")

    assert payload["provider"] == "coolify"
    assert payload["mode"] == "apply"
    assert payload["write"] is True
    assert payload["service_count"] == 2
    assert payload["audit_action"] == "coolify_registry_sync"

    web = store.load_service_record("svc_edge-01_demo-web")
    assert web is not None
    assert web.kind == "coolify"
    assert web.node_id == "edge-01"
    assert web.domains == ["demo.example.com", "www.demo.example.com"]
    assert web.deploy_path == "root@10.0.0.8:/srv/demo"
    assert web.monitor_enabled is True
    assert web.status == "running"
    assert web.health_check_url == "https://demo.example.com"
    assert web.metadata["coolify"]["repo"] == "[REDACTED]"
    assert web.metadata["coolify"]["deploy_target"]["name"] == "edge-01-docker"
    assert web.metadata["env_summary"]["API_KEY"] == "[REDACTED]"
    assert web.metadata["service_instance"]["provider"] == "coolify"
    assert web.metadata["service_instance"]["instance_id"] == "app-1"

    worker = store.load_service_record("svc_edge-01_worker")
    assert worker is not None
    assert worker.monitor_enabled is False
    assert worker.health_check_url == ""
    assert worker.status == "stopped"
    assert worker.metadata["coolify"]["repo_branch"] == "develop"

    audits = store.list_audit_events()
    assert len(audits) == 1
    assert audits[0].action == "coolify_registry_sync"
    assert audits[0].subject_type == "provider"
    assert audits[0].subject_id == "coolify"
    assert audits[0].details["service_ids"] == ["svc_edge-01_demo-web", "svc_edge-01_worker"]
    assert audits[0].details["source"] == f"coolify-sync:{fixture_path}"
    rendered = json.dumps(audits[0].details, ensure_ascii=False)
    assert "secret-api-key" not in rendered
    assert "super-secret-password" not in rendered
    assert "repo-token" not in rendered
    assert "[REDACTED]" in rendered
