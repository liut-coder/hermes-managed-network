from __future__ import annotations

from .docs_generate import _sanitize_value
from .service_registry import ServiceRecord, ServiceRegistry
from .storage import ServiceRecord as StorageServiceRecord


def registry_from_storage_records(records: list[StorageServiceRecord]) -> ServiceRegistry:
    """Build the shared service registry view from persisted DB service records."""
    return ServiceRegistry([service_record_from_storage(record) for record in records])


def service_record_from_storage(record: StorageServiceRecord) -> ServiceRecord:
    metadata = dict(record.metadata or {})
    monitor = _monitor_payload_from_storage(record, metadata)
    return ServiceRecord(
        service_id=record.service_id,
        name=record.name,
        node=record.node_id,
        kind=record.kind,
        domains=list(record.domains),
        ports=list(record.ports),
        runtime=record.runtime,
        deploy_path=record.deploy_path,
        config_paths=list(record.config_paths),
        env_paths=list(record.env_paths),
        data_paths=list(record.data_paths),
        health_check_url=record.health_check_url,
        source=record.source,
        docs_path=record.docs_path,
        monitor=monitor,
        warnings=[str(warning) for warning in metadata.get("warnings", [])] if isinstance(metadata.get("warnings"), list) else [],
        metadata=_sanitize_value(metadata) if isinstance(metadata, dict) else {},
    )


def _monitor_payload_from_storage(record: StorageServiceRecord, metadata: dict[str, object]) -> dict[str, object]:
    monitor = dict(metadata.get("monitor") or {}) if isinstance(metadata.get("monitor"), dict) else {}
    providers = _provider_specs_from_metadata(metadata)
    if providers:
        existing = monitor.get("providers")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(providers)
        monitor["providers"] = merged
    monitor.setdefault("enabled", record.monitor_enabled)
    monitor["registry_status"] = record.status
    sanitized_metadata = _sanitize_value(metadata)
    if isinstance(sanitized_metadata, dict):
        if record.health_check_url:
            sanitized_metadata["health_check_url"] = record.health_check_url
        monitor["metadata"] = sanitized_metadata
    else:
        monitor["metadata"] = {"health_check_url": record.health_check_url} if record.health_check_url else {}
    return monitor


def _provider_specs_from_metadata(metadata: dict[str, object]) -> dict[str, object]:
    providers: dict[str, object] = {}
    for raw in (
        metadata.get("providers"),
        (metadata.get("deploy") or {}).get("providers") if isinstance(metadata.get("deploy"), dict) else None,
        (metadata.get("monitor") or {}).get("providers") if isinstance(metadata.get("monitor"), dict) else None,
    ):
        if isinstance(raw, dict):
            providers.update(raw)
    return providers
