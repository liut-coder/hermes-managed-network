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
    for service in sorted(registry.list_services(), key=lambda item: (item.node, item.name, item.service_id)):
        monitor = dict(service.monitor or {}) if isinstance(service.monitor, dict) else {}
        metadata = dict(monitor.get("metadata") or {}) if isinstance(monitor.get("metadata"), dict) else {}
        exposure = dict(metadata.get("exposure") or {}) if isinstance(metadata.get("exposure"), dict) else {}
        scope = str(exposure.get("scope") or "").strip().lower()
        if scope == "internal-only":
            plan["skip"].append({"service_id": service.service_id, "name": service.name, "reason": "internal-only service"})
            continue
        if scope == "status-page":
            plan["skip"].append({"service_id": service.service_id, "name": service.name, "reason": "status page service"})
            continue

        strategy = str(monitor.get("strategy") or "").strip().lower()
        keyword = str(monitor.get("keyword") or "").strip()
        health_check_url = str(metadata.get("health_check_url") or "").strip()
        domain = _first_domain(service.domains)

        if strategy == "ping":
            plan["create"].append(
                {
                    "service_id": service.service_id,
                    "name": service.name,
                    "monitor": {
                        "type": "ping",
                        "name": f"{service.name} ({service.node})",
                        "host": service.node,
                    },
                }
            )
            continue
        if keyword and (health_check_url or domain):
            target_url = health_check_url or f"https://{domain}"
            plan["create"].append(
                {
                    "service_id": service.service_id,
                    "name": service.name,
                    "monitor": {
                        "type": "keyword",
                        "name": f"{service.name} ({service.node})",
                        "url": target_url,
                        "keyword": keyword,
                    },
                }
            )
            continue
        if domain:
            plan["create"].append(
                {
                    "service_id": service.service_id,
                    "name": service.name,
                    "monitor": {
                        "type": "http",
                        "name": f"{service.name} ({service.node})",
                        "url": health_check_url or f"https://{domain}",
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
    monitor_priority = {"keyword": 0, "http": 0, "tcp": 1, "ping": 2}
    plan["create"].sort(
        key=lambda item: monitor_priority.get(
            str((item.get("monitor") if isinstance(item, dict) else {}).get("type") or ""),
            99,
        )
    )
    return plan


def build_uptime_plan_from_path(path: Path = DEFAULT_SERVICE_REGISTRY_PATH) -> dict[str, list[dict[str, object]]]:
    return build_uptime_plan(ServiceRegistry.load(path))


def render_uptime_plan_json(path: Path = DEFAULT_SERVICE_REGISTRY_PATH) -> str:
    return json.dumps(build_uptime_plan_from_path(path), ensure_ascii=False, indent=2, sort_keys=True)
