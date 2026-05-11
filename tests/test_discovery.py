import json

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.discovery import discover_services
from hermes_managed_network.inspect import ContainerRecord, NodeInventory, PortRecord
from hermes_managed_network.service_registry import ServiceRegistry


def _inventory(**overrides):
    defaults = dict(
        node="node-a",
        hostname="node-a.local",
        os_release="Debian",
        ports=[],
        containers=[],
        systemd_services=[],
        reverse_proxy_domains=[],
        reverse_proxy_mappings={},
        paths=[],
        warnings=[],
    )
    defaults.update(overrides)
    return NodeInventory(**defaults)


def test_discover_container_with_published_port_creates_service():
    inventory = _inventory(
        containers=[ContainerRecord(name="web", image="nginx:alpine", status="Up", ports=["0.0.0.0:8080->80/tcp"])]
    )

    registry = discover_services(inventory)
    service = registry.list_services()[0]

    assert service.service_id == "node-a:docker:web"
    assert service.kind == "docker"
    assert service.ports == [8080]
    assert service.runtime == "nginx:alpine"


def test_discover_caddy_domain_reverse_proxy_binds_domain_to_matching_container_port():
    inventory = _inventory(
        ports=[PortRecord(protocol="tcp", listen="127.0.0.1", port=5001, process="python")],
        containers=[ContainerRecord(name="app", image="demo/app", status="Up", ports=["127.0.0.1:5001->5001/tcp"])],
        reverse_proxy_domains=["app.example.com"],
        reverse_proxy_mappings={"app.example.com": 5001},
    )

    service = discover_services(inventory).list_services()[0]

    assert service.name == "app"
    assert service.domains == ["app.example.com"]
    assert service.ports == [5001]
    assert service.warnings == []


def test_discover_caddy_domain_reverse_proxy_binds_domain_to_unknown_listener_port():
    inventory = _inventory(
        ports=[PortRecord(protocol="tcp", listen="127.0.0.1", port=5001, process="python")],
        reverse_proxy_domains=["app.example.com"],
        reverse_proxy_mappings={"app.example.com": 5001},
    )

    service = discover_services(inventory).list_services()[0]

    assert service.name == "port-5001"
    assert service.kind == "unknown"
    assert service.domains == ["app.example.com"]
    assert service.ports == [5001]


def test_discover_unknown_public_port_emits_warning():
    inventory = _inventory(ports=[PortRecord(protocol="tcp", listen="0.0.0.0", port=2222, process="sshd")])

    services = discover_services(inventory).list_services()

    assert services[0].kind == "unknown"
    assert services[0].ports == [2222]
    assert "bare public port 2222 could not be attributed" in services[0].warnings


def test_cli_discover_services_reads_inventory_and_outputs_registry_json(tmp_path):
    inventory_path = tmp_path / "inventory.json"
    registry_path = tmp_path / "service-registry.json"
    inventory_path.write_text(
        json.dumps(
            _inventory(
                containers=[ContainerRecord(name="web", image="nginx", status="Up", ports=["0.0.0.0:8080->80/tcp"])]
            ).to_dict()
        )
    )

    result = CliRunner().invoke(
        app,
        ["discover", "services", "--inventory", str(inventory_path), "--output", str(registry_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["services"][0]["name"] == "web"
    assert ServiceRegistry.load(registry_path).list_services()[0].name == "web"
