from __future__ import annotations

import json
from pathlib import Path

from .coolify_provider import build_coolify_sync_dry_run, discover_coolify_services_from_fixture
from .docs_generate import _sanitize_value
from .github_actions_provider import build_github_actions_dispatch_plan, build_github_actions_status
from .service_registry import DEFAULT_SERVICE_REGISTRY_PATH, ServiceRecord, ServiceRegistry
from .uptime import build_uptime_plan

SKELETON_ACTION_RESULT = {
    "approval_required": True,
    "not_executed": True,
    "machine_changed": False,
}


PROVIDER_ALIASES = {
    "github_actions": "github_actions",
    "github-actions": "github_actions",
    "github": "github_actions",
    "coolify": "coolify",
    "uptime": "uptime",
    "uptime_kuma": "uptime",
    "uptime-kuma": "uptime",
}


def build_deploy_plan_from_path(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    provider_fixture_dir: Path | None = None,
) -> dict[str, object]:
    registry = ServiceRegistry.load(path)
    return build_deploy_plan(registry, service_id=service_id, provider_fixture_dir=provider_fixture_dir)


def build_deploy_status_from_path(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    provider_fixture_dir: Path | None = None,
) -> dict[str, object]:
    registry = ServiceRegistry.load(path)
    return build_deploy_status(registry, service_id=service_id, provider_fixture_dir=provider_fixture_dir)


def render_deploy_plan_json(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    provider_fixture_dir: Path | None = None,
) -> str:
    payload = build_deploy_plan_from_path(path, service_id=service_id, provider_fixture_dir=provider_fixture_dir)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def render_deploy_status_json(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    provider_fixture_dir: Path | None = None,
) -> str:
    payload = build_deploy_status_from_path(path, service_id=service_id, provider_fixture_dir=provider_fixture_dir)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_deploy_plan(
    registry: ServiceRegistry,
    *,
    service_id: str | None = None,
    provider_fixture_dir: Path | None = None,
) -> dict[str, object]:
    services = _select_services(registry, service_id)
    payload_services = [_service_plan_payload(service, provider_fixture_dir=provider_fixture_dir) for service in services]
    return _top_level_payload(mode="plan", services=payload_services, source_registry=registry, service_id=service_id)


def build_deploy_status(
    registry: ServiceRegistry,
    *,
    service_id: str | None = None,
    provider_fixture_dir: Path | None = None,
) -> dict[str, object]:
    services = _select_services(registry, service_id)
    payload_services = [_service_status_payload(service, provider_fixture_dir=provider_fixture_dir) for service in services]
    return _top_level_payload(mode="status", services=payload_services, source_registry=registry, service_id=service_id)


def _top_level_payload(
    *,
    mode: str,
    services: list[dict[str, object]],
    source_registry: ServiceRegistry,
    service_id: str | None,
) -> dict[str, object]:
    source = _registry_source(source_registry)
    return _sanitize_value(
        {
            "mode": mode,
            "service_count": len(services),
            "service_id": service_id,
            "source": source,
            "services": services,
        }
    )


def _registry_source(registry: ServiceRegistry) -> str:
    services = registry.list_services()
    if not services:
        return "service-registry"
    first_source = services[0].source or "service-registry"
    return str(first_source)


def _select_services(registry: ServiceRegistry, service_id: str | None) -> list[ServiceRecord]:
    services = registry.list_services()
    if service_id is None:
        return services
    return [service for service in services if service.service_id == service_id]


def _service_plan_payload(service: ServiceRecord, *, provider_fixture_dir: Path | None) -> dict[str, object]:
    payload = _base_service_payload(service)
    payload["providers"] = _provider_plan_payloads(service, provider_fixture_dir=provider_fixture_dir)
    payload["actions"] = {
        "apply": dict(SKELETON_ACTION_RESULT),
        "rollback": dict(SKELETON_ACTION_RESULT),
    }
    return _sanitize_value(payload)


def _service_status_payload(service: ServiceRecord, *, provider_fixture_dir: Path | None) -> dict[str, object]:
    payload = _base_service_payload(service)
    payload["providers"] = _provider_status_payloads(service, provider_fixture_dir=provider_fixture_dir)
    payload["actions"] = {
        "apply": dict(SKELETON_ACTION_RESULT),
        "rollback": dict(SKELETON_ACTION_RESULT),
    }
    return _sanitize_value(payload)


