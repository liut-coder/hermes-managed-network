from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .inventory import Node
from .providers import redact_sensitive_data
from .storage import ServiceRecord


def _node_host(node: Node) -> str:
    if node.ssh_host:
        return node.ssh_host
    if node.addresses:
        return node.addresses[0]
    return node.hostname


def _service_summary(service: ServiceRecord) -> dict[str, Any]:
    return {
        "service_id": service.service_id,
        "name": service.name,
        "node_id": service.node_id,
        "kind": service.kind,
        "runtime": service.runtime,
        "domains": list(service.domains),
        "ports": list(service.ports),
        "source": service.source,
        "status": service.status,
        "docs_path": service.docs_path,
        "monitor_enabled": service.monitor_enabled,
        "health_check_url": service.health_check_url,
        "metadata": redact_sensitive_data(dict(service.metadata)),
    }


def plan_config_inventory_export(*, nodes: Iterable[Node], services: Iterable[ServiceRecord]) -> dict[str, Any]:
    node_list = sorted(list(nodes), key=lambda item: item.node_id)
    service_list = sorted(list(services), key=lambda item: item.service_id)
    service_ids_by_node: dict[str, list[str]] = defaultdict(list)
    grouped_services: dict[str, list[ServiceRecord]] = defaultdict(list)

    for service in service_list:
        if service.node_id:
            service_ids_by_node[service.node_id].append(service.service_id)
        grouped_services[service.service_id].append(service)

    inventory_by_node = {
        node.node_id: {
            "node_id": node.node_id,
            "hostname": node.hostname,
            "host": _node_host(node),
            "addresses": list(node.addresses),
            "ssh_user": node.ssh_user,
            "ssh_port": node.ssh_port,
            "labels": list(node.labels),
            "status": node.status,
            "trust_level": node.trust_level,
            "services": sorted(service_ids_by_node.get(node.node_id, [])),
        }
        for node in node_list
    }

    inventory_by_service = {}
    for service in service_list:
        inventory_by_service[service.service_id] = {
            "service_id": service.service_id,
            "nodes": [service.node_id] if service.node_id else [],
            "service": _service_summary(service),
        }

    return {
        "provider_id": "config-provider",
        "display_name": "Config Provider",
        "operation": "inventory_export_plan",
        "intent": "dry_run_inventory_export",
        "mutating": False,
        "risk": "low",
        "dry_run": True,
        "approval_required": False,
        "summary": f"dry-run inventory export prepared for {len(node_list)} nodes and {len(service_list)} services",
        "capabilities": {
            "inventory_export": {"enabled": True, "mode": "dry_run"},
            "apply": {"enabled": False, "status": "not_enabled", "approval_required": True},
            "playbook_execution": {"enabled": False, "status": "not_enabled", "approval_required": True},
        },
        "inventory": {
            "by_node": inventory_by_node,
            "by_service": inventory_by_service,
        },
    }
