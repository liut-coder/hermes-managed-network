import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.inspect import (
    ContainerRecord,
    NodeInventory,
    PortRecord,
    SystemdServiceRecord,
)


def test_useful_ops_smoke_dry_run_chain_generates_registry_docs_and_uptime_plan(monkeypatch, tmp_path):
    inventory_path = tmp_path / "inventory.json"
    registry_path = tmp_path / "service-registry.json"
    docs_dir = tmp_path / "docs"

    inventory = NodeInventory(
        node="demo-node",
        hostname="demo-node.local",
        os_release="Debian 12",
        ports=[
            PortRecord(protocol="tcp", listen="0.0.0.0", port=8080, process="docker-proxy"),
            PortRecord(protocol="tcp", listen="127.0.0.1", port=5001, process="python"),
        ],
        containers=[
            ContainerRecord(name="web", image="nginx:alpine", status="Up", ports=["0.0.0.0:8080->80/tcp"])
        ],
        systemd_services=[
            SystemdServiceRecord(name="demo.service", active="active", sub="running", description="Demo Service")
        ],
        reverse_proxy_domains=["app.example.com"],
        reverse_proxy_mappings={"app.example.com": 8080},
        paths=["/srv/demo"],
        warnings=["docker unavailable or not installed"],
    )
    monkeypatch.setattr("hermes_managed_network.cli.collect_local_inventory", lambda node="local": inventory)
    runner = CliRunner()

    inspect_result = runner.invoke(app, ["inspect", "node", "--local", "--output", str(inventory_path), "--json"])
    assert inspect_result.exit_code == 0, inspect_result.stdout
    assert inventory_path.exists()
    inventory_payload = json.loads(inventory_path.read_text())
    assert inventory_payload["node"] == "demo-node"
    assert inventory_payload["reverse_proxy_mappings"] == {"app.example.com": 8080}

    discover_result = runner.invoke(
        app,
        ["discover", "services", "--inventory", str(inventory_path), "--output", str(registry_path), "--json"],
    )
    assert discover_result.exit_code == 0, discover_result.stdout
    registry_payload = json.loads(discover_result.stdout)
    registry_file_payload = json.loads(registry_path.read_text())
    assert registry_file_payload == registry_payload
    services = {service["service_id"]: service for service in registry_payload["services"]}
    assert registry_path.exists()
    assert services["demo-node:docker:web"]["domains"] == ["app.example.com"]
    assert services["demo-node:docker:web"]["ports"] == [8080]
    assert services["demo-node:systemd:demo.service"]["kind"] == "systemd"

    docs_result = runner.invoke(
        app,
        ["docs", "generate", "--registry", str(registry_path), "--output-dir", str(docs_dir)],
    )
    assert docs_result.exit_code == 0
    assert (docs_dir / "service" / "web.md").exists()
    assert (docs_dir / "service" / "demo.md").exists()
    assert "app.example.com" in (docs_dir / "service" / "web.md").read_text()
    assert "demo.service" in (docs_dir / "service" / "demo.md").read_text()

    uptime_result = runner.invoke(app, ["uptime", "plan", "--service-registry", str(registry_path), "--json"])
    assert uptime_result.exit_code == 0
    uptime_payload = json.loads(uptime_result.stdout)
    assert uptime_payload["create"] == [
        {
            "service_id": "demo-node:docker:web",
            "name": "web",
            "monitor": {
                "type": "http",
                "name": "web (demo-node)",
                "url": "https://app.example.com",
            },
        }
    ]
    assert uptime_payload["update"] == []
    assert uptime_payload["skip"] == [
        {
            "service_id": "demo-node:systemd:demo.service",
            "name": "demo",
            "reason": "missing domain and port",
        }
    ]

    apply_result = runner.invoke(app, ["uptime", "sync", "--db", str(tmp_path / "hmn.db"), "--service-registry", str(registry_path)])
    assert apply_result.exit_code != 0
    assert "需要审批" in apply_result.stdout
    assert "未写入 Uptime Kuma" in apply_result.stdout
