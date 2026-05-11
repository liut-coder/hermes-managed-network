from __future__ import annotations

import json
import re
from pathlib import Path

from .docs_generate import _sanitize_value
from .service_registry import DEFAULT_SERVICE_REGISTRY_PATH, ServiceRecord, ServiceRegistry

DEFAULT_SERVER_DOC_ROOT = Path("/srv/files/docs/server")
DEFAULT_SERVICE_DOC_ROOT = Path("/srv/files/service")


def build_docs_sync_plan_from_path(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    server_doc_root: Path = DEFAULT_SERVER_DOC_ROOT,
    service_doc_root: Path = DEFAULT_SERVICE_DOC_ROOT,
    rename_hosts: dict[str, str] | None = None,
) -> dict[str, object]:
    registry = ServiceRegistry.load(path)
    return build_docs_sync_plan_from_registry(
        registry,
        server_doc_root=server_doc_root,
        service_doc_root=service_doc_root,
        rename_hosts=rename_hosts,
    )


def build_docs_sync_plan_from_registry(
    registry: ServiceRegistry,
    *,
    server_doc_root: Path = DEFAULT_SERVER_DOC_ROOT,
    service_doc_root: Path = DEFAULT_SERVICE_DOC_ROOT,
    rename_hosts: dict[str, str] | None = None,
) -> dict[str, object]:
    rename_hosts = {str(old): str(new) for old, new in (rename_hosts or {}).items() if str(old).strip() and str(new).strip()}
    services = registry.list_services()

    server_entries: list[dict[str, object]] = []
    service_entries: list[dict[str, object]] = []
    domain_mapping: dict[str, list[dict[str, object]]] = {}
    runbook_entries: list[dict[str, object]] = []
    rename_actions: list[dict[str, object]] = []
    touched_hosts: set[str] = set()

    host_summary: dict[str, dict[str, object]] = {}

    for service in services:
        source_host = _safe_host_segment(service.node)
        target_host = _safe_host_segment(rename_hosts.get(service.node, service.node))
        host_doc_path = _server_doc_path(server_doc_root, target_host)
        service_slug = _safe_service_slug(service.service_id or service.name)
        service_doc_path = _service_doc_path(service_doc_root, service_slug)

        if target_host not in host_summary:
            host_summary[target_host] = {
                "host": target_host,
                "source_host": source_host if source_host != target_host else None,
                "target_path": host_doc_path,
                "services": [],
            }
        host_summary[target_host]["services"].append(service.service_id)

        sanitized_monitor = _sanitize_value(service.monitor)
        sanitized_source = _sanitize_value(service.source)
        sanitized_docs_path = _sanitize_value(service.docs_path)
        sanitized_warnings = _sanitize_value(service.warnings)

        service_entry = {
            "service_id": service.service_id,
            "service_name": service.name,
            "service_slug": service_slug,
            "host": target_host,
            "source_host": source_host if source_host != target_host else None,
            "kind": service.kind,
            "runtime": service.runtime,
            "domains": list(service.domains),
            "ports": list(service.ports),
            "source": sanitized_source,
            "docs_path": sanitized_docs_path,
            "monitor": sanitized_monitor,
            "warnings": sanitized_warnings,
            "target_path": service_doc_path,
            "server_doc_path": host_doc_path,
            "dry_run": True,
        }
        service_entries.append(service_entry)

        for domain in service.domains:
            domain_mapping.setdefault(domain, []).append(
                {
                    "service_id": service.service_id,
                    "service_slug": service_slug,
                    "host": target_host,
                    "doc_path": service_doc_path,
                }
            )

        runbook_entries.append(
            {
                "service_id": service.service_id,
                "service_slug": service_slug,
                "host": target_host,
                "source_host": source_host if source_host != target_host else None,
                "service_doc_path": service_doc_path,
                "server_doc_path": host_doc_path,
                "domains": list(service.domains),
                "warnings": sanitized_warnings,
            }
        )

        if service.node in rename_hosts and service.node not in touched_hosts:
            touched_hosts.add(service.node)
            rename_actions.extend(
                _build_rename_actions(
                    old_host=source_host,
                    new_host=target_host,
                    server_doc_root=server_doc_root,
                    service_doc_root=service_doc_root,
                    affected_services=[item for item in service_entries if item["source_host"] == source_host],
                )
            )

    server_entries = sorted(host_summary.values(), key=lambda item: str(item["host"]))
    server_index_entries = [
        {
            "host": entry["host"],
            "source_host": entry.get("source_host"),
            "doc_path": entry["target_path"],
            "service_ids": sorted(entry["services"]),
        }
        for entry in server_entries
    ]
    service_index_entries = [
        {
            "service_id": entry["service_id"],
            "service_slug": entry["service_slug"],
            "host": entry["host"],
            "doc_path": entry["target_path"],
            "server_doc_path": entry["server_doc_path"],
        }
        for entry in service_entries
    ]

    payload = {
        "mode": "docs-sync-plan",
        "dry_run": True,
        "service_count": len(service_entries),
        "server_count": len(server_entries),
        "server_doc_root": server_doc_root.as_posix(),
        "service_doc_root": service_doc_root.as_posix(),
        "server_docs": server_entries,
        "service_docs": sorted(service_entries, key=lambda item: str(item["service_id"])),
        "indexes": {
            "server": {
                "target_path": (server_doc_root / "README.md").as_posix(),
                "entries": sorted(server_index_entries, key=lambda item: str(item["host"])),
            },
            "service": {
                "target_path": (service_doc_root / "README.md").as_posix(),
                "entries": sorted(service_index_entries, key=lambda item: str(item["service_id"])),
            },
            "domain_mapping": {
                "target_path": (service_doc_root / "domain-mapping.json").as_posix(),
                "entries": {key: value for key, value in sorted(domain_mapping.items())},
            },
            "runbook_mapping": {
                "target_path": (service_doc_root / "runbook-mapping.json").as_posix(),
                "entries": sorted(runbook_entries, key=lambda item: str(item["service_id"])),
            },
        },
        "rename_actions": rename_actions,
    }
    return _sanitize_value(payload)


