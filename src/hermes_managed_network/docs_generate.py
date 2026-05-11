from __future__ import annotations

import json
import re
from pathlib import Path

from .service_registry import ServiceRecord, ServiceRegistry

SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b([a-z0-9_-]*(?:token|password|api[_-]?key|secret)[a-z0-9_-]*)\s*=\s*([^\s,;]+)"
)
SENSITIVE_JSON_KEY_PATTERN = re.compile(
    r'(?i)("?[a-z0-9_-]*(?:token|password|api[_-]?key|secret)[a-z0-9_-]*"?\s*:\s*)("[^"]*"|[^,}\]]+)'
)
SENSITIVE_HEADERS_PATTERN = re.compile(
    r"(?i)\b(authorization|bearer|passwd|pwd)\b\s*[:=]\s*([^,;\n]+)"
)


def generate_docs_from_registry(registry: ServiceRegistry, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    service_dir = output_dir / "service"
    domains_dir = output_dir / "domains"
    runbooks_dir = output_dir / "runbooks"
    service_dir.mkdir(parents=True, exist_ok=True)
    domains_dir.mkdir(parents=True, exist_ok=True)
    runbooks_dir.mkdir(parents=True, exist_ok=True)

    services = registry.list_services()
    service_entries: list[tuple[ServiceRecord, Path]] = []
    for service in services:
        relative_doc_path = _relative_doc_path(service)
        service_path = output_dir / relative_doc_path
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(render_service_doc(service), encoding="utf-8")
        service_entries.append((service, relative_doc_path))

    (service_dir / "README.md").write_text(render_service_index(service_entries), encoding="utf-8")
    (domains_dir / "README.md").write_text(render_domains_index(service_entries), encoding="utf-8")
    (runbooks_dir / "README.md").write_text(render_runbook_index(service_entries), encoding="utf-8")
    return output_dir


def load_registry_and_generate_docs(registry_path: Path, output_dir: Path) -> Path:
    registry = ServiceRegistry.load(registry_path)
    return generate_docs_from_registry(registry, output_dir)


def render_service_doc(service: ServiceRecord) -> str:
    generated_doc_path = _relative_doc_path(service).as_posix()
    domains = ", ".join(f"`{domain}`" for domain in service.domains) if service.domains else "无"
    ports = ", ".join(f"`{port}`" for port in service.ports) if service.ports else "无"
    warnings = "\n".join(f"- {warning}" for warning in service.warnings) if service.warnings else "- 无"
    monitor = _render_monitor(service)
    lines = [
        f"# {service.name}\n",
        "## 服务概览",
        f"- 服务名：`{service.name}`",
        f"- 承载节点：`{service.node}`",
        f"- 类型：`{service.kind}`",
        f"- 运行时：`{service.runtime or 'unknown'}`",
        f"- 域名：{domains}",
        f"- 端口：{ports}",
        f"- 来源：`{_redact_text(service.source)}`",
        f"- 文档路径：`{service.docs_path or generated_doc_path}`",
        f"- 生成路径：`{generated_doc_path}`",
        "",
        "## 告警 / 风险提示",
        warnings,
        "",
        "## 监控信息",
        monitor,
        "",
        "## 常用运维命令占位",
        "```bash",
        "systemctl status <service>",
        "journalctl -u <service> -n 200 --no-pager",
        "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'",
        "ss -lntp",
        "```",
        "",
    ]
    return "\n".join(lines)


def render_service_index(service_entries: list[tuple[ServiceRecord, Path]]) -> str:
    lines = ["# Service Index", ""]
    if not service_entries:
        lines.append("暂无服务")
    else:
        for service, path in service_entries:
            domains = ", ".join(service.domains) if service.domains else "-"
            lines.append(f"- [{service.name}]({path.name}) · {service.node} · {service.kind} · {domains}")
    lines.append("")
    return "\n".join(lines)


def render_domains_index(service_entries: list[tuple[ServiceRecord, Path]]) -> str:
    lines = ["# Domains Index", ""]
    items: list[str] = []
    for service, path in service_entries:
        for domain in service.domains:
            items.append(f"- `{domain}` -> [{service.name}](../{path.as_posix()})")
    if items:
        lines.extend(sorted(items))
    else:
        lines.append("暂无域名")
    lines.append("")
    return "\n".join(lines)


def render_runbook_index(service_entries: list[tuple[ServiceRecord, Path]]) -> str:
    lines = ["# Runbook Index", ""]
    if not service_entries:
        lines.append("暂无服务")
    else:
        for service, path in service_entries:
            lines.append(f"- [{service.name}](../{path.as_posix()}) · 节点 `{service.node}` · 查看常用运维命令占位")
    lines.append("")
    return "\n".join(lines)


def _relative_doc_path(service: ServiceRecord) -> Path:
    safe_name = _safe_service_slug(service.name)
    return Path("service") / f"{safe_name}.md"


def _safe_service_slug(name: str) -> str:
    candidates = [segment for segment in re.split(r"[\\/]+", name) if segment not in {"", ".", ".."}]
    base = candidates[-1] if candidates else "service"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-._")
    return slug or "service"


def _render_monitor(service: ServiceRecord) -> str:
    if not service.monitor:
        return "- 无"
    sanitized = _sanitize_value(service.monitor)
    return "```json\n" + json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"


def _sanitize_value(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    value = SENSITIVE_KEY_PATTERN.sub("[REDACTED]", text)
    value = SENSITIVE_JSON_KEY_PATTERN.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
    value = SENSITIVE_HEADERS_PATTERN.sub(lambda match: f"{match.group(1)}: [REDACTED]", value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in ("token", "password", "api_key", "secret", "passwd", "pwd"))
