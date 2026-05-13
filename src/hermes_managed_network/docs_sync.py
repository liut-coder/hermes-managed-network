from __future__ import annotations

import json
import re
from pathlib import Path

from .docs_generate import _redact_text, _sanitize_value
from .service_registry import DEFAULT_SERVICE_REGISTRY_PATH, ServiceRecord, ServiceRegistry

DEFAULT_DOCS_CENTER_ROOT = Path("/srv/files")
DEFAULT_SERVER_DOC_ROOT = DEFAULT_DOCS_CENTER_ROOT / "docs" / "server"
DEFAULT_SERVICE_DOC_ROOT = DEFAULT_DOCS_CENTER_ROOT / "service"


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


def apply_docs_sync_from_path(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    root: Path = DEFAULT_DOCS_CENTER_ROOT,
    execute: bool = False,
    rename_hosts: dict[str, str] | None = None,
) -> dict[str, object]:
    registry = ServiceRegistry.load(path)
    return apply_docs_sync_from_registry(registry, root=root, execute=execute, rename_hosts=rename_hosts)


def apply_docs_sync_from_registry(
    registry: ServiceRegistry,
    *,
    root: Path = DEFAULT_DOCS_CENTER_ROOT,
    execute: bool = False,
    rename_hosts: dict[str, str] | None = None,
) -> dict[str, object]:
    root = Path(root)
    server_doc_root = root / "docs" / "server"
    service_doc_root = root / "service"
    plan = build_docs_sync_plan_from_registry(
        registry,
        server_doc_root=server_doc_root,
        service_doc_root=service_doc_root,
        rename_hosts=rename_hosts,
    )
    services = registry.list_services()
    root_resolved = root.resolve(strict=False)

    writes = _build_apply_writes(plan=plan, services=services, root=root, root_resolved=root_resolved)
    payload: dict[str, object] = {
        "mode": "docs-sync-apply",
        "root": root.as_posix(),
        "dry_run": not execute,
        "execute": execute,
        "service_count": len(services),
        "server_count": int(plan["server_count"]),
        "changed": 0,
        "written": [],
        "skipped": len(writes),
        "plan": plan,
        "audit": _audit_event_skeleton(root=root, execute=execute, writes=writes),
    }

    if not execute:
        return _sanitize_value(payload)

    written: list[str] = []
    for relative_target, content in writes:
        target = _resolve_target(root_resolved, relative_target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target.as_posix())

    payload["dry_run"] = False
    payload["changed"] = len(written)
    payload["written"] = written
    payload["skipped"] = 0
    payload["audit"] = _audit_event_skeleton(root=root, execute=execute, writes=writes, written=written)
    return _sanitize_value(payload)


