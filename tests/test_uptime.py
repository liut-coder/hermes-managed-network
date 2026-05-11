import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.service_registry import ServiceRecord, ServiceRegistry


def test_uptime_plan_generates_http_tcp_and_skip_entries(tmp_path):
    registry_path = tmp_path / "service-registry.json"
    ServiceRegistry(
        [
            ServiceRecord(
                service_id="node-a:docker:web",
                name="web",
                node="node-a",
                kind="docker",
                domains=["app.example.com"],
                ports=[8080],
                runtime="nginx:alpine",
                source="container:web",
            ),
            ServiceRecord(
                service_id="node-b:docker:ssh",
                name="ssh",
                node="node-b",
                kind="docker",
                domains=[],
                ports=[2222],
                runtime="openssh",
                source="container:ssh",
            ),
            ServiceRecord(
                service_id="node-c:unknown:worker",
                name="worker",
                node="node-c",
                kind="unknown",
                domains=[],
                ports=[],
                runtime=None,
                source="inventory",
            ),
        ]
    ).save(registry_path)

    result = CliRunner().invoke(
        app,
        ["uptime", "plan", "--service-registry", str(registry_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["update"] == []

    assert payload["create"][0] == {
        "service_id": "node-a:docker:web",
        "name": "web",
        "monitor": {
            "type": "http",
            "name": "web (node-a)",
            "url": "https://app.example.com",
        },
    }
    assert payload["create"][1] == {
        "service_id": "node-b:docker:ssh",
        "name": "ssh",
        "monitor": {
            "type": "tcp",
            "name": "ssh (node-b)",
            "host": "node-b",
            "port": 2222,
        },
    }
    assert payload["skip"] == [
        {
            "service_id": "node-c:unknown:worker",
            "name": "worker",
            "reason": "missing domain and port",
        }
    ]


def test_uptime_plan_uses_domain_before_port_when_both_exist(tmp_path):
    registry_path = tmp_path / "service-registry.json"
    ServiceRegistry(
        [
            ServiceRecord(
                service_id="node-a:docker:api",
                name="api",
                node="node-a",
                kind="docker",
                domains=["api.example.com"],
                ports=[9000],
                runtime="api:latest",
                source="container:api",
            )
        ]
    ).save(registry_path)

    result = CliRunner().invoke(
        app,
        ["uptime", "plan", "--service-registry", str(registry_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["create"] == [
        {
            "service_id": "node-a:docker:api",
            "name": "api",
            "monitor": {
                "type": "http",
                "name": "api (node-a)",
                "url": "https://api.example.com",
            },
        }
    ]
    assert payload["skip"] == []
    assert payload["update"] == []
