from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from .storage import SQLiteStore, ServiceRecord


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower()).strip("-")
    return normalized or "unknown"


def _service_id(node_id: str, name: str) -> str:
    return f"svc_{node_id}_{_normalize_name(name)}"


def _merge_unique_ints(left: list[int], right: list[int]) -> list[int]:
    return sorted({int(item) for item in [*left, *right]})


def _merge_metadata(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "listen_ports":
            current = list(merged.get(key, []))
            seen = {(item.get("address"), item.get("port"), item.get("process")) for item in current if isinstance(item, dict)}
            for item in value if isinstance(value, list) else []:
                if not isinstance(item, dict):
                    continue
                marker = (item.get("address"), item.get("port"), item.get("process"))
                if marker not in seen:
                    current.append(item)
                    seen.add(marker)
            merged[key] = current
        elif key not in merged:
            merged[key] = value
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            nested = dict(merged[key])
            for nested_key, nested_value in value.items():
                if nested_key not in nested:
                    nested[nested_key] = nested_value
            merged[key] = nested
    return merged


def _has_value(value: Any) -> bool:
    if isinstance(value, (list, dict, str)):
        return bool(value)
    return value is not None and value is not False


def _prefer_existing(existing: Any, incoming: Any) -> Any:
    return existing if _has_value(existing) or not _has_value(incoming) else incoming


def _add_or_merge(records: dict[str, ServiceRecord], record: ServiceRecord) -> None:
    existing = records.get(record.service_id)
    if existing is None:
        records[record.service_id] = record
        return
    records[record.service_id] = replace(
        existing,
        ports=_merge_unique_ints(existing.ports, record.ports),
        metadata=_merge_metadata(existing.metadata, record.metadata),
    )


def _parse_systemd(systemd_output: str, node_id: str) -> list[ServiceRecord]:
    records: list[ServiceRecord] = []
    for line in systemd_output.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("●"):
            line = line.removeprefix("●").strip()
        parts = line.split(None, 4)
        if len(parts) < 4 or not parts[0].endswith(".service"):
            continue
        unit = parts[0]
        name = unit.removesuffix(".service")
        description = parts[4] if len(parts) > 4 else ""
        records.append(
            ServiceRecord(
                service_id=_service_id(node_id, name),
                name=name,
                node_id=node_id,
                kind="systemd",
                runtime=unit,
                source="discovery",
                metadata={
                    "systemd": {
                        "unit": unit,
                        "load": parts[1],
                        "active": parts[2],
                        "sub": parts[3],
                        "description": description,
                    }
                },
            )
        )
    return records


def _extract_docker_ports(value: str) -> list[int]:
    ports: set[int] = set()
    for match in re.finditer(r"(?:(?:\d{1,3}\.){3}\d{1,3}|::|\[?:::\]?|0\.0\.0\.0|localhost)?:(\d+)->\d+/(?:tcp|udp)", value):
        ports.add(int(match.group(1)))
    return sorted(ports)


def _parse_docker(docker_output: str, node_id: str) -> list[ServiceRecord]:
    records: list[ServiceRecord] = []
    for line in docker_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(payload.get("Names") or payload.get("Name") or payload.get("ID") or "container").lstrip("/")
        image = str(payload.get("Image") or "")
        ports_text = str(payload.get("Ports") or "")
        records.append(
            ServiceRecord(
                service_id=_service_id(node_id, name),
                name=name,
                node_id=node_id,
                kind="docker",
                runtime=image,
                ports=_extract_docker_ports(ports_text),
                source="discovery",
                metadata={
                    "docker": {
                        "container_name": name,
                        "image": image,
                        "ports": ports_text,
                    }
                },
            )
        )
    return records


def _normalize_process_name(value: str) -> str:
    if value == "postgres":
        return "postgresql"
    return value


def _parse_ports(ports_output: str, node_id: str) -> list[ServiceRecord]:
    records: list[ServiceRecord] = []
    process_re = re.compile(r'"([^"]+)"')
    for line in ports_output.splitlines():
        line = line.strip()
        if not line or line.startswith("State") or "LISTEN" not in line:
            continue
        parts = line.split()
        local = next((part for part in parts if ":" in part and not part.endswith(":*")), "")
        if not local:
            continue
        address, port_text = local.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            continue
        process_match = process_re.search(line)
        process = process_match.group(1) if process_match else f"port-{port}"
        name = _normalize_process_name(process)
        records.append(
            ServiceRecord(
                service_id=_service_id(node_id, name),
                name=name,
                node_id=node_id,
                kind="port",
                runtime="ss",
                ports=[port],
                source="discovery",
                metadata={"listen_ports": [{"address": address, "port": port, "process": process}]},
            )
        )
    return records


def discover_services_from_text(
    node_id: str,
    systemd_output: str = "",
    docker_output: str = "",
    ports_output: str = "",
) -> list[ServiceRecord]:
    """Discover ServiceRecord candidates from command output text fixtures."""
    records: dict[str, ServiceRecord] = {}
    for record in _parse_systemd(systemd_output, node_id):
        _add_or_merge(records, record)
    for record in _parse_docker(docker_output, node_id):
        _add_or_merge(records, record)
    for record in _parse_ports(ports_output, node_id):
        _add_or_merge(records, record)
    return [records[key] for key in sorted(records)]


def plan_discovered_services(
    store: SQLiteStore,
    node_id: str,
    records: list[ServiceRecord],
    source: str = "discovery",
) -> tuple[list[ServiceRecord], list[dict[str, Any]]]:
    """Build merged service records and a non-mutating before/after diff."""
    planned: list[ServiceRecord] = []
    changes: list[dict[str, Any]] = []
    for record in records:
        discovered = replace(record, node_id=record.node_id or node_id, source=source)
        existing = store.load_service_record(discovered.service_id)
        if existing is not None:
            metadata = _merge_metadata(existing.metadata, discovered.metadata)
            metadata["discovery_source"] = source
            service = replace(
                existing,
                name=_prefer_existing(existing.name, discovered.name),
                node_id=_prefer_existing(existing.node_id, discovered.node_id),
                kind=_prefer_existing(existing.kind, discovered.kind),
                runtime=_prefer_existing(existing.runtime, discovered.runtime),
                domains=_prefer_existing(existing.domains, discovered.domains),
                ports=_merge_unique_ints(existing.ports, discovered.ports),
                deploy_path=_prefer_existing(existing.deploy_path, discovered.deploy_path),
                config_paths=_prefer_existing(existing.config_paths, discovered.config_paths),
                env_paths=_prefer_existing(existing.env_paths, discovered.env_paths),
                data_paths=_prefer_existing(existing.data_paths, discovered.data_paths),
                health_check_url=_prefer_existing(existing.health_check_url, discovered.health_check_url),
                monitor_enabled=_prefer_existing(existing.monitor_enabled, discovered.monitor_enabled),
                docs_path=_prefer_existing(existing.docs_path, discovered.docs_path),
                source=_prefer_existing(existing.source, discovered.source),
                status=_prefer_existing(existing.status, discovered.status),
                metadata=metadata,
            )
            change_type = "update" if _service_diff(existing, service) else "unchanged"
        else:
            metadata = dict(discovered.metadata)
            metadata["discovery_source"] = source
            service = replace(discovered, metadata=metadata)
            change_type = "create"
        planned.append(service)
        changes.append(
            {
                "service_id": service.service_id,
                "change": change_type,
                "before": _service_snapshot(existing) if existing else None,
                "after": _service_snapshot(service),
                "diff": _service_diff(existing, service) if existing else _service_diff(None, service),
            }
        )
    return planned, changes


def apply_discovered_services(
    store: SQLiteStore,
    node_id: str,
    records: list[ServiceRecord],
    source: str = "discovery",
) -> list[ServiceRecord]:
    planned, changes = plan_discovered_services(store, node_id, records, source=source)
    saved = [store.save_service_record(service) for service in planned]
    store.record_audit(
        event_type="service",
        subject_type="node",
        subject_id=node_id,
        action="service_discovery",
        outcome="ok",
        details={
            "source": source,
            "service_count": len(saved),
            "service_ids": [record.service_id for record in saved],
            "changes": [
                {"service_id": change["service_id"], "change": change["change"], "diff": change["diff"]}
                for change in changes
            ],
        },
    )
    return saved


def _service_snapshot(service: ServiceRecord | None) -> dict[str, Any] | None:
    if service is None:
        return None
    return {
        "service_id": service.service_id,
        "name": service.name,
        "node_id": service.node_id,
        "kind": service.kind,
        "runtime": service.runtime,
        "domains": list(service.domains),
        "ports": list(service.ports),
        "deploy_path": service.deploy_path,
        "config_paths": list(service.config_paths),
        "env_paths": list(service.env_paths),
        "data_paths": list(service.data_paths),
        "health_check_url": service.health_check_url,
        "monitor_enabled": service.monitor_enabled,
        "docs_path": service.docs_path,
        "source": service.source,
        "status": service.status,
        "metadata": service.metadata,
    }


def _service_diff(before: ServiceRecord | None, after: ServiceRecord) -> dict[str, dict[str, Any]]:
    before_snapshot = _service_snapshot(before) or {}
    after_snapshot = _service_snapshot(after) or {}
    diff: dict[str, dict[str, Any]] = {}
    for key, after_value in after_snapshot.items():
        before_value = before_snapshot.get(key)
        if before_value != after_value:
            diff[key] = {"before": before_value, "after": after_value}
    return diff
