from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable
import re

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYWORDS = (
    "api_key",
    "apikey",
    "authorization",
    "token",
    "secret",
    "password",
    "passwd",
    "private_key",
    "client_secret",
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([a-z0-9_\-]*(?:token|secret|password|api[_-]?key|authorization)[a-z0-9_\-]*)=([^\s,;]+)"
)


class ProviderOperationStatus(StrEnum):
    OK = "ok"
    PLANNED = "planned"
    NOT_IMPLEMENTED = "not_implemented"
    ERROR = "error"



def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS)



def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (_REDACTED if _looks_sensitive_key(str(key)) else redact_sensitive_data(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{_REDACTED}", value)
    return value


@dataclass(slots=True)
class ManagedProviderPlan:
    provider_id: str
    operation: str
    intent: str
    mutating: bool = False
    risk: str = "low"
    dry_run: bool = True
    approval_required: bool = False
    summary: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    rollback_hint: str = ""

    def sanitized(self) -> dict[str, Any]:
        return redact_sensitive_data(asdict(self))

    def audit_payload(self) -> dict[str, Any]:
        payload = self.sanitized()
        return {
            "provider_id": payload["provider_id"],
            "operation": payload["operation"],
            "intent": payload["intent"],
            "mutating": payload["mutating"],
            "risk": payload["risk"],
            "approval_required": payload["approval_required"],
            "dry_run": payload["dry_run"],
            "summary": payload["summary"],
            "steps": payload["steps"],
            "context": payload["context"],
            "metadata": payload["metadata"],
            "rollback_hint": payload["rollback_hint"],
        }


@dataclass(slots=True)
class ManagedProviderResult:
    operation: str
    status: ProviderOperationStatus
    summary: str
    changed: bool = False
    dry_run: bool = True
    approval_required: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def sanitized(self) -> dict[str, Any]:
        return redact_sensitive_data(asdict(self))


@runtime_checkable
class ManagedProviderProtocol(Protocol):
    provider_id: str
    display_name: str

    def discover(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult: ...
    def plan(self, *, intent: str, config: dict[str, object] | None = None) -> ManagedProviderPlan: ...
    def apply(self, *, plan: ManagedProviderPlan, config: dict[str, object] | None = None) -> ManagedProviderResult: ...
    def verify(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult: ...
    def status(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult: ...
    def rollback(self, *, plan: ManagedProviderPlan, config: dict[str, object] | None = None) -> ManagedProviderResult: ...


class ManagedProvider(ABC):
    provider_id: str
    display_name: str

    @abstractmethod
    def discover(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult:
        raise NotImplementedError

    @abstractmethod
    def plan(self, *, intent: str, config: dict[str, object] | None = None) -> ManagedProviderPlan:
        raise NotImplementedError

    @abstractmethod
    def verify(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult:
        raise NotImplementedError

    @abstractmethod
    def status(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult:
        raise NotImplementedError

    def apply(self, *, plan: ManagedProviderPlan, config: dict[str, object] | None = None) -> ManagedProviderResult:
        config = dict(config or {})
        approval_request_id = config.get("approval_request_id")
        audit_event_id = config.get("audit_event_id")
        if approval_request_id:
            return ManagedProviderResult(
                operation="apply",
                status=ProviderOperationStatus.NOT_IMPLEMENTED,
                summary=f"{plan.provider_id} provider apply is delegated to the approval/audit execution path",
                changed=False,
                dry_run=True,
                approval_required=False,
                details={
                    "provider_id": plan.provider_id,
                    "risk": plan.risk,
                    "approval_request_id": approval_request_id,
                    "plan": plan.sanitized(),
                },
                metadata={
                    "audit_event_id": audit_event_id,
                    "placeholder": True,
                    "not_executable": True,
                },
                warnings=["provider contract placeholder does not perform external writes"],
            )
        return ManagedProviderResult(
            operation="apply",
            status=ProviderOperationStatus.PLANNED,
            summary=f"{plan.provider_id} provider apply requires approval before execution",
            changed=False,
            dry_run=True,
            approval_required=plan.approval_required or plan.mutating or plan.risk in {"high", "critical"},
            details={
                "provider_id": plan.provider_id,
                "risk": plan.risk,
                "plan": plan.sanitized(),
            },
            metadata={
                "contract": "placeholder",
                "placeholder": True,
                "not_executable": True,
                "audit_event_id": audit_event_id,
            },
            warnings=["mutation is blocked by the provider contract placeholder"],
        )

    def rollback(self, *, plan: ManagedProviderPlan, config: dict[str, object] | None = None) -> ManagedProviderResult:
        config = dict(config or {})
        approval_request_id = config.get("approval_request_id")
        audit_event_id = config.get("audit_event_id")
        if approval_request_id:
            return ManagedProviderResult(
                operation="rollback",
                status=ProviderOperationStatus.NOT_IMPLEMENTED,
                summary=f"{plan.provider_id} provider rollback is delegated to the approval/audit execution path",
                changed=False,
                dry_run=True,
                approval_required=False,
                details={
                    "provider_id": plan.provider_id,
                    "risk": plan.risk,
                    "approval_request_id": approval_request_id,
                    "rollback_hint": plan.rollback_hint,
                    "plan": plan.sanitized(),
                },
                metadata={
                    "audit_event_id": audit_event_id,
                    "placeholder": True,
                    "not_executable": True,
                },
                warnings=["provider contract placeholder does not perform external writes"],
            )
        return ManagedProviderResult(
            operation="rollback",
            status=ProviderOperationStatus.PLANNED,
            summary=f"{plan.provider_id} provider rollback requires approval before execution",
            changed=False,
            dry_run=True,
            approval_required=plan.approval_required or plan.mutating or plan.risk in {"high", "critical"},
            details={
                "provider_id": plan.provider_id,
                "risk": plan.risk,
                "approval_request_id": approval_request_id,
                "rollback_hint": plan.rollback_hint,
                "plan": plan.sanitized(),
            },
            metadata={
                "audit_event_id": audit_event_id,
                "placeholder": True,
                "not_executable": True,
            },
            warnings=["mutation is blocked by the provider contract placeholder"],
        )
