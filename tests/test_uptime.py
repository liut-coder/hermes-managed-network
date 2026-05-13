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



def test_uptime_plan_auto_selects_http_keyword_tcp_and_ping_without_exposing_internal_only_services(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_service_record(
        StorageServiceRecord(
            service_id="svc_web",
            name="web",
            node_id="node-a",
            kind="docker",
            domains=["app.example.com"],
            ports=[8080],
            health_check_url="https://app.example.com/readyz",
            monitor_enabled=True,
            metadata={"monitor": {"keyword": "ready"}},
        )
    )
    store.save_service_record(
        StorageServiceRecord(
            service_id="svc_tcp",
            name="smtp",
            node_id="node-b",
            kind="systemd",
            ports=[25],
            monitor_enabled=True,
            metadata={},
        )
    )
    store.save_service_record(
        StorageServiceRecord(
            service_id="svc_ping",
            name="backup-host",
            node_id="node-c",
            kind="host",
            monitor_enabled=True,
            metadata={"monitor": {"strategy": "ping"}},
        )
    )
    store.save_service_record(
        StorageServiceRecord(
            service_id="svc_internal",
            name="internal-api",
            node_id="node-d",
            kind="docker",
            ports=[9000],
            monitor_enabled=True,
            metadata={"exposure": {"scope": "internal-only"}},
        )
    )
    store.save_service_record(
        StorageServiceRecord(
            service_id="svc_status",
            name="hmn-status",
            node_id="node-e",
            kind="docker",
            domains=["status.example.com"],
            ports=[3001],
            monitor_enabled=True,
            metadata={"exposure": {"scope": "status-page"}},
        )
    )

    result = CliRunner().invoke(app, ["uptime", "plan", "--db", str(db), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["create"] == [
        {
            "service_id": "svc_web",
            "name": "web",
            "monitor": {
                "type": "keyword",
                "name": "web (node-a)",
                "url": "https://app.example.com/readyz",
                "keyword": "ready",
            },
        },
        {
            "service_id": "svc_tcp",
            "name": "smtp",
            "monitor": {
                "type": "tcp",
                "name": "smtp (node-b)",
                "host": "node-b",
                "port": 25,
            },
        },
        {
            "service_id": "svc_ping",
            "name": "backup-host",
            "monitor": {
                "type": "ping",
                "name": "backup-host (node-c)",
                "host": "node-c",
            },
        },
    ]
    assert payload["skip"] == [
        {"service_id": "svc_internal", "name": "internal-api", "reason": "internal-only service"},
        {"service_id": "svc_status", "name": "hmn-status", "reason": "status page service"},
    ]
    assert payload["update"] == []