def _base_service_payload(service: ServiceRecord) -> dict[str, object]:
    return {
        "service_id": service.service_id,
        "name": service.name,
        "host": service.node,
        "kind": service.kind,
        "domains": list(service.domains),
        "ports": list(service.ports),
        "docs_path": service.docs_path,
        "runtime": service.runtime,
        "source": service.source,
        "monitor": service.monitor,
        "warnings": list(service.warnings),
    }


def _provider_plan_payloads(service: ServiceRecord, *, provider_fixture_dir: Path | None) -> dict[str, object]:
    providers = _provider_specs(service)
    result: dict[str, object] = {}
    for provider_name, spec in providers.items():
        if provider_name == "github_actions":
            result[provider_name] = _github_actions_plan(service, spec, provider_fixture_dir=provider_fixture_dir)
        elif provider_name == "coolify":
            result[provider_name] = _coolify_plan(service, spec, provider_fixture_dir=provider_fixture_dir)
        elif provider_name == "uptime":
            result[provider_name] = _uptime_plan(service, spec)
    return result


def _provider_status_payloads(service: ServiceRecord, *, provider_fixture_dir: Path | None) -> dict[str, object]:
    providers = _provider_specs(service)
    result: dict[str, object] = {}
    for provider_name, spec in providers.items():
        if provider_name == "github_actions":
            result[provider_name] = _github_actions_status(service, spec, provider_fixture_dir=provider_fixture_dir)
        elif provider_name == "coolify":
            result[provider_name] = _coolify_status(service, spec, provider_fixture_dir=provider_fixture_dir)
        elif provider_name == "uptime":
            result[provider_name] = _uptime_status(service, spec)
    return result


def _provider_specs(service: ServiceRecord) -> dict[str, dict[str, object]]:
    monitor = service.monitor if isinstance(service.monitor, dict) else {}
    raw = monitor.get("providers")
    providers: dict[str, dict[str, object]] = {}
    if isinstance(raw, dict):
        for name, spec in raw.items():
            canonical = PROVIDER_ALIASES.get(str(name).strip().lower())
            if canonical is None:
                continue
            providers[canonical] = dict(spec) if isinstance(spec, dict) else {}
    provider_name = monitor.get("provider")
    if isinstance(provider_name, str):
        canonical = PROVIDER_ALIASES.get(provider_name.strip().lower())
        if canonical and canonical not in providers:
            providers[canonical] = {}
    return providers


def _github_actions_plan(service: ServiceRecord, spec: dict[str, object], *, provider_fixture_dir: Path | None) -> dict[str, object]:
    fixture_path = _resolve_fixture(spec, provider_fixture_dir)
    warnings: list[str] = []
    plan = None
    if fixture_path is None:
        warnings.append("missing provider fixture: github_actions")
    elif not fixture_path.exists():
        warnings.append(f"missing provider fixture: {fixture_path}")
    else:
        plan = build_github_actions_dispatch_plan(
            fixture_path,
            service_id=service.service_id,
            requested_by="hmn",
            source_label="github-actions-fixture",
        )
    return {
        "provider": "github_actions",
        "fixture": str(fixture_path) if fixture_path else None,
        "plan": plan,
        "warnings": warnings,
    }


def _github_actions_status(service: ServiceRecord, spec: dict[str, object], *, provider_fixture_dir: Path | None) -> dict[str, object]:
    fixture_path = _resolve_fixture(spec, provider_fixture_dir)
    warnings: list[str] = []
    status = None
    if fixture_path is None:
        warnings.append("missing provider fixture: github_actions")
    elif not fixture_path.exists():
        warnings.append(f"missing provider fixture: {fixture_path}")
    else:
        status = build_github_actions_status(
            fixture_path,
            service_id=service.service_id,
            source_label="github-actions-fixture",
        )
    return {
        "provider": "github_actions",
        "fixture": str(fixture_path) if fixture_path else None,
        "status": status,
        "warnings": warnings,
    }


