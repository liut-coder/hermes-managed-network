from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ComponentManifest:
    id: str
    name: str
    version: str
    api_version: int
    summary: str
    risk: str
    requires: dict[str, Any]
    provides: dict[str, Any]
    permissions: dict[str, Any]
    config_schema: dict[str, Any]
    drivers: dict[str, Any]
    playbooks: dict[str, Any]
    health: dict[str, Any]
    audit: dict[str, Any]
    source: str = "builtin"

    @property
    def manifest_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "api_version": self.api_version,
            "summary": self.summary,
            "risk": self.risk,
            "requires": self.requires,
            "provides": self.provides,
            "permissions": self.permissions,
            "config_schema": self.config_schema,
            "drivers": self.drivers,
            "playbooks": self.playbooks,
            "health": self.health,
            "audit": self.audit,
            "source": self.source,
        }


_REQUIRED_FIELDS = ["id", "name", "version", "api_version", "risk", "requires", "provides", "drivers", "playbooks", "audit"]


def _from_dict(data: dict[str, Any], *, source: str) -> ComponentManifest:
    missing = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError("component manifest missing required fields: " + ", ".join(missing))
    return ComponentManifest(
        id=str(data["id"]),
        name=str(data["name"]),
        version=str(data["version"]),
        api_version=int(data["api_version"]),
        summary=str(data.get("summary", "")),
        risk=str(data["risk"]),
        requires=dict(data.get("requires") or {}),
        provides=dict(data.get("provides") or {}),
        permissions=dict(data.get("permissions") or {}),
        config_schema=dict(data.get("config_schema") or {}),
        drivers=dict(data.get("drivers") or {}),
        playbooks=dict(data.get("playbooks") or {}),
        health=dict(data.get("health") or {}),
        audit=dict(data.get("audit") or {}),
        source=source,
    )


def load_component_manifest(path: str | Path) -> ComponentManifest:
    manifest_path = Path(path)
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("component manifest must be a mapping")
    return _from_dict(data, source=str(manifest_path))


def load_builtin_components() -> dict[str, ComponentManifest]:
    root = resources.files("hermes_managed_network").joinpath("components")
    components: dict[str, ComponentManifest] = {}
    for manifest in root.rglob("component.yaml"):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        component = _from_dict(data, source="builtin")
        components[component.id] = component
    return dict(sorted(components.items()))
