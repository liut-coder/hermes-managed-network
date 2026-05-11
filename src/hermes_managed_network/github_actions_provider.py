from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .docs_generate import _redact_text, _sanitize_value


@dataclass(frozen=True)
class GitHubActionsConfig:
    repo: str
    workflow: str
    token: str
    ref: str = "main"

    def __post_init__(self) -> None:
        normalized_repo = self.repo.strip().strip("/")
        normalized_workflow = self.workflow.strip()
        normalized_ref = _normalize_ref(self.ref)
        if normalized_repo.count("/") != 1 or any(not part.strip() for part in normalized_repo.split("/")):
            raise ValueError("repo must use owner/name format")
        if not normalized_workflow:
            raise ValueError("workflow is required")
        if not self.token.strip():
            raise ValueError("token is required")
        object.__setattr__(self, "repo", normalized_repo)
        object.__setattr__(self, "workflow", normalized_workflow)
        object.__setattr__(self, "ref", normalized_ref)

    def sanitized(self) -> dict[str, str]:
        return {
            "repo": self.repo,
            "workflow": self.workflow,
            "ref": self.ref,
            "token": "[REDACTED]",
        }


class GitHubActionsProvider:
    def __init__(self, config: GitHubActionsConfig) -> None:
        self.config = config

    def status(self, fixture_path: Path, *, service_id: str, source_label: str = "github-actions-fixture") -> dict[str, object]:
        result = build_github_actions_status(fixture_path, service_id=service_id, source_label=source_label)
        self._assert_fixture_matches_config(result)
        return result

    def dispatch_plan(
        self,
        fixture_path: Path,
        *,
        service_id: str,
        requested_by: str,
        source_label: str = "github-actions-fixture",
        inputs: dict[str, object] | None = None,
    ) -> dict[str, object]:
        result = build_github_actions_dispatch_plan(
            fixture_path,
            service_id=service_id,
            requested_by=requested_by,
            source_label=source_label,
            inputs=inputs,
        )
        self._assert_fixture_matches_config(result["plan"])
        return result

    def _assert_fixture_matches_config(self, payload: dict[str, object]) -> None:
        repo = str(payload.get("repo") or "")
        workflow = str(payload.get("workflow") or "")
        ref = str(payload.get("ref") or "")
        if repo != self.config.repo:
            raise ValueError(f"fixture repo mismatch: expected {self.config.repo}, got {repo}")
        if workflow != self.config.workflow:
            raise ValueError(f"fixture workflow mismatch: expected {self.config.workflow}, got {workflow}")
        if ref != self.config.ref:
            raise ValueError(f"fixture ref mismatch: expected {self.config.ref}, got {ref}")


