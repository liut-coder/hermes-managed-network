from __future__ import annotations

import json
from pathlib import Path

from .providers import redact_sensitive_data
from .service_registry import DEFAULT_SERVICE_REGISTRY_PATH, ServiceRecord, ServiceRegistry

SKELETON_ACTION_RESULT = {
    "approval_required": True,
    "not_executed": True,
    "machine_changed": False,
}

ALLOWED_STRATEGIES = {"backup-restore", "redeploy", "manual-copy"}


def build_migration_plan_from_path(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    source_node: str | None = None,
    target_node: str | None = None,
    strategy: str | None = None,
) -> dict[str, object]:
    registry = ServiceRegistry.load(path)
    return build_migration_plan(
        registry,
        service_id=service_id,
        source_node=source_node,
        target_node=target_node,
        strategy=strategy,
    )


def render_migration_plan_json(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    service_id: str | None = None,
    source_node: str | None = None,
    target_node: str | None = None,
    strategy: str | None = None,
) -> str:
    payload = build_migration_plan_from_path(
        path,
        service_id=service_id,
        source_node=source_node,
        target_node=target_node,
        strategy=strategy,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_migration_plan(
    registry: ServiceRegistry,
    *,
    service_id: str | None = None,
    source_node: str | None = None,
    target_node: str | None = None,
    strategy: str | None = None,
) -> dict[str, object]:
    normalized_strategy = _normalize_strategy(strategy)
    services = _select_services(registry, service_id=service_id, source_node=source_node)
    payload_services = [
        _service_migration_payload(
            service,
            requested_target_node=target_node,
            requested_strategy=normalized_strategy,
        )
        for service in services
    ]
    warning_count = sum(len(item.get("warnings", [])) for item in payload_services)
    return redact_sensitive_data(
        {
            "mode": "plan",
            "dry_run": True,
            "service_count": len(payload_services),
            "service_id": service_id,
            "source_node": source_node,
            "target_node": target_node,
            "strategy": normalized_strategy,
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
    source_node: str | None,
) -> list[ServiceRecord]:
    services = registry.list_services()
    if service_id is not None:
        services = [service for service in services if service.service_id == service_id]
    if source_node is not None:
        services = [service for service in services if service.node == source_node]
    return services


def _service_migration_payload(
    service: ServiceRecord,
    *,
    requested_target_node: str | None,
    requested_strategy: str | None,
) -> dict[str, object]:
    backup = _backup_metadata(service)
    restore = _restore_metadata(service)
    warnings = list(service.warnings)
    effective_target_node = _string_value(requested_target_node) or service.node
    if not _string_value(requested_target_node):
        warnings.insert(0, f"target node not specified for {service.service_id}; defaulting to source node skeleton")
    effective_strategy = requested_strategy or _infer_strategy(backup=backup, restore=restore)

    return {
        "service_id": service.service_id,
        "source_node": service.node,
        "target_node": effective_target_node,
        "strategy": effective_strategy,
        "data_paths": _data_paths(service, backup=backup, restore=restore),
        "config_paths": _config_paths(restore),
        "docs_path": _string_value(service.docs_path),
        "ports": list(service.ports),
        "domains": list(service.domains),
        "conflict_checks": _conflict_checks(service, target_node=effective_target_node),
        "backup_prerequisite": _backup_prerequisite(backup),
        "restore_prerequisite": _restore_prerequisite(restore, target_node=effective_target_node),
        "prerequisites": _prerequisites(service, backup=backup, restore=restore, target_node=effective_target_node),
        "dns_cutover_hint": _dns_cutover_hint(service, restore=restore),
        "reverse_proxy_cutover_hint": _reverse_proxy_cutover_hint(service),
        "verification_steps": _verification_steps(service, backup=backup, restore=restore),
        "rollback_steps": _rollback_steps(service),
        "risk_flags": {
            "no_zero_downtime_guarantee": True,
            "requires_maintenance_window": True,
            "dry_run_only": True,
        },
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


def _normalize_strategy(strategy: object) -> str | None:
    if not isinstance(strategy, str):
        return None
    normalized = strategy.strip().lower()
    if normalized in ALLOWED_STRATEGIES:
        return normalized
    return None


def _infer_strategy(*, backup: dict[str, object], restore: dict[str, object]) -> str:
    if backup or restore:
        return "backup-restore"
    return "redeploy"


def _data_paths(service: ServiceRecord, *, backup: dict[str, object], restore: dict[str, object]) -> list[str]:
    values = _string_list(backup.get("include_paths") or backup.get("include") or backup.get("paths"))
    target_path = _string_value(restore.get("target_path") or restore.get("path") or restore.get("target"))
    if target_path and target_path not in values:
        values.insert(0, target_path)
    return values


def _config_paths(restore: dict[str, object]) -> list[str]:
    return _string_list(restore.get("config_paths") or restore.get("config") or restore.get("config_path"))


def _conflict_checks(service: ServiceRecord, *, target_node: str) -> list[dict[str, object]]:
    return [
        {
            "name": "ports",
            "status": "planned",
            "detail": f"检查目标节点 {target_node} 上端口冲突：{service.ports or []}。",
        },
        {
            "name": "domains",
            "status": "planned",
            "detail": f"检查域名 / 入口占用与证书绑定：{service.domains or []}。",
        },
    ]


def _backup_prerequisite(backup: dict[str, object]) -> dict[str, object]:
    return {
        "available": bool(backup),
        "adapter": _string_value(backup.get("adapter")),
        "repository": "[REDACTED]" if backup.get("repository") or backup.get("target") else None,
        "snapshot": _snapshot_selector(backup),
    }


def _restore_prerequisite(restore: dict[str, object], *, target_node: str) -> dict[str, object]:
    return {
        "available": bool(restore),
        "target_node": target_node,
        "target_path": "[REDACTED]" if _string_value(restore.get("target_path") or restore.get("path") or restore.get("target")) else None,
        "service_stop_required": bool(restore.get("service_stop_required", False)),
    }


def _prerequisites(
    service: ServiceRecord,
    *,
    backup: dict[str, object],
    restore: dict[str, object],
    target_node: str,
) -> list[dict[str, object]]:
    return [
        {
            "name": "backup_metadata",
            "status": "ready" if backup else "warning",
            "detail": "确认存在可用 backup 元数据；当前仅校验元数据骨架，不访问真实仓库。",
        },
        {
            "name": "restore_metadata",
            "status": "ready" if restore else "warning",
            "detail": "确认存在 restore 目标路径/服务停机提示；当前不读取真实数据目录。",
        },
        {
            "name": "target_node",
            "status": "planned",
            "detail": f"确认目标节点 {target_node} 已具备运行时与挂载条件；当前不连接远端。",
        },
        {
            "name": "docs_path",
            "status": "planned",
            "detail": f"同步服务文档路径 {service.docs_path or 'pending'} 到迁移记录。",
        },
    ]


def _dns_cutover_hint(service: ServiceRecord, *, restore: dict[str, object]) -> str:
    explicit = _string_value(restore.get("domain_cutover_hint"))
    if explicit:
        return explicit
    if service.domains:
        return f"在维护窗口内切换 DNS/入口到目标节点，域名：{service.domains}。"
    return "如服务无域名，请改为内部入口或上游依赖切换。"


def _reverse_proxy_cutover_hint(service: ServiceRecord) -> str:
    if service.domains or service.ports:
        return "更新反向代理 / 负载均衡 upstream 指向目标节点，确认回切入口仍可用。"
    return "如无公网入口，请确认内部代理、服务发现或 systemd 依赖已指向目标节点。"


def _verification_steps(
    service: ServiceRecord,
    *,
    backup: dict[str, object],
    restore: dict[str, object],
) -> list[dict[str, object]]:
    verify = _dict_value(backup.get("verify"))
    healthcheck = _string_value(restore.get("healthcheck")) or _default_healthcheck(service)
    return [
        {
            "name": "checksum",
            "detail": f"按迁移后数据与备份摘要比对 checksum：{_string_value(verify.get('checksum')) or 'pending'}。",
        },
        {
            "name": "healthcheck",
            "detail": f"验证健康检查：{healthcheck or 'pending'}。",
        },
        {
            "name": "port_smoke",
            "detail": f"对目标节点端口做监听/连通性冒烟：{service.ports or []}。",
        },
        {
            "name": "domain_smoke",
            "detail": f"对域名入口做解析/HTTP 冒烟：{service.domains or []}。",
        },
    ]


def _rollback_steps(service: ServiceRecord) -> list[dict[str, object]]:
    return [
        {
            "name": "repoint_dns",
            "detail": "若切换后失败，回指 DNS / 入口到源节点。",
        },
        {
            "name": "revert_proxy",
            "detail": "回滚反向代理 / LB upstream 到源节点并撤销新目标流量。",
        },
        {
            "name": "restore_source_service",
            "detail": f"确认源节点 {service.node} 服务继续保留或重新拉起，直到验证完成。",
        },
    ]


def _snapshot_selector(backup: dict[str, object]) -> str | None:
    snapshot = _string_value(backup.get("snapshot") or backup.get("snapshot_id") or backup.get("last_snapshot"))
    if snapshot:
        return "[REDACTED]"
    verify = _dict_value(backup.get("verify"))
    if verify.get("checksum") is not None:
        return "[REDACTED]"
    return None


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
    return _redact_sensitive_text(str(value))


def _redact_sensitive_text(value: str) -> str:
    redacted = redact_sensitive_data(value)
    return redacted if isinstance(redacted, str) else str(redacted)
