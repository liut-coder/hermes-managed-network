from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_SERVICE_REGISTRY_PATH = Path("service-registry.json")


@dataclass(frozen=True)
class ServiceRecord:
    service_id: str
    name: str
    node: str
    kind: str
    domains: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    runtime: str | None = ""
    source: str = "manual"
    docs_path: str | None = ""
    monitor: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ServiceRecord":
        return cls(
            service_id=str(payload["service_id"]),
            name=str(payload.get("name") or payload["service_id"]),
            node=str(payload.get("node") or payload.get("node_id") or ""),
            kind=str(payload.get("kind") or "unknown"),
            domains=[str(domain) for domain in payload.get("domains", [])],
            ports=[int(port) for port in payload.get("ports", [])],
            runtime=None if payload.get("runtime") is None else str(payload.get("runtime", "")),
            source=str(payload.get("source") or "manual"),
            docs_path=None if payload.get("docs_path") is None else str(payload.get("docs_path", "")),
            monitor=dict(payload.get("monitor") or {}),
            warnings=[str(warning) for warning in payload.get("warnings", [])],
        )


class ServiceRegistry:
    def __init__(self, services: list[ServiceRecord] | None = None) -> None:
        self._services: dict[str, ServiceRecord] = {}
        for service in services or []:
            self.upsert(service)

    def upsert(self, service: ServiceRecord) -> None:
        self._services[service.service_id] = service

    def list_services(self) -> list[ServiceRecord]:
        return list(self._services.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "services": [service.to_dict() for service in self.list_services()],
        }

    def save(self, path: str | Path = DEFAULT_SERVICE_REGISTRY_PATH) -> Path:
        registry_path = Path(path)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return registry_path

    @classmethod
    def load(cls, path: str | Path = DEFAULT_SERVICE_REGISTRY_PATH) -> "ServiceRegistry":
        registry_path = Path(path)
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        services = payload.get("services", []) if isinstance(payload, dict) else []
        return cls([ServiceRecord.from_dict(service) for service in services if isinstance(service, dict)])
