from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .network_base import NetworkProviderError
from .storage import SQLiteStore


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def run_acl_command(command: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=60,
    )


def dispatch_approved_network_acl_apply(store: SQLiteStore, approval_id: str) -> bool:
    approval = store.load_approval_request(approval_id)
    if approval is None or approval.status != "approved":
        return False
    if approval.subject_type != "network_acl" or approval.action != "network.acl.apply":
        return False
    details = approval.details
    current_path = Path(str(details.get("current_path") or "")).expanduser()
    proposed_path = Path(str(details.get("proposed_path") or "")).expanduser()
    if not current_path or not proposed_path or not proposed_path.exists():
        return False
    old_text = current_path.read_text(encoding="utf-8") if current_path.exists() else ""
    new_text = proposed_path.read_text(encoding="utf-8")
    old_sha256 = sha256_text(old_text)
    expected_old_sha256 = str(details.get("old_sha256") or "")
    if expected_old_sha256 and expected_old_sha256 != old_sha256:
        store.record_audit(
            event_type="network",
            subject_type="network_acl",
            subject_id=str(current_path),
            action="acl/apply",
            outcome="failed",
            details={
                "approval_id": approval.approval_id,
                "current_path": str(current_path),
                "proposed_path": str(proposed_path),
                "error": "current ACL sha256 mismatch",
                "expected_old_sha256": expected_old_sha256,
                "actual_old_sha256": old_sha256,
            },
        )
        raise NetworkProviderError("当前 ACL 已变化，请重新生成 diff 后再审批")
    current_path.parent.mkdir(parents=True, exist_ok=True)
    current_path.write_text(new_text, encoding="utf-8")
    reload_command = str(details.get("reload_command") or "").strip()
    verify_command = str(details.get("verify_command") or "").strip()
    reload_result = None
    verify_result = None
    try:
        if reload_command:
            reload_result = run_acl_command(reload_command, cwd=current_path.parent)
            if reload_result.returncode != 0:
                raise NetworkProviderError(f"Headscale reload 失败: {reload_result.stderr.strip() or reload_result.stdout.strip()}")
        if verify_command:
            verify_result = run_acl_command(verify_command, cwd=current_path.parent)
            if verify_result.returncode != 0:
                raise NetworkProviderError(f"Headscale ACL verify 失败: {verify_result.stderr.strip() or verify_result.stdout.strip()}")
    except NetworkProviderError as exc:
        store.record_audit(
            event_type="network",
            subject_type="network_acl",
            subject_id=str(current_path),
            action="acl/apply",
            outcome="failed",
            details={
                "approval_id": approval.approval_id,
                "current_path": str(current_path),
                "proposed_path": str(proposed_path),
                "old_sha256": old_sha256,
                "new_sha256": sha256_text(new_text),
                "reload_exit_code": None if reload_result is None else reload_result.returncode,
                "verify_exit_code": None if verify_result is None else verify_result.returncode,
                "error": str(exc),
            },
        )
        raise
    store.record_audit(
        event_type="network",
        subject_type="network_acl",
        subject_id=str(current_path),
        action="acl/apply",
        outcome="ok",
        details={
            "approval_id": approval.approval_id,
            "current_path": str(current_path),
            "proposed_path": str(proposed_path),
            "old_sha256": old_sha256,
            "new_sha256": sha256_text(new_text),
            "reload_exit_code": None if reload_result is None else reload_result.returncode,
            "verify_exit_code": None if verify_result is None else verify_result.returncode,
        },
    )
    return True
