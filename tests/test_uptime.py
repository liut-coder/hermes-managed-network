import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.service_registry import ServiceRecord, ServiceRegistry
from hermes_managed_network.storage import SQLiteStore, ServiceRecord as StorageServiceRecord


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


def test_uptime_plan_db_services_generate_registry_style_http_tcp_and_skip_entries(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_service_record(
        StorageServiceRecord(
            service_id="node-db:docker:web",
            name="web",
            node_id="node-db",
            kind="docker",
            domains=["web.example.com"],
            ports=[8080],
            monitor_enabled=True,
            metadata={"providers": {"uptime": {"enabled": True}}},
        )
    )
    store.save_service_record(
        StorageServiceRecord(
            service_id="node-db:systemd:ssh",
            name="ssh",
            node_id="node-db",
            kind="systemd",
            ports=[2222],
            monitor_enabled=True,
            metadata={"providers": {"uptime": {"enabled": True}}},
        )
    )
    store.save_service_record(
        StorageServiceRecord(
            service_id="node-db:unknown:worker",
            name="worker",
            node_id="node-db",
            kind="unknown",
            monitor_enabled=True,
        )
    )

    result = CliRunner().invoke(app, ["uptime", "plan", "--db", str(db), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["update"] == []
    assert payload["create"] == [
        {
            "service_id": "node-db:docker:web",
            "name": "web",
            "monitor": {"type": "http", "name": "web (node-db)", "url": "https://web.example.com"},
        },
        {
            "service_id": "node-db:systemd:ssh",
            "name": "ssh",
            "monitor": {"type": "tcp", "name": "ssh (node-db)", "host": "node-db", "port": 2222},
        },
    ]
    assert payload["skip"] == [
        {"service_id": "node-db:unknown:worker", "name": "worker", "reason": "missing domain and port"}
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