def _coolify_plan(service: ServiceRecord, spec: dict[str, object], *, provider_fixture_dir: Path | None) -> dict[str, object]:
    fixture_path = _resolve_fixture(spec, provider_fixture_dir)
    warnings: list[str] = []
    plan = None
    if fixture_path is None:
        warnings.append("missing provider fixture: coolify")
    elif not fixture_path.exists():
        warnings.append(f"missing provider fixture: {fixture_path}")
    else:
        dry_run = build_coolify_sync_dry_run(fixture_path)
        matched = _match_coolify_service(discover_coolify_services_from_fixture(fixture_path), service)
        plan = {
            "provider": dry_run["provider"],
            "mode": dry_run["mode"],
            "source": dry_run["source"],
            "service": matched.to_dict() if matched is not None else None,
            "result": dict(SKELETON_ACTION_RESULT),
        }
        if matched is None:
            warnings.append(f"coolify fixture has no matching service for {service.service_id}")
    return {
        "provider": "coolify",
        "fixture": str(fixture_path) if fixture_path else None,
        "plan": plan,
        "warnings": warnings,
    }


def _coolify_status(service: ServiceRecord, spec: dict[str, object], *, provider_fixture_dir: Path | None) -> dict[str, object]:
    fixture_path = _resolve_fixture(spec, provider_fixture_dir)
    warnings: list[str] = []
    status = None
    if fixture_path is None:
        warnings.append("missing provider fixture: coolify")
    elif not fixture_path.exists():
        warnings.append(f"missing provider fixture: {fixture_path}")
    else:
        dry_run = build_coolify_sync_dry_run(fixture_path)
        registry = discover_coolify_services_from_fixture(fixture_path)
        matched = _match_coolify_service(registry, service)
        status = {
            "provider": "coolify",
            "mode": "status",
            "source": dry_run["source"],
            "service": matched.to_dict() if matched is not None else None,
            "service_count": dry_run["service_count"],
        }
        if matched is None:
            warnings.append(f"coolify fixture has no matching service for {service.service_id}")
    return {
        "provider": "coolify",
        "fixture": str(fixture_path) if fixture_path else None,
        "status": status,
        "warnings": warnings,
    }


def _uptime_plan(service: ServiceRecord, spec: dict[str, object]) -> dict[str, object]:
    monitor = _uptime_entry(service)
    warnings: list[str] = []
    if monitor is None:
        warnings.append(f"uptime plan unavailable for {service.service_id}")
    return {
        "provider": "uptime",
        "plan": {
            "provider": "uptime",
            "mode": "dry-run",
            "monitor": monitor,
            "result": dict(SKELETON_ACTION_RESULT),
        }
        if monitor is not None
        else None,
        "warnings": warnings,
    }


def _uptime_status(service: ServiceRecord, spec: dict[str, object]) -> dict[str, object]:
    monitor = _uptime_entry(service)
    warnings: list[str] = []
    if monitor is None:
        warnings.append(f"uptime status unavailable for {service.service_id}")
    return {
        "provider": "uptime",
        "status": {
            "provider": "uptime",
            "mode": "status",
            "monitor": monitor,
            "exists": monitor is not None,
        }
        if monitor is not None
        else None,
        "warnings": warnings,
    }


def _uptime_entry(service: ServiceRecord) -> dict[str, object] | None:
    registry = ServiceRegistry([service])
    plan = build_uptime_plan(registry)
    for action in ("create", "update"):
        for item in plan.get(action, []):
            if item.get("service_id") == service.service_id:
                return dict(item.get("monitor") or {})
    return None


def _resolve_fixture(spec: dict[str, object], provider_fixture_dir: Path | None) -> Path | None:
    raw = spec.get("fixture") or spec.get("fixture_path")
    if raw is None:
        return None
    path = Path(str(raw))
    if path.is_absolute():
        return path
    if provider_fixture_dir is not None:
        return provider_fixture_dir / path
    return path


def _match_coolify_service(registry: ServiceRegistry, service: ServiceRecord) -> ServiceRecord | None:
    candidates = registry.list_services()
    for candidate in candidates:
        if candidate.name == service.name and candidate.node == service.node:
            return candidate
    for candidate in candidates:
        if set(candidate.domains).intersection(service.domains):
            return candidate
    return None
