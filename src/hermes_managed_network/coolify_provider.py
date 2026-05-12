from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

from .sanitize import _redact_text, _sanitize_value
from .service_registry import ServiceRecord, ServiceRegistry

Transport = Callable[[str, str], object]


@dataclass(frozen=True)
class CoolifyConfig:
    base_url: str
    api_token: str
    project_uuid: str
    environment_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    @property
    def api_root(self) -> str:
        return f"{self.base_url}/api/v1"


@dataclass(frozen=True)
class CoolifyApplicationSpec:
    name: str
    git_repository: str
    git_branch: str = "main"
    domains: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


class CoolifyProvider:
    def __init__(
        self,
        config: CoolifyConfig,
        *,
        transport: Callable[[str, str], object] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or self._missing_transport

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def build_url(self, path: str) -> str:
        return urljoin(f"{self.config.api_root}/", path.lstrip("/"))

    def build_application_payload(self, spec: CoolifyApplicationSpec) -> dict[str, object]:
        return {
            "name": spec.name,
            "project_uuid": self.config.project_uuid,
            "environment_name": self.config.environment_name,
            "source": {
                "type": "git",
                "repository": spec.git_repository,
                "branch": spec.git_branch,
            },
            "domains": spec.domains,
            "ports": spec.ports,
            "env": spec.env,
        }

    def request(self, method: str, path: str, *, json: dict | None = None):
        return self._transport(method, self.build_url(path), headers=self.headers, json=json)

    @staticmethod
    def _missing_transport(method: str, url: str, *, headers: dict[str, str], json: dict | None = None):
        raise RuntimeError(
            "CoolifyProvider transport is not configured; inject a transport for tests or wire an HTTP client in CLI/API code"
        )


def load_coolify_fixture(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_coolify_services_from_fixture(
    path: Path,
    *,
    source_label: str = "coolify-fixture",
) -> ServiceRegistry:
    payload = load_coolify_fixture(path)
    server_name = _server_name(payload)
    project_name = _project_name(payload)
    environment_name = _environment_name(payload)
    source = f"{source_label}:{path}"
    registry = ServiceRegistry()

    for app in payload.get("applications", []):
        if not isinstance(app, dict):
            continue
        service = _service_from_application(
            app,
            server_name=server_name,
            project_name=project_name,
            environment_name=environment_name,
            source=source,
        )
        registry.upsert(service)
    return registry


def build_coolify_sync_dry_run(path: Path) -> dict[str, object]:
    registry = discover_coolify_services_from_fixture(path)
    source = f"coolify-fixture:{path}"
    return {
        "provider": "coolify",
        "mode": "dry-run",
        "write": False,
        "source": source,
        "service_count": len(registry.list_services()),
        "services": [service.to_dict() for service in registry.list_services()],
        "registry": registry.to_dict(),
    }


def _service_from_application(
    app: dict[str, object],
    *,
    server_name: str,
    project_name: str,
    environment_name: str,
    source: str,
) -> ServiceRecord:
    app_name = str(app.get("name") or app.get("uuid") or "coolify-app")
    app_id = str(app.get("uuid") or app_name)
    domains = _domains(app.get("domains"))
    ports = _ports(app.get("ports"))
    runtime = _runtime_summary(app)
    status = str(app.get("status") or "unknown")
    deploy_target = _sanitize_deploy_target(dict(app.get("deploy_target", {})))
    env_summary = _sanitize_env(dict(app.get("env", {})))
    warnings = _warnings(app_name=app_name, domains=domains, ports=ports, status=status)

    return ServiceRecord(
        service_id=f"coolify:{server_name}:{app_id}",
        name=app_name,
        node=server_name,
        kind="coolify",
        domains=domains,
        ports=ports,
        runtime=runtime,
        source=source,
        monitor={
            "status": status,
            "project": project_name,
            "environment": environment_name,
            "deploy_target": deploy_target,
            "env_summary": env_summary,
            "git_branch": str(app.get("git_branch") or "main"),
            "git_repository": _sanitize_value(str(app.get("git_repository") or "unknown")),
        },
        warnings=warnings,
    )


def _server_name(payload: dict[str, object]) -> str:
    server = payload.get("server")
    if isinstance(server, dict) and server.get("name"):
        return str(server["name"])
    return "coolify-server"


def _project_name(payload: dict[str, object]) -> str:
    project = payload.get("project")
    if isinstance(project, dict):
        return str(project.get("name") or project.get("uuid") or "coolify-project")
    return "coolify-project"


def _environment_name(payload: dict[str, object]) -> str:
    environment = payload.get("environment")
    if isinstance(environment, dict) and environment.get("name"):
        return str(environment["name"])
    return "production"


def _domains(raw: object) -> list[str]:
    result: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("domain"):
                result.append(str(item["domain"]))
            elif isinstance(item, str) and item.strip():
                result.append(item.strip())
    return result


def _ports(raw: object) -> list[int]:
    values: set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            try:
                values.add(int(item))
            except (TypeError, ValueError):
                continue
    return sorted(values)


def _runtime_summary(app: dict[str, object]) -> str:
    repository = _redact_text(app.get("git_repository") or "unknown")
    branch = _redact_text(app.get("git_branch") or "main")
    return f"repo:{repository}#{branch}"


def _sanitize_deploy_target(deploy_target: dict[str, object]) -> dict[str, object]:
    sanitized = _sanitize_value(deploy_target)
    if not isinstance(sanitized, dict):
        return {}
    for key, value in list(sanitized.items()):
        if isinstance(value, str):
            lowered = key.lower().replace("-", "_")
            if lowered in {"authorization", "bearer", "access_token", "refresh_token", "api_token", "token", "password", "api_key", "secret"}:
                sanitized[key] = "[REDACTED]"
            else:
                redacted = _redact_text(value)
                sanitized[key] = "[REDACTED]" if "[REDACTED]" in redacted and redacted != value else redacted
    return sanitized


def _sanitize_env(env: dict[str, object]) -> dict[str, object]:
    sanitized = _sanitize_value(env)
    if not isinstance(sanitized, dict):
        return {}
    for key, value in list(sanitized.items()):
        if isinstance(value, str):
            lowered = key.lower().replace("-", "_")
            if any(marker in lowered for marker in ("token", "password", "api_key", "secret", "authorization", "bearer", "passwd", "pwd")):
                sanitized[key] = "[REDACTED]"
            else:
                redacted = _redact_text(value)
                sanitized[key] = "[REDACTED]" if "[REDACTED]" in redacted and redacted != value else redacted
    return sanitized


def _warnings(*, app_name: str, domains: list[str], ports: list[int], status: str) -> list[str]:
    warnings: list[str] = []
    if not domains:
        warnings.append(f"coolify app {app_name} has no domains")
    if not ports:
        warnings.append(f"coolify app {app_name} has no ports")
    if status.lower() not in {"running", "healthy", "ready"}:
        warnings.append(f"coolify app {app_name} status={status}")
    return warnings
