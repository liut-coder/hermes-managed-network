from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_SERVICE_REGISTRY_PATH = Path("/var/lib/hermes-managed-network/service-registry.json")


@dataclass
class ServiceRecord:
    service_id: str
    name: str
    node: str
    kind: str
    domains: list[str]
    ports: list[int]
    runtime: str | None
    source: str
    docs_path: str | None = None
    monitor: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ServiceRecord":
        return cls(
            service_id=str(data.get("service_id", "")),
            name=str(data.get("name", "")),
            node=str(data.get("node", "")),
            kind=str(data.get("kind", "unknown")),
            domains=[str(item) for item in data.get("domains", [])],
            ports=sorted({int(item) for item in data.get("ports", [])}),
            runtime=str(data["runtime"]) if data.get("runtime") is not None else None,
            source=str(data.get("source", "")),
            docs_path=str(data["docs_path"]) if data.get("docs_path") is not None else None,
            monitor=dict(data.get("monitor", {})),
            warnings=[str(item) for item in data.get("warnings", [])],
        )


class ServiceRegistry:
    def __init__(self, services: list[ServiceRecord] | None = None) -> None:
        self._services: dict[str, ServiceRecord] = {}
        for service in services or []:
            self.upsert(service)

    def upsert(self, service: ServiceRecord) -> ServiceRecord:
        if not service.service_id.strip():
            raise ValueError("service_id is required")
        existing = self._services.get(service.service_id)
        if existing is None:
            normalized = ServiceRecord.from_dict(service.to_dict())
            normalized.domains = _merge_unique([], normalized.domains)
            normalized.ports = sorted(set(normalized.ports))
            normalized.warnings = _merge_unique([], normalized.warnings)
            self._services[service.service_id] = normalized
            return normalized

        existing.name = service.name or existing.name
        existing.node = service.node or existing.node
        existing.kind = service.kind or existing.kind
        existing.runtime = service.runtime if service.runtime is not None else existing.runtime
        existing.source = service.source or existing.source
        existing.docs_path = service.docs_path if service.docs_path is not None else existing.docs_path
        existing.domains = _merge_unique(existing.domains, service.domains)
        existing.ports = sorted(set(existing.ports).union(service.ports))
        existing.monitor.update(service.monitor)
        existing.warnings = _merge_unique(existing.warnings, service.warnings)
        return existing

    def list_services(self) -> list[ServiceRecord]:
        return [self._services[key] for key in sorted(self._services)]

    def to_dict(self) -> dict[str, object]:
        return {"services": [service.to_dict() for service in self.list_services()]}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ServiceRegistry":
        return cls([ServiceRecord.from_dict(item) for item in data.get("services", [])])

    def save(self, path: Path = DEFAULT_SERVICE_REGISTRY_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as tmp_file:
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            Path(tmp_name).replace(path)
        finally:
            tmp_path = Path(tmp_name)
            if tmp_path.exists():
                tmp_path.unlink()

    @classmethod
    def load(cls, path: Path = DEFAULT_SERVICE_REGISTRY_PATH) -> "ServiceRegistry":
        if not path.exists():
            return cls()
        return cls.from_dict(json.loads(path.read_text()))


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    merged: list[str] = []
    for item in [*left, *right]:
        if item not in merged:
            merged.append(item)
    return merged
