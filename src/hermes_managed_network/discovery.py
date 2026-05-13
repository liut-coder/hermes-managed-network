from __future__ import annotations

import re
from pathlib import Path

from .inspect import ContainerRecord, NodeInventory, PortRecord, inventory_from_json
from .service_registry import ServiceRecord, ServiceRegistry

SYSTEMD_EXCLUDE_PREFIXES = (
    "systemd-",
    "dbus.",
    "ssh.",
    "cron.",
    "rsyslog.",
    "getty@",
    "networking.",
)


def discover_services(inventory: NodeInventory) -> ServiceRegistry:
    registry = ServiceRegistry()
    claimed_ports: set[int] = set()

    for container in inventory.containers:
        service = _service_from_container(inventory, container)
        if not service.ports:
            service.warnings.append(f"docker container {container.name} has no published ports")
        registry.upsert(service)
        claimed_ports.update(service.ports)

    _bind_reverse_proxy_domains(registry, inventory)
    claimed_ports.update(port for service in registry.list_services() for port in service.ports)

    for systemd_service in inventory.systemd_services:
        if systemd_service.name.startswith(SYSTEMD_EXCLUDE_PREFIXES):
            continue
        service_id = f"{inventory.node}:systemd:{systemd_service.name}"
        registry.upsert(
            ServiceRecord(
                service_id=service_id,
                name=systemd_service.name.removesuffix(".service"),
                node=inventory.node,
                kind="systemd",
                domains=[],
                ports=[],
                runtime=systemd_service.name,
                source=f"systemd:{systemd_service.name}",
            )
        )

    for port in inventory.ports:
        if _is_public_listener(port) and port.port not in claimed_ports:
            registry.upsert(
                ServiceRecord(
                    service_id=f"{inventory.node}:unknown:port:{port.port}",
                    name=f"port-{port.port}",
                    node=inventory.node,
                    kind="unknown",
                    domains=[],
                    ports=[port.port],
                    runtime=port.process,
                    source="listener",
                    warnings=[f"bare public port {port.port} could not be attributed"],
                )
            )

    known_domains = {domain for service in registry.list_services() for domain in service.domains}
    for domain in inventory.reverse_proxy_domains:
        if domain not in known_domains:
            registry.upsert(
                ServiceRecord(
                    service_id=f"{inventory.node}:unknown:domain:{domain}",
                    name=domain,
                    node=inventory.node,
                    kind="unknown",
                    domains=[domain],
                    ports=[],
                    runtime=None,
                    source="reverse-proxy",
                    warnings=[f"domain {domain} could not be attributed to a service"],
                )
            )
    return registry


def discover_services_from_file(path: Path) -> ServiceRegistry:
    return discover_services(inventory_from_json(path.read_text()))


def _service_from_container(inventory: NodeInventory, container: ContainerRecord) -> ServiceRecord:
    host_ports = _container_host_ports(container)
    return ServiceRecord(
        service_id=f"{inventory.node}:docker:{container.name}",
        name=container.name,
        node=inventory.node,
        kind="docker",
        domains=[],
        ports=host_ports,
        runtime=container.image,
        source=f"container:{container.name}",
    )


def _bind_reverse_proxy_domains(registry: ServiceRegistry, inventory: NodeInventory) -> None:
    for domain, upstream_port in inventory.reverse_proxy_mappings.items():
        target = _find_service_by_port(registry, upstream_port)
        if target is None:
            target = _unknown_service_for_upstream_port(inventory, upstream_port)
        registry.upsert(
            ServiceRecord(
                service_id=target.service_id,
                name=target.name,
                node=target.node,
                kind=target.kind,
                domains=[domain],
                ports=[upstream_port],
                runtime=target.runtime,
                source=target.source,
            )
        )


def _unknown_service_for_upstream_port(inventory: NodeInventory, upstream_port: int) -> ServiceRecord:
    listener = next((port for port in inventory.ports if port.port == upstream_port), None)
    return ServiceRecord(
        service_id=f"{inventory.node}:unknown:port:{upstream_port}",
        name=f"port-{upstream_port}",
        node=inventory.node,
        kind="unknown",
        domains=[],
        ports=[upstream_port],
        runtime=listener.process if listener else None,
        source="reverse-proxy",
    )


def _find_service_by_port(registry: ServiceRegistry, port: int) -> ServiceRecord | None:
    for service in registry.list_services():
        if port in service.ports:
            return service
    return None


def _container_host_ports(container: ContainerRecord) -> list[int]:
    ports: set[int] = set()
    for mapping in container.ports:
        for match in re.finditer(r"(?:(?:\d+\.\d+\.\d+\.\d+|127\.0\.0\.1|0\.0\.0\.0|::):)?(?P<host>\d+)->\d+/(?:tcp|udp)", mapping):
            ports.add(int(match.group("host")))
    return sorted(ports)


def _is_public_listener(port: PortRecord) -> bool:
    return port.listen in {"0.0.0.0", "::", "*"}
