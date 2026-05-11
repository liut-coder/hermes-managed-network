from __future__ import annotations

import json
from pathlib import Path

from .service_registry import DEFAULT_SERVICE_REGISTRY_PATH, ServiceRegistry


def _first_domain(domains: list[str]) -> str | None:
    for domain in domains:
        normalized = domain.strip()
        if normalized:
            return normalized
    return None


def build_uptime_plan(registry: ServiceRegistry) -> dict[str, list[dict[str, object]]]:
    plan: dict[str, list[dict[str, object]]] = {
        "create": [],
        "update": [],
        "skip": [],
    }
    for service in registry.list_services():
        domain = _first_domain(service.domains)
        if domain:
            plan["create"].append(
                {
                    "service_id": service.service_id,
                    "name": service.name,
                    "monitor": {
                        "type": "http",
                        "name": f"{service.name} ({service.node})",
                        "url": f"https://{domain}",
                    },
                }
            )
            continue
        if service.ports:
            plan["create"].append(
                {
                    "service_id": service.service_id,
                    "name": service.name,
                    "monitor": {
                        "type": "tcp",
                        "name": f"{service.name} ({service.node})",
                        "host": service.node,
                        "port": service.ports[0],
                    },
                }
            )
            continue
        plan["skip"].append(
            {
                "service_id": service.service_id,
                "name": service.name,
                "reason": "missing domain and port",
            }
        )
    return plan


def build_uptime_plan_from_path(path: Path = DEFAULT_SERVICE_REGISTRY_PATH) -> dict[str, list[dict[str, object]]]:
    return build_uptime_plan(ServiceRegistry.load(path))


def render_uptime_plan_json(path: Path = DEFAULT_SERVICE_REGISTRY_PATH) -> str:
    return json.dumps(build_uptime_plan_from_path(path), ensure_ascii=False, indent=2, sort_keys=True)
