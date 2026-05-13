from __future__ import annotations

import json
from pathlib import Path

from .platforms import NodeRuntimeProfile
from .providers import redact_sensitive_data
from .service_registry import ServiceRegistry

SKELETON_ACTION_RESULT = {
    "approval_required": True,
    "not_executed": True,
    "machine_changed": False,
}

ALLOWED_TARGET_ROLES = {
    NodeRuntimeProfile.FULL_WORKER.value,
    NodeRuntimeProfile.LITE_WORKER.value,
    NodeRuntimeProfile.BEACON_ONLY.value,
    NodeRuntimeProfile.PROXY_MANAGED.value,
}


def build_onboarding_plan_from_path(
    nodes_path: Path,
    *,
    service_registry_path: Path | None = None,
    target_role: str | None = None,
) -> dict[str, object]:
    nodes = _load_nodes(nodes_path)
    registry = ServiceRegistry.load(service_registry_path) if service_registry_path else ServiceRegistry()
    return build_onboarding_plan(
        nodes,
        source=str(nodes_path),
        service_registry=registry,
        target_role=target_role,
    )


def render_onboarding_plan_json(
    nodes_path: Path,
    *,
    service_registry_path: Path | None = None,
    target_role: str | None = None,
) -> str:
    payload = build_onboarding_plan_from_path(
        nodes_path,
        service_registry_path=service_registry_path,
        target_role=target_role,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_onboarding_plan(
    nodes: list[dict[str, object]],
    *,
    source: str,
    service_registry: ServiceRegistry | None = None,
    target_role: str | None = None,
) -> dict[str, object]:
    normalized_target_role = _normalize_target_role(target_role)
    registry = service_registry or ServiceRegistry()
    node_contexts = [
        {
            "node": node,
            "capability_profile": _capability_profile(node),
        }
        for node in nodes
    ]
    for context in node_contexts:
        context["recommended_role"] = _recommended_role(context["capability_profile"])
    node_payloads = [
        _build_node_payload(
            context,
            all_node_contexts=node_contexts,
            registry=registry,
            target_role=normalized_target_role,
        )
        for context in node_contexts
    ]
    return redact_sensitive_data(
        {
            "mode": "plan",
            "dry_run": True,
            "source": source,
            "nodes_count": len(node_payloads),
            "service_count": len(registry.list_services()),
            "target_role": normalized_target_role,
            "service_registry_summary": _service_registry_summary(registry),
            "nodes": node_payloads,
        }
    )


def _load_nodes(path: Path) -> list[dict[str, object]]:
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        return [_coerce_node(item) for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        if isinstance(raw.get("nodes"), list):
            return [_coerce_node(item) for item in raw["nodes"] if isinstance(item, dict)]
        return [_coerce_node(raw)]
    return []


def _coerce_node(node: dict[str, object]) -> dict[str, object]:
    return {str(key): value for key, value in node.items()}


def _normalize_target_role(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in ALLOWED_TARGET_ROLES:
        return normalized
    return None


def _build_node_payload(
    node_context: dict[str, object],
    *,
    all_node_contexts: list[dict[str, object]],
    registry: ServiceRegistry,
    target_role: str | None,
) -> dict[str, object]:
    node = _dict(node_context.get("node"))
    capability_profile = _dict(node_context.get("capability_profile"))
    recommended_role = _string(node_context.get("recommended_role")) or _recommended_role(capability_profile)
    return {
        "node_id": _node_id(node),
        "hostname": _string(node.get("hostname")) or _node_id(node),
        "target_role": target_role,
        "recommended_role": recommended_role,
        "capability_profile": capability_profile,
        "input_summary": redact_sensitive_data(node),
        "onboarding_steps": _onboarding_steps(),
        "warnings": _warnings(capability_profile),
        "migration_target_recommendation": _migration_target_recommendation(
            node_context,
            all_node_contexts=all_node_contexts,
            registry=registry,
            target_role=target_role or recommended_role,
        ),
        "apply": dict(SKELETON_ACTION_RESULT),
    }


def _node_id(node: dict[str, object]) -> str:
    return _string(node.get("node")) or _string(node.get("node_id")) or _string(node.get("hostname")) or "unknown-node"


def _capability_profile(node: dict[str, object]) -> dict[str, object]:
    os_value = _string(node.get("os")) or _string(node.get("os_release")) or "unknown"
    arch_value = _string(node.get("arch")) or _string(node.get("architecture")) or "unknown"
    cpu = _dict(node.get("cpu"))
    memory = _dict(node.get("memory"))
    disk = _dict(node.get("disk"))
    network = _dict(node.get("network"))
    docker = _dict(node.get("docker"))
    systemd = _dict(node.get("systemd"))
    service_manager = _string(node.get("service_manager")) or _service_manager_from_flags(systemd)
    return {
        "os": os_value,
        "arch": arch_value,
        "cpu": {"cores": _int(cpu.get("cores"), default=0)},
        "memory": {"total_mb": _int(memory.get("total_mb") or memory.get("mb"), default=0)},
        "disk": {"free_gb": _float(disk.get("free_gb") or disk.get("available_gb"), default=0.0)},
        "network": {
            "egress": _string(network.get("egress")) or "unknown",
            **({"latency_tier": _string(network.get("latency_tier"))} if _string(network.get("latency_tier")) else {}),
        },
        "service_manager": service_manager or "none",
        "docker_available": _bool(docker.get("available")),
        "systemd_available": _bool(systemd.get("available")) or (service_manager == "systemd"),
        "ssh_port": _int(_dict(node.get("ssh")).get("port"), default=22),
    }


def _service_manager_from_flags(systemd: dict[str, object]) -> str:
    if _bool(systemd.get("available")):
        return "systemd"
    return "none"


def _recommended_role(profile: dict[str, object]) -> str:
    memory_mb = _int(_dict(profile.get("memory")).get("total_mb"), default=0)
    disk_gb = _float(_dict(profile.get("disk")).get("free_gb"), default=0.0)
    cpu_cores = _int(_dict(profile.get("cpu")).get("cores"), default=0)
    network = _dict(profile.get("network"))
    egress = _string(network.get("egress")) or "unknown"
    docker_available = _bool(profile.get("docker_available"))
    systemd_available = _bool(profile.get("systemd_available"))

    if docker_available and systemd_available and memory_mb >= 4096 and disk_gb >= 40 and cpu_cores >= 4 and egress != "unknown":
        return NodeRuntimeProfile.FULL_WORKER.value
    if memory_mb >= 256 and disk_gb >= 4 and cpu_cores >= 1 and egress != "unknown":
        return NodeRuntimeProfile.LITE_WORKER.value
    if memory_mb >= 96 and disk_gb >= 1 and cpu_cores >= 1:
        return NodeRuntimeProfile.BEACON_ONLY.value
    return NodeRuntimeProfile.PROXY_MANAGED.value


def _onboarding_steps() -> list[str]:
    return [
        "install agent",
        "register node",
        "configure network",
        "enable heartbeat",
        "optional worker shell disabled by default",
    ]


def _warnings(profile: dict[str, object]) -> list[str]:
    warnings: list[str] = []
    disk_gb = _float(_dict(profile.get("disk")).get("free_gb"), default=0.0)
    if disk_gb < 10:
        warnings.append("low disk")
    if not _bool(profile.get("docker_available")):
        warnings.append("no docker")
    if not _bool(profile.get("systemd_available")):
        warnings.append("no systemd")
    if (_string(_dict(profile.get("network")).get("egress")) or "unknown") == "unknown":
        warnings.append("unknown network")
    if _int(profile.get("ssh_port"), default=22) == 22:
        warnings.append("ssh port 22 warning")
    return warnings


def _migration_target_recommendation(
    current_node_context: dict[str, object],
    *,
    all_node_contexts: list[dict[str, object]],
    registry: ServiceRegistry,
    target_role: str,
) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    current_node_id = _node_id(_dict(current_node_context.get("node")))
    for context in all_node_contexts:
        node = _dict(context.get("node"))
        profile = _dict(context.get("capability_profile"))
        score, reasons = _candidate_score(profile, registry=registry, target_role=target_role)
        candidates.append(
            {
                "node_id": _node_id(node),
                "score": score,
                "recommended": False,
                "service_registry_considered": bool(registry.list_services()),
                "reasons": reasons,
                "matches_requested_target_role": (_string(context.get("recommended_role")) == target_role),
                "is_current_node": _node_id(node) == current_node_id,
            }
        )
    candidates.sort(key=lambda item: (int(item["score"]), 1 if item["matches_requested_target_role"] else 0, 1 if item["is_current_node"] else 0), reverse=True)
    if candidates:
        candidates[0]["recommended"] = True
    return {
        "target_role": target_role,
        "summary": "local inventory scoring only; no machine connections performed",
        "candidates": candidates,
    }


def _service_registry_summary(registry: ServiceRegistry) -> dict[str, object]:
    services = registry.list_services()
    if not services:
        return {"considered": False, "services": []}
    return {
        "considered": True,
        "services": [
            {
                "service_id": service.service_id,
                "node": service.node,
                "kind": service.kind,
                "runtime": service.runtime,
                "source": service.source,
            }
            for service in services
        ],
    }


def _candidate_score(
    profile: dict[str, object],
    *,
    registry: ServiceRegistry,
    target_role: str,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    memory_mb = _int(_dict(profile.get("memory")).get("total_mb"), default=0)
    disk_gb = _float(_dict(profile.get("disk")).get("free_gb"), default=0.0)
    cpu_cores = _int(_dict(profile.get("cpu")).get("cores"), default=0)
    egress = _string(_dict(profile.get("network")).get("egress")) or "unknown"
    docker_available = _bool(profile.get("docker_available"))
    service_manager = _string(profile.get("service_manager")) or "none"

    score += min(cpu_cores * 10, 40)
    if memory_mb >= 8192:
        score += 25
        reasons.append("high memory capacity")
    elif memory_mb >= 2048:
        score += 15
        reasons.append("adequate memory capacity")
    elif memory_mb >= 256:
        score += 8
        reasons.append("limited memory capacity")
    else:
        reasons.append("very low memory capacity")

    if disk_gb >= 100:
        score += 20
        reasons.append("high disk headroom")
    elif disk_gb >= 20:
        score += 10
        reasons.append("adequate disk headroom")
    elif disk_gb >= 4:
        score += 4
        reasons.append("limited disk headroom")
    else:
        reasons.append("low disk headroom")

    if docker_available:
        score += 10
        reasons.append("docker available")
    if service_manager == "systemd":
        score += 10
        reasons.append("systemd available")
    if egress != "unknown":
        score += 10
        reasons.append("known network egress")
    else:
        reasons.append("network profile incomplete")

    if registry.list_services():
        heavy_services = [svc for svc in registry.list_services() if svc.kind == "docker" or (svc.runtime or "").strip()]
        if target_role == NodeRuntimeProfile.FULL_WORKER.value and docker_available and memory_mb >= 4096 and disk_gb >= 20:
            score += 10
            reasons.append(f"service registry fit for {len(heavy_services)} service(s)")
        elif target_role == NodeRuntimeProfile.LITE_WORKER.value and memory_mb >= 256:
            score += 5
            reasons.append("service registry fit for light workloads")
        else:
            reasons.append("service registry fit is limited")

    return score, reasons


def _dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: object, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: object, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False
