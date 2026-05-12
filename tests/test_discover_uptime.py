import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app


def test_discover_from_json_imports_service_records(tmp_path):
    db = tmp_path / "hmn.db"
    manifest = tmp_path / "services.json"
    manifest.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "service_id": "svc_blog",
                        "name": "Blog",
                        "node_id": "node1",
                        "kind": "docker",
                        "runtime": "compose",
                        "domains": ["blog.example.invalid"],
                        "ports": [443],
                        "deploy_path": "/srv/blog",
                        "health_check_url": "https://blog.example.invalid/healthz",
                        "monitor_enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["discover", "--db", str(db), "--from-json", str(manifest)])

    assert result.exit_code == 0
    assert "discovered services: 1" in result.stdout
    shown = CliRunner().invoke(app, ["service", "show", "svc_blog", "--db", str(db)])
    assert shown.exit_code == 0
    assert "domains: blog.example.invalid" in shown.stdout


def test_docs_service_from_registry_writes_service_doc(tmp_path):
    db = tmp_path / "hmn.db"
    service_root = tmp_path / "service-docs"
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "service",
            "add",
            "svc_blog",
            "--db",
            str(db),
            "--name",
            "Blog",
            "--node",
            "node1",
            "--domain",
            "blog.example.invalid",
            "--port",
            "443",
            "--health-check-url",
            "https://blog.example.invalid/healthz",
        ],
    )

    result = runner.invoke(
        app,
        ["docs", "service", "svc_blog", "--db", str(db), "--service-root", str(service_root), "--from-registry"],
    )

    assert result.exit_code == 0
    doc = service_root / "svc_blog" / "README.md"
    assert doc.exists()
    content = doc.read_text(encoding="utf-8")
    assert "# Blog" in content
    assert "node1" in content
    assert "https://blog.example.invalid/healthz" in content


def test_uptime_plan_outputs_dry_run_monitor_plan_from_registry(tmp_path):
    db = tmp_path / "hmn.db"
    runner = CliRunner()
    runner.invoke(
        app,
        [
            "service",
            "add",
            "svc_blog",
            "--db",
            str(db),
            "--name",
            "Blog",
            "--domain",
            "blog.example.invalid",
            "--health-check-url",
            "https://blog.example.invalid/healthz",
            "--monitor-enabled",
        ],
    )

    result = runner.invoke(app, ["uptime", "plan", "--db", str(db), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["provider"] == "uptime-kuma"
    assert payload["monitors"] == [
        {
            "service_id": "svc_blog",
            "name": "Blog",
            "type": "http",
            "url": "https://blog.example.invalid/healthz",
            "tags": ["hmn", "service:svc_blog"],
        }
    ]
