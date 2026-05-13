from __future__ import annotations

import json
from pathlib import Path

from .docs_generate import _sanitize_value
from .service_registry import DEFAULT_SERVICE_REGISTRY_PATH, ServiceRecord, ServiceRegistry

SKELETON_ACTION_RESULT = {
    "approval_required": True,
    "not_executed": True,
    "machine_changed": False,
}

ADAPTER_ALIASES = {
    "restic": "restic",
    "borgmatic": "borgmatic",
    "kopia": "kopia",
}



def build_backup_plan_from_path(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    adapter: str | None = None,
) -> dict[str, object]:
    registry = ServiceRegistry.load(path)
    return build_backup_plan(registry, service_id=service_id, adapter=adapter)



def render_backup_plan_json(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    adapter: str | None = None,
) -> str:
    payload = build_backup_plan_from_path(path, service_id=service_id, adapter=adapter)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)



def build_backup_plan(
    registry: ServiceRegistry,
    *,
    service_id: str | None = None,
    adapter: str | None = None,
) -> dict[str, object]:
    normalized_adapter = _normalize_adapter(adapter)
    services = _select_services(registry, service_id=service_id, adapter=normalized_adapter)
    payload_services = [_service_backup_payload(service) for service in services]
    warning_count = sum(
        1 for item in payload_services for warning in item.get("warnings", []) if "missing backup metadata" in str(warning)
    )
    return _sanitize_value(
        {
            "mode": "plan",
            "service_count": len(payload_services),
            "service_id": service_id,
            "adapter": normalized_adapter,
            "source": _registry_source(registry),
            "warning_count": warning_count,
            "services": payload_services,
        }
    )



def build_backup_apply_request(
    registry: ServiceRegistry,
    *,
    service_id: str | None = None,
    adapters: list[str] | None = None,
) -> dict[str, object]:
    normalized_adapters = _normalize_adapters(adapters)
    filtered = registry.list_services()
    if service_id is not None:
        filtered = [service for service in filtered if service.service_id == service_id]
    if normalized_adapters:
        filtered = [service for service in filtered if _service_adapter(service) in normalized_adapters]
    payload_services = [_service_backup_payload(service) for service in filtered if _service_adapter(service) is not None]
    return _sanitize_value(
        {
            "mode": "apply_request",
            "provider": "backup-provider",
            "dry_run": True,
            "approval_required": True,
            "risk": "high",
            "service_count": len(payload_services),
            "service_id": service_id,
            "tool_candidates": normalized_adapters or sorted({_service_adapter(service) for service in filtered if _service_adapter(service)}),
            "provider_capabilities": {
                "verify": True,
                "restore_docs": True,
                "external_execution_enabled": False,
            },
            "execution": {
                "requested": False,
                "not_executed": True,
                "external_writes_blocked": True,
            },
            "services": payload_services,
        }
    )



def _registry_source(registry: ServiceRegistry) -> str:
    services = registry.list_services()
    if not services:
        return "service-registry"
    return str(services[0].source or "service-registry")



def _select_services(
    registry: ServiceRegistry,
    *,
    service_id: str | None,
    adapter: str | None,
) -> list[ServiceRecord]:
    services = registry.list_services()
    if service_id is not None:
        services = [service for service in services if service.service_id == service_id]
    if adapter is not None:
        services = [service for service in services if _service_adapter(service) == adapter]
    return services



def _service_backup_payload(service: ServiceRecord) -> dict[str, object]:
    backup = _backup_metadata(service)
    warnings = list(service.warnings)
    if not backup:
        warnings.insert(0, f"missing backup metadata for {service.service_id}")
        return {
            "service_id": service.service_id,
            "node": service.node,
            "adapter": None,
            "include_paths": [],
            "exclude_patterns": [],
            "repository": None,
            "retention_policy": {},
            "schedule_hint": None,
            "verify_plan": {},
            "approval": dict(SKELETON_ACTION_RESULT),
            "warnings": warnings,
        }

    return {
        "service_id": service.service_id,
        "node": service.node,
        "adapter": _normalize_adapter(backup.get("adapter")),
        "include_paths": _string_list(backup.get("include_paths") or backup.get("include") or backup.get("paths")),
        "exclude_patterns": _string_list(backup.get("exclude_patterns") or backup.get("exclude")),
        "repository": _redacted_repository(backup),
        "retention_policy": _dict_value(backup.get("retention")),
        "schedule_hint": _string_value(backup.get("schedule") or backup.get("schedule_hint")),
        "verify_plan": _verify_plan(backup.get("verify"), backup.get("checksum")),
        "approval": dict(SKELETON_ACTION_RESULT),
        "warnings": warnings,
    }



def _backup_metadata(service: ServiceRecord) -> dict[str, object]:
    monitor = service.monitor if isinstance(service.monitor, dict) else {}
    backup = monitor.get("backup")
    if isinstance(backup, dict):
        return dict(backup)
    return {}



def _service_adapter(service: ServiceRecord) -> str | None:
    return _normalize_adapter(_backup_metadata(service).get("adapter"))



def _normalize_adapter(adapter: object) -> str | None:
    if not isinstance(adapter, str):
        return None
    return ADAPTER_ALIASES.get(adapter.strip().lower())



def _normalize_adapters(adapters: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for adapter in adapters or []:
        value = _normalize_adapter(adapter)
        if value is not None and value not in normalized:
            normalized.append(value)
    return normalized



def _redacted_repository(backup: dict[str, object]) -> str | None:
    if backup.get("repository") or backup.get("target"):
        return "[REDACTED]"
    return None



def _string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value in {None, ""}:
        return []
    return [str(value)]



def _dict_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}



def _string_value(value: object) -> str | None:
    if value is None:
        return None
    return str(value)



def _verify_plan(verify: object, checksum: object) -> dict[str, object]:
    plan = _dict_value(verify)
    if checksum is not None and "checksum" not in plan:
        plan["checksum"] = checksum
    return plan