def render_docs_sync_apply_json(
    path: Path = DEFAULT_SERVICE_REGISTRY_PATH,
    *,
    root: Path = DEFAULT_DOCS_CENTER_ROOT,
    execute: bool = False,
    rename_hosts: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        apply_docs_sync_from_path(path, root=root, execute=execute, rename_hosts=rename_hosts),
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


def _build_apply_writes(
    *,
    plan: dict[str, object],
    services: list[ServiceRecord],
    root: Path,
    root_resolved: Path,
) -> list[tuple[Path, str]]:
    docs_dir = Path("docs")
    service_dir = Path("service")
    server_dir = docs_dir / "server"

    server_entries = list(plan["server_docs"])
    service_entries = list(plan["service_docs"])
    indexes = dict(plan["indexes"])

    writes: list[tuple[Path, str]] = [
        (docs_dir / "README.md", _render_docs_root_readme(root, plan)),
        (docs_dir / "index.json", _json_dumps(_build_docs_index_json(root, plan))),
        (server_dir / "README.md", _render_server_index_readme(server_entries)),
        (service_dir / "README.md", _render_service_index_readme(service_entries)),
        (service_dir / "index.json", _json_dumps(_build_service_index_json(root, service_entries, indexes))),
        (service_dir / "domain-mapping.json", _json_dumps(indexes["domain_mapping"]["entries"])),
        (service_dir / "runbook-mapping.json", _json_dumps(indexes["runbook_mapping"]["entries"])),
    ]

    service_by_id = {service.service_id: service for service in services}

    for entry in server_entries:
        rel = _relative_from_root(root_resolved, Path(str(entry["target_path"])))
        writes.append((rel, _render_server_readme(entry, service_entries)))

    for entry in service_entries:
        rel = _relative_from_root(root_resolved, Path(str(entry["target_path"])))
        service = service_by_id[str(entry["service_id"])]
        writes.append((rel, _render_service_readme(entry, service)))

    for rel, _ in writes:
        _resolve_target(root_resolved, rel)
    return writes


def _build_docs_index_json(root: Path, plan: dict[str, object]) -> dict[str, object]:
    return {
        "mode": "docs-center-index",
        "root": root.as_posix(),
        "docs_root": (root / "docs").as_posix(),
        "service_root": (root / "service").as_posix(),
        "server_count": plan["server_count"],
        "service_count": plan["service_count"],
        "server_index": (root / "docs" / "server" / "README.md").as_posix(),
        "service_index": (root / "service" / "README.md").as_posix(),
        "future_submit_flow": {
            "ingest": "nodes submit facts/service registry to master API or worker bridge",
            "master_responsibility": ["redaction", "normalization", "write_files", "indexing", "audit"],
            "public_submit_api": "reserved-not-implemented",
        },
    }


def _build_service_index_json(root: Path, service_entries: list[dict[str, object]], indexes: dict[str, object]) -> dict[str, object]:
    return {
        "mode": "service-index",
        "root": root.as_posix(),
        "services": [
            {
                "service_id": entry["service_id"],
                "service_slug": entry["service_slug"],
                "host": entry["host"],
                "doc_path": entry["target_path"],
                "server_doc_path": entry["server_doc_path"],
                "domains": entry["domains"],
                "ports": entry["ports"],
                "warnings": entry["warnings"],
            }
            for entry in service_entries
        ],
        "domain_mapping": indexes["domain_mapping"]["entries"],
        "runbook_mapping": indexes["runbook_mapping"]["entries"],
    }


def _audit_event_skeleton(root: Path, execute: bool, writes: list[tuple[Path, str]], written: list[str] | None = None) -> dict[str, object]:
    return {
        "event_type": "docs_sync",
        "subject_type": "docs_center",
        "subject_id": root.as_posix(),
        "action": "apply",
        "outcome": "success" if execute else "dry_run",
        "details": {
            "execute": execute,
            "planned_writes": len(writes),
            "written": len(written or []),
        },
    }


def _render_docs_root_readme(root: Path, plan: dict[str, object]) -> str:
    return "\n".join(
        [
            "# HMN 文档中心 / 数据中心",
            "",
            f"- 根目录：`{root.as_posix()}`",
            f"- 机器数：`{plan['server_count']}`",
            f"- 服务数：`{plan['service_count']}`",
            "- 面向人类：Markdown README",
            "- 面向机器：JSON index",
            "",
            "## 第一版写入边界",
            "- 统一由 master 写入文档中心",
            "- 节点不直接任意写 /srv/files",
            "- 落盘前先脱敏、规范化、建索引、产出审计事件",
            "",
            "## 后续节点提交流程（预留）",
            "1. 节点通过 master API / worker 上报 facts 或 service registry",
            "2. master 负责 redaction / normalization / storage / indexing / audit",
            "3. 当前版本只落盘 apply，不实现公网 submit API",
            "",
            "- 机器索引：`docs/server/README.md`",
            "- 服务索引：`../service/README.md`",
            "",
        ]
    )


def _render_server_index_readme(server_entries: list[dict[str, object]]) -> str:
    lines = ["# Server Docs Index", ""]
    if not server_entries:
        lines.append("暂无机器文档")
    else:
        for entry in server_entries:
            host = str(entry["host"])
            service_ids = ", ".join(f"`{item}`" for item in entry["services"])
            lines.append(f"- [{host}]({host}/README.md) · {service_ids}")
    lines.append("")
    return "\n".join(lines)


def _render_service_index_readme(service_entries: list[dict[str, object]]) -> str:
    lines = ["# Service Docs Index", ""]
    if not service_entries:
        lines.append("暂无服务文档")
    else:
        for entry in service_entries:
            slug = str(entry["service_slug"])
            lines.append(
                f"- [{entry['service_id']}]({slug}/README.md) · host `{entry['host']}` · {', '.join(entry['domains']) or '-'}"
            )
    lines.append("")
    return "\n".join(lines)


def _render_server_readme(server_entry: dict[str, object], service_entries: list[dict[str, object]]) -> str:
    host = str(server_entry["host"])
    target_path = Path(str(server_entry["target_path"]))
    docs_root = target_path.parents[2]
    lines = [f"# {host}", "", "## 机器概览", f"- hostname：`{host}`", f"- 文档中心根目录：`{docs_root.parent}`"]
    if server_entry.get("source_host"):
        lines.append(f"- 原 hostname：`{server_entry['source_host']}`")
    lines.extend(["", "## 承载服务", ""])
    for entry in service_entries:
        if entry["host"] != host:
            continue
        lines.append(f"- [`{entry['service_id']}`](../../../service/{entry['service_slug']}/README.md)")
    lines.extend(["", "## 说明", "- 文档中心由 master 统一维护", ""])
    return "\n".join(lines)


def _render_service_readme(entry: dict[str, object], service: ServiceRecord) -> str:
    monitor = _sanitize_value(service.monitor)
    monitor_text = json.dumps(monitor, ensure_ascii=False, indent=2, sort_keys=True) if monitor else "{}"
    warnings = list(service.warnings) if service.warnings else ["无"]
    warnings_text = "\n".join(f"- {_redact_text(str(item))}" for item in warnings)
    domains = ", ".join(f"`{domain}`" for domain in service.domains) if service.domains else "无"
    ports = ", ".join(f"`{port}`" for port in service.ports) if service.ports else "无"
    docs_path = _redact_text(service.docs_path) if service.docs_path else str(entry["target_path"])
    target_path = Path(str(entry["target_path"]))
    docs_root = target_path.parents[2]
    config_paths = _render_inline_paths(service.config_paths)
    env_paths = _render_inline_paths(service.env_paths)
    data_paths = _render_inline_paths(service.data_paths)
    dependencies = _render_inline_strings(_metadata_string_list(service.metadata, "dependencies"))
    operations = _operation_sections(service)
    troubleshooting = _metadata_string_list(service.metadata, "troubleshooting")
    lines = [
        f"# {service.name}",
        "",
        "## 服务概览",
        f"- service_id：`{service.service_id}`",
        f"- slug：`{entry['service_slug']}`",
        f"- host：`{entry['host']}`",
        f"- 文档中心根目录：`{docs_root}`",
        f"- kind：`{service.kind}`",
        f"- runtime：`{service.runtime or 'unknown'}`",
        f"- domains：{domains}",
        f"- ports：{ports}",
        f"- source：`{_redact_text(service.source)}`",
        f"- docs_path：`{docs_path}`",
        "",
        "## 部署信息",
        f"- 文档中心根目录：`{docs_root}`",
        f"- 部署路径：`{_redact_text(service.deploy_path) or '未登记'}`",
        f"- 配置文件：{config_paths}",
        f"- 环境文件：{env_paths}",
        f"- 数据目录：{data_paths}",
        f"- 依赖：{dependencies}",
        f"- 健康检查：`{_redact_text(service.health_check_url) or '未登记'}`",
        "",
        "## 启停命令",
        "### 启动",
        *_render_bullets(operations.get("start") or ["未登记"]),
        "",
        "### 停止",
        *_render_bullets(operations.get("stop") or ["未登记"]),
        "",
        "## 风险 / 提示",
        warnings_text,
        "",
        "## 监控摘要",
        "```json",
        monitor_text,
        "```",
        "",
        "## 维护 Runbook",
        "### 巡检",
        *_render_bullets(operations.get("inspect") or [service.health_check_url or "未登记"]),
        "",
        "### 日志",
        *_render_bullets(operations.get("logs") or ["journalctl -u <service> -n 200 --no-pager"]),
        "",
        "### 重启",
        *_render_bullets(operations.get("restart") or ["未登记"]),
        "",
        "### 升级",
        *_render_bullets(operations.get("upgrade") or ["未登记"]),
        "",
        "### 备份",
        *_render_bullets(operations.get("backup") or ["未登记"]),
        "",
        "### 恢复",
        *_render_bullets(operations.get("restore") or ["未登记"]),
        "",
        "### 回滚",
        *_render_bullets(operations.get("rollback") or ["未登记"]),
        "",
        "### 常见故障处理",
        *_render_bullets(troubleshooting or ["未登记"]),
        "",
        "## 后续接入架构",
        "- 节点后续通过 master API / worker 上报 facts/service registry",
        "- master 负责脱敏、规范化、落盘、索引、audit",
        "- 当前版本未开放公网 submit API",
        "",
    ]
    return "\n".join(lines)


def _json_dumps(payload: object) -> str:
    return json.dumps(_sanitize_value(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _render_inline_paths(values: list[str]) -> str:
    return _render_inline_strings(values)


def _render_inline_strings(values: list[str]) -> str:
    cleaned = [_redact_text(str(value)) for value in values if str(value).strip()]
    if not cleaned:
        return "未登记"
    return ", ".join(f"`{value}`" for value in cleaned)


def _render_bullets(values: list[str]) -> list[str]:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return ["- 未登记"]
    rendered: list[str] = []
    for item in items:
        text = _redact_text(item)
        if _looks_like_command(item):
            rendered.append(f"- `{text}`")
        else:
            rendered.append(f"- {text}")
    return rendered


def _looks_like_command(value: str) -> bool:
    stripped = value.strip()
    command_prefixes = (
        "curl ",
        "docker ",
        "systemctl ",
        "git ",
        "restic ",
        "journalctl ",
        "ss ",
        "cat ",
        "python ",
        "bash ",
        "sh ",
    )
    return stripped.startswith(command_prefixes)


def _metadata_string_list(metadata: dict[str, object], key: str) -> list[str]:
    raw = metadata.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def _operation_sections(service: ServiceRecord) -> dict[str, list[str]]:
    raw = service.metadata.get("operations") if isinstance(service.metadata, dict) else None
    if not isinstance(raw, dict):
        return {}
    sections: dict[str, list[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            sections[str(key)] = [str(item) for item in value if str(item).strip()]
    return sections


def _relative_from_root(root_resolved: Path, target_path: Path) -> Path:
    target_resolved = target_path.resolve(strict=False)
    if root_resolved == target_resolved:
        return Path(".")
    try:
        return target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"target escapes root: {target_path}") from exc


def _resolve_target(root_resolved: Path, relative_target: Path) -> Path:
    target = (root_resolved / relative_target).resolve(strict=False)
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError(f"target escapes root: {target}")
    return target


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
