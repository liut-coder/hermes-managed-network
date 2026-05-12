from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServiceRecord:
    service_id: str
    name: str
    node: str
    kind: str
    domains: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    runtime: str = ""
    source: str = "manual"
    monitor: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, ServiceRecord] = {}

    def upsert(self, service: ServiceRecord) -> None:
        self._services[service.service_id] = service

    def list_services(self) -> list[ServiceRecord]:
        return list(self._services.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "services": [service.to_dict() for service in self.list_services()],
        }
