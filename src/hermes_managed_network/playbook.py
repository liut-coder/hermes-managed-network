from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import yaml


@dataclass
class Playbook:
    id: str
    name: str
    risk: str
    permissions: list[str]
    inputs: dict[str, dict[str, Any]]
    precheck: list[str]
    backup: list[str]
    action: list[str]
    verify: list[str]
    rollback_hint: list[str]

    @classmethod
    def load(cls, path: Path) -> "Playbook":
        data = yaml.safe_load(path.read_text())
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            risk=data["risk"],
            permissions=list(data.get("permissions", [])),
            inputs=dict(data.get("inputs", {})),
            precheck=list(data.get("precheck", [])),
            backup=list(data.get("backup", [])),
            action=list(data.get("action", [])),
            verify=list(data.get("verify", [])),
            rollback_hint=list(data.get("rollback_hint", [])),
        )

    def render_phase(self, phase: str, values: dict[str, str]) -> list[str]:
        self._validate_required_inputs(values)
        commands = getattr(self, phase)
        return [_render(command, values) for command in commands]

    def _validate_required_inputs(self, values: dict[str, str]) -> None:
        for name, spec in self.inputs.items():
            if spec.get("required") and name not in values:
                raise ValueError(f"missing required input: {name}")


def _render(command: str, values: dict[str, str]) -> str:
    rendered = command
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", value)
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered
