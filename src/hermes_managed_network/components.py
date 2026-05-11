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
_ALLOWED_API_VERSIONS = {1}
_ALLOWED_RISKS = {"low", "medium", "high"}
_REQUIRED_PLAYBOOKS = {"install", "configure", "verify", "uninstall"}


def _require_mapping(data: Any, field: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"component manifest {field} must be a mapping")
    return data


def _require_non_empty_string(data: dict[str, Any], field: str) -> None:
    if not isinstance(data.get(field), str) or not data[field].strip():
        raise ValueError(f"component manifest {field} must be a non-empty string")


def _validate_manifest_data(data: dict[str, Any]) -> None:
    missing = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError("component manifest missing required fields: " + ", ".join(missing))

    for field in ("id", "name", "version"):
        _require_non_empty_string(data, field)

    if data["api_version"] not in _ALLOWED_API_VERSIONS:
        raise ValueError(f"component manifest api_version must be one of {sorted(_ALLOWED_API_VERSIONS)}")

    if data["risk"] not in _ALLOWED_RISKS:
        raise ValueError(f"component manifest risk must be one of {sorted(_ALLOWED_RISKS)}")

    _require_mapping(data["requires"], "requires")
    _require_mapping(data["provides"], "provides")

    config_schema = _require_mapping(data.get("config_schema") or {}, "config_schema")
    required_config = config_schema.get("required") or []
    properties = config_schema.get("properties") or {}
    if not isinstance(required_config, list):
        raise ValueError("component manifest config_schema.required must be a list")
    if not isinstance(properties, dict):
        raise ValueError("component manifest config_schema.properties must be a mapping")
    missing_properties = [name for name in required_config if name not in properties]
    if missing_properties:
        raise ValueError("component manifest config_schema.required entries missing properties: " + ", ".join(missing_properties))

    drivers = _require_mapping(data["drivers"], "drivers")
    driver_options = drivers.get("options")
    default_driver = drivers.get("default")
    if not isinstance(driver_options, list) or not driver_options:
        raise ValueError("component manifest drivers.options must be a non-empty list")
    if default_driver not in driver_options:
        raise ValueError("component manifest drivers.default must be included in drivers.options")

    playbooks = _require_mapping(data["playbooks"], "playbooks")
    missing_playbooks = sorted(_REQUIRED_PLAYBOOKS - set(playbooks))
    if missing_playbooks:
        raise ValueError("component manifest missing playbooks." + ", playbooks.".join(missing_playbooks))
    for key in _REQUIRED_PLAYBOOKS:
        if not isinstance(playbooks.get(key), str) or not playbooks[key].strip():
            raise ValueError(f"component manifest playbooks.{key} must be a non-empty string")

    audit = _require_mapping(data["audit"], "audit")
    if not isinstance(audit.get("category"), str) or not audit["category"].strip():
        raise ValueError("component manifest audit.category must be a non-empty string")


def _from_dict(data: dict[str, Any], *, source: str) -> ComponentManifest:
    _validate_manifest_data(data)
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


class ComponentRegistry:
    def __init__(self, components: dict[str, ComponentManifest] | None = None) -> None:
        self._components = dict(sorted((components or {}).items()))

    @classmethod
    def from_builtin(cls) -> "ComponentRegistry":
        return cls(load_builtin_components())

    def list(self) -> list[ComponentManifest]:
        return list(self._components.values())

    def get(self, component_id: str) -> ComponentManifest:
        try:
            return self._components[component_id]
        except KeyError as exc:
            raise KeyError(f"component not found: {component_id}") from exc

    def validate(self, component_id: str) -> ComponentManifest:
        component = self.get(component_id)
        _validate_manifest_data(component.manifest_json)
        return component