def render_docs_sync_plan_json(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    server_doc_root: Path = DEFAULT_SERVER_DOC_ROOT,
    service_doc_root: Path = DEFAULT_SERVICE_DOC_ROOT,
    rename_hosts: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        build_docs_sync_plan_from_path(
            path,
            server_doc_root=server_doc_root,
            service_doc_root=service_doc_root,
            rename_hosts=rename_hosts,
        ),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def parse_rename_host_args(values: list[str] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in values or []:
        item = str(raw).strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"invalid rename host mapping: {raw}")
        old, new = item.split("=", 1)
        old = old.strip()
        new = new.strip()
        if not old or not new:
            raise ValueError(f"invalid rename host mapping: {raw}")
        mapping[old] = new
    return mapping


def _build_rename_actions(
    *,
    old_host: str,
    new_host: str,
    server_doc_root: Path,
    service_doc_root: Path,
    affected_services: list[dict[str, object]],
) -> list[dict[str, object]]:
    paths = [
        (server_doc_root / "README.md").as_posix(),
        (service_doc_root / "README.md").as_posix(),
        (service_doc_root / "domain-mapping.json").as_posix(),
        (service_doc_root / "runbook-mapping.json").as_posix(),
    ]
    for item in sorted(affected_services, key=lambda value: str(value["service_id"])):
        path = str(item["target_path"])
        if path not in paths:
            paths.append(path)
    return [
        {
            "action": "move",
            "from": (server_doc_root / old_host).as_posix(),
            "to": (server_doc_root / new_host).as_posix(),
            "host": old_host,
            "new_host": new_host,
        },
        {
            "action": "update_references",
            "paths": paths,
            "host": old_host,
            "new_host": new_host,
        },
    ]


def _server_doc_path(root: Path, host: str) -> str:
    return (root / host / "README.md").as_posix()


def _service_doc_path(root: Path, slug: str) -> str:
    return (root / slug / "README.md").as_posix()


def _safe_service_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.replace("/", "-").replace("\\", "-"))
    cleaned = cleaned.replace("..", "-")
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "service"


def _safe_host_segment(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.replace("/", "-").replace("\\", "-"))
    cleaned = cleaned.replace("..", "-")
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "host"