def load_github_actions_fixture(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_github_actions_status(
    path: Path,
    *,
    service_id: str,
    source_label: str = "github-actions-fixture",
) -> dict[str, object]:
    payload = load_github_actions_fixture(path)
    source = f"{source_label}:{path}"
    summary = _fixture_summary(payload)
    deployment_record = _deployment_record(
        service_id=service_id,
        repo=summary["repo"],
        workflow=summary["workflow"],
        ref=summary["ref"],
        status=_status_from_run(summary["run"]),
        run_url=summary["run"].get("html_url"),
        commit_sha=summary["head_sha"],
        source=source,
    )
    return {
        "provider": "github-actions",
        "mode": "status",
        "write": False,
        "source": source,
        "repo": summary["repo"],
        "workflow": summary["workflow"],
        "ref": summary["ref"],
        "run": _sanitize_result_payload(summary["run"]),
        "jobs": _sanitize_result_payload(summary["jobs"]),
        "checks": _sanitize_result_payload(summary["checks"]),
        "deployment_record": deployment_record,
    }


def build_github_actions_dispatch_plan(
    path: Path,
    *,
    service_id: str,
    requested_by: str,
    source_label: str = "github-actions-fixture",
    inputs: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = load_github_actions_fixture(path)
    source = f"{source_label}:{path}"
    summary = _fixture_summary(payload)
    merged_inputs = dict(summary["inputs"])
    merged_inputs.update(inputs or {})
    plan = {
        "action": "workflow_dispatch",
        "repo": summary["repo"],
        "workflow": summary["workflow"],
        "ref": summary["ref"],
        "requested_by": requested_by,
        "inputs": _sanitize_value(merged_inputs),
        "dry_run": True,
        "mutating": False,
    }
    return {
        "provider": "github-actions",
        "mode": "dispatch-plan",
        "write": False,
        "source": source,
        "approval_required": True,
        "executed": False,
        "plan": plan,
        "result": {
            "approval_required": True,
            "not_executed": True,
            "machine_changed": False,
        },
        "deployment_record": _deployment_record(
            service_id=service_id,
            repo=summary["repo"],
            workflow=summary["workflow"],
            ref=summary["ref"],
            status="approval_required",
            run_url=None,
            commit_sha=summary["head_sha"],
            source=source,
        ),
    }


def _fixture_summary(payload: dict[str, object]) -> dict[str, object]:
    repo = _repo_name(payload.get("repo"))
    workflow = _workflow_name(payload.get("workflow"))
    ref = _normalize_ref(str(payload.get("ref") or "main"))
    runs = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
    run = dict(runs[0]) if runs else {"status": "unknown", "conclusion": None, "html_url": None}
    head_sha = str(payload.get("head_sha") or run.get("head_sha") or "unknown")
    return {
        "repo": repo,
        "workflow": workflow,
        "ref": ref,
        "run": run,
        "jobs": [dict(item) for item in jobs if isinstance(item, dict)],
        "checks": [dict(item) for item in checks if isinstance(item, dict)],
        "inputs": dict(inputs),
        "head_sha": head_sha,
    }


def _repo_name(raw: object) -> str:
    if isinstance(raw, dict):
        full_name = raw.get("full_name")
        if isinstance(full_name, str) and full_name.strip():
            return full_name.strip()
        owner = str(raw.get("owner") or "").strip()
        name = str(raw.get("name") or "").strip()
        if owner and name:
            return f"{owner}/{name}"
    return "unknown/unknown"


def _workflow_name(raw: object) -> str:
    if isinstance(raw, dict):
        path = raw.get("path")
        if isinstance(path, str) and path.strip():
            return path.strip().split("/")[-1]
        name = raw.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return "workflow.yml"


def _normalize_ref(value: str) -> str:
    normalized = value.strip()
    prefix = "refs/heads/"
    if normalized.startswith(prefix):
        return normalized[len(prefix) :]
    return normalized or "main"


def _status_from_run(run: dict[str, object]) -> str:
    conclusion = str(run.get("conclusion") or "").strip()
    if conclusion:
        return conclusion
    status = str(run.get("status") or "").strip()
    return status or "unknown"


def _deployment_record(
    *,
    service_id: str,
    repo: str,
    workflow: str,
    ref: str,
    status: str,
    run_url: object,
    commit_sha: str,
    source: str,
) -> dict[str, object]:
    return {
        "service_id": service_id,
        "repo": _redact_text(repo),
        "workflow": _redact_text(workflow),
        "ref": _redact_text(ref),
        "status": status,
        "run_url": _sanitize_url(run_url),
        "commit_sha": _mask_commit_sha(commit_sha),
        "source": _redact_text(source),
    }


def _sanitize_result_payload(value: object) -> object:
    sanitized = _sanitize_value(value)
    if isinstance(sanitized, dict):
        return {str(key): _sanitize_result_payload(item) for key, item in sanitized.items()}
    if isinstance(sanitized, list):
        return [_sanitize_result_payload(item) for item in sanitized]
    if isinstance(sanitized, str):
        return _sanitize_url(sanitized) or sanitized
    return sanitized


def _sanitize_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "?" not in text:
        return _redact_text(text)
    base, _query = text.split("?", 1)
    return f"{_redact_text(base)}?[REDACTED]"


def _mask_commit_sha(value: str) -> str:
    sha = value.strip()
    if len(sha) <= 20:
        return _redact_text(sha)
    return f"{sha[:12]}…{sha[-8:]}"
