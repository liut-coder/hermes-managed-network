from __future__ import annotations

import json
from pathlib import Path

from .docs_generate import _redact_text, _sanitize_value
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


def build_restore_plan_from_path(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    target_node: str | None = None,
    snapshot: str | None = None,
) -> dict[str, object]:
    registry = ServiceRegistry.load(path)
    return build_restore_plan(registry, service_id=service_id, target_node=target_node, snapshot=snapshot)



def render_restore_plan_json(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    target_node: str | None = None,
    snapshot: str | None = None,
) -> str:
    payload = build_restore_plan_from_path(path, service_id=service_id, target_node=target_node, snapshot=snapshot)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)



def build_restore_plan(
    registry: ServiceRegistry,
    *,
    service_id: str | None = None,
    target_node: str | None = None,
    snapshot: str | None = None,
) -> dict[str, object]:
    services = _select_services(registry, service_id=service_id)
    payload_services = [
        _service_restore_payload(service, target_node=target_node, snapshot=snapshot)
        for service in services
    ]
    warning_count = sum(
        1
        for item in payload_services
        for warning in item.get("warnings", [])
        if "missing restore metadata" in str(warning) or "missing backup metadata" in str(warning)
    )
    return _sanitize_value(
        {
            "mode": "plan",
            "service_count": len(payload_services),
            "service_id": service_id,
            "target_node": target_node,
            "snapshot": _snapshot_selector(snapshot),
            "source": _registry_source(registry),
            "warning_count": warning_count,
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
) -> list[ServiceRecord]:
    services = registry.list_services()
    if service_id is not None:
        services = [service for service in services if service.service_id == service_id]
    return services



def _service_restore_payload(
    service: ServiceRecord,
    *,
    target_node: str | None,
    snapshot: str | None,
) -> dict[str, object]:
    backup = _backup_metadata(service)
    restore = _restore_metadata(service)
    warnings = list(service.warnings)
    if not backup:
        warnings.insert(0, f"missing backup metadata for {service.service_id}")
    if not restore:
        warnings.insert(0, f"missing restore metadata for {service.service_id}")

    effective_target_node = _string_value(target_node) or service.node
    effective_target_path = _string_value(restore.get("target_path") or restore.get("path") or restore.get("target"))
    effective_snapshot = _string_value(snapshot) or _string_value(restore.get("snapshot") or restore.get("snapshot_id")) or "latest"
    adapter = _normalize_adapter(backup.get("adapter"))
    service_stop_required = bool(restore.get("service_stop_required", bool(service.runtime)))
    cutover_hint = _string_value(restore.get("domain_cutover_hint"))

    return {
        "service_id": service.service_id,
        "node": service.node,
        "adapter": adapter,
        "repository": _redacted_repository(backup),
        "source_selector": _restore_source_selector(backup),
        "snapshot_selector": _snapshot_selector(effective_snapshot),
        "target_node": effective_target_node,
        "target_path": _redacted_target_path(effective_target_path),
        "preflight_checks": _preflight_checks(
            service,
            target_node=effective_target_node,
            target_path=effective_target_path,
            service_stop_required=service_stop_required,
            domain_cutover_hint=cutover_hint,
        ),
        "restore_steps": _restore_steps(service, adapter=adapter),
        "verification_steps": _verification_steps(service, backup=backup, restore=restore),
        "rollback_hint": _rollback_hint(service, adapter=adapter),
        "apply": dict(SKELETON_ACTION_RESULT),
        "warnings": warnings,
    }



def _backup_metadata(service: ServiceRecord) -> dict[str, object]:
    monitor = service.monitor if isinstance(service.monitor, dict) else {}
    backup = monitor.get("backup")
    if isinstance(backup, dict):
        return dict(backup)
    return {}



def _restore_metadata(service: ServiceRecord) -> dict[str, object]:
    monitor = service.monitor if isinstance(service.monitor, dict) else {}
    restore = monitor.get("restore")
    if isinstance(restore, dict):
        return dict(restore)
    return {}



def _normalize_adapter(adapter: object) -> str | None:
    if not isinstance(adapter, str):
        return None
    return ADAPTER_ALIASES.get(adapter.strip().lower())



def _redacted_repository(backup: dict[str, object]) -> str | None:
    if backup.get("repository") or backup.get("target"):
        return "[REDACTED]"
    return None



def _restore_source_selector(backup: dict[str, object]) -> str | None:
    if backup.get("repository") or backup.get("target"):
        return "[REDACTED]"
    return None



def _redacted_target_path(target_path: str | None) -> str | None:
    if target_path:
        return "[REDACTED]"
    return None



def _snapshot_selector(snapshot: str | None) -> str:
    if not snapshot or snapshot.strip() == "":
        return "latest"
    normalized = snapshot.strip()
    if normalized.lower() == "latest":
        return "latest"
    return "[REDACTED]"



def _preflight_checks(
    service: ServiceRecord,
    *,
    target_node: str,
    target_path: str | None,
    service_stop_required: bool,
    domain_cutover_hint: str | None,
) -> list[dict[str, object]]:
    checks = [
        {
            "name": "disk_space",
            "status": "planned",
            "detail": f"确认目标节点 {target_node} 具备足够磁盘空间容纳恢复数据。",
        },
        {
            "name": "path_exists_or_empty",
            "status": "planned",
            "detail": "确认目标路径存在，或为空目录后再执行恢复。" if target_path else "确认恢复目标路径后再验证是否存在或为空目录。",
        },
        {
            "name": "permissions",
            "status": "planned",
            "detail": "确认恢复执行用户对目标路径拥有写入权限，并可修改属主/权限。",
        },
        {
            "name": "port_conflicts",
            "status": "planned",
            "detail": f"检查目标节点端口冲突：{service.ports or []}。",
        },
        {
            "name": "service_stop_required",
            "status": "planned",
            "required": service_stop_required,
            "detail": "若恢复会覆盖运行中数据，先在审批通过后停服务；当前 plan 不执行停服。",
        },
        {
            "name": "domain_cutover",
            "status": "planned",
            "detail": domain_cutover_hint or "如涉及对外流量，请确认域名 / 入口切换窗口与回切策略。",
        },
    ]
    return checks



def _restore_steps(service: ServiceRecord, *, adapter: str | None) -> list[dict[str, object]]:
    adapter_name = adapter or "backup-adapter"
    return [
        {
            "step": 1,
            "name": "select_snapshot",
            "detail": f"根据审批结果选择 {adapter_name} 快照或 latest；当前仅生成计划。",
        },
        {
            "step": 2,
            "name": "prepare_target",
            "detail": "校验目标节点、目标路径、权限和空间；当前不读取真实目录内容。",
        },
        {
            "step": 3,
            "name": "restore_data",
            "detail": "执行恢复命令骨架并记录输出；当前 plan 不连接仓库、不落地恢复。",
        },
        {
            "step": 4,
            "name": "reconcile_runtime",
            "detail": f"按服务类型 {service.kind} 补齐运行时配置并准备后续验证；当前不启动/停止服务。",
        },
    ]



def _verification_steps(
    service: ServiceRecord,
    *,
    backup: dict[str, object],
    restore: dict[str, object],
) -> list[dict[str, object]]:
    healthcheck = _string_value(restore.get("healthcheck")) or _default_healthcheck(service)
    smoke_commands = _string_list(restore.get("smoke"))
    return [
        {
            "name": "checksum",
            "detail": f"按备份元数据校验 checksum：{_string_value(_dict_value(backup.get('verify')).get('checksum')) or 'pending'}。",
        },
        {
            "name": "healthcheck",
            "detail": f"验证健康检查：{healthcheck or 'pending'}。",
        },
        {
            "name": "port",
            "detail": f"确认监听端口与 registry 一致：{service.ports or []}。",
        },
        {
            "name": "domain",
            "detail": f"确认域名解析/入口回源正确：{service.domains or []}。",
        },
        {
            "name": "smoke",
            "detail": smoke_commands or ["执行基础 smoke test（如首页、登录、关键接口）。"],
        },
    ]



def _rollback_hint(service: ServiceRecord, *, adapter: str | None) -> dict[str, object]:
    return {
        "summary": "若验证失败，保留当前目标节点现场，切回旧实例或旧域名入口，并重新选择上一个可用快照。",
        "service_id": service.service_id,
        "adapter": adapter,
        "machine_changed": False,
    }



def _default_healthcheck(service: ServiceRecord) -> str | None:
    if service.domains:
        return f"https://{service.domains[0]}/healthz"
    return None



def _dict_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}



def _string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    if value in {None, ""}:
        return []
    return [str(value)]



def _string_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return _redact_text(text)
