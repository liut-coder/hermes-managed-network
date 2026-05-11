from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic

from .playbook import Playbook
from .storage import SQLiteStore, Task

SAFE_PHASES = {"precheck", "backup", "action", "verify", "rollback_hint"}
SAFE_RISKS = {"low", "medium"}


@dataclass
class SSHExecutionError(RuntimeError):
    task_id: str
    exit_code: int
    stderr: str
    stdout: str = ""
    duration_ms: int = 0
    failure_reason: str = "unknown"

    def __str__(self) -> str:
        return f"task {self.task_id} failed with exit code {self.exit_code}: {self.stderr.strip() or 'ssh command failed'}"


def _preview_output(text: str, *, limit: int = 240) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def classify_ssh_failure(exit_code: int, stderr: str) -> str:
    message = (stderr or "").lower()
    if "permission denied" in message or "publickey" in message or "authentication failed" in message:
        return "ssh_auth"
    if exit_code == 124:
        return "timeout"
    if "timed out" in message or "no route to host" in message or "connection refused" in message or "could not resolve hostname" in message:
        return "ssh_connectivity"
    if exit_code != 0:
        return "remote_command"
    return "none"


def _ssh_label_value(labels: list[str], key: str) -> str:
    prefix = f"{key}="
    for label in labels:
        if label.startswith(prefix):
            return label[len(prefix):]
    return ""


@dataclass
class SSHTarget:
    host: str
    user: str
    port: str
    source: str


def ssh_target_for_node(node) -> tuple[str, str, str]:
    target = ssh_target_details_for_node(node)
    return target.host, target.user, target.port


def ssh_target_details_for_node(node) -> SSHTarget:
    if node.ssh_host:
        host = node.ssh_host
        source = "ssh_host"
    elif _ssh_label_value(node.labels, "ssh-host"):
        host = _ssh_label_value(node.labels, "ssh-host")
        source = "label:ssh-host"
    elif getattr(node, "network_ip", ""):
        host = node.network_ip
        source = "network_ip"
    else:
        host = node.addresses[0] if node.addresses else ""
        source = "address" if host else ""
    user = node.ssh_user or _ssh_label_value(node.labels, "ssh-user") or "root"
    port = str(node.ssh_port or 22)
    label_port = _ssh_label_value(node.labels, "ssh-port")
    if node.ssh_port == 22 and label_port:
        port = label_port
    if not host:
        raise ValueError(f"node {node.node_id} missing ssh host")
    return SSHTarget(host=host, user=user, port=port, source=source)


def _ssh_target(task: Task, node) -> SSHTarget:
    try:
        return ssh_target_details_for_node(node)
    except ValueError as exc:
        raise ValueError(f"node {node.node_id} missing ssh host for task {task.task_id}") from exc


def run_ssh_task(store: SQLiteStore, task_id: str, *, allow_risk: set[str] | None = None, timeout_seconds: int = 120) -> Task:
    task = store.load_task(task_id)
    if task is None:
        raise ValueError(f"task not found: {task_id}")
    if task.executor != "ssh":
        raise ValueError(f"task {task.task_id} is not an ssh task")
    allowed = allow_risk or SAFE_RISKS
    if task.risk not in allowed:
        raise PermissionError(f"task risk '{task.risk}' is not allowed for ssh executor")
    node = store.load_node(task.node_id)
    if node is None or node.status != "managed":
        raise ValueError(f"node {task.node_id} is not managed")
    target = _ssh_target(task, node)
    running = task
    running.status = "running"
    running.started_at = datetime.now(timezone.utc)
    store.save_task(running)
    command = ["ssh", "-p", target.port, f"{target.user}@{target.host}", task.command]
    started = monotonic()
    completed = subprocess.run(command, check=False, text=True, capture_output=True, timeout=timeout_seconds)
    duration_ms = max(0, int((monotonic() - started) * 1000))
    failure_reason = classify_ssh_failure(completed.returncode, completed.stderr)
    stdout_preview = _preview_output(completed.stdout)
    stderr_preview = _preview_output(completed.stderr)
    store.record_audit(
        event_type="task",
        subject_type="task",
        subject_id=task.task_id,
        action="ssh_execute",
        outcome="ok" if completed.returncode == 0 else "failed",
        details={
            "node_id": task.node_id,
            "command": task.command,
            "host": target.host,
            "user": target.user,
            "port": int(target.port),
            "target_source": target.source,
            "duration_ms": duration_ms,
            "stdout_preview": stdout_preview,
            "stderr_preview": stderr_preview,
            "failure_reason": failure_reason,
        },
    )
    finished = store.complete_task(
        task.task_id,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    assert finished is not None
    if completed.returncode != 0:
        raise SSHExecutionError(
            task_id=task.task_id,
            exit_code=completed.returncode,
            stderr=completed.stderr,
            stdout=completed.stdout,
            duration_ms=duration_ms,
            failure_reason=failure_reason,
        )
    return finished



@dataclass
class CommandResult:
    phase: str
    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class PlaybookRun:
    playbook_id: str
    started_at: datetime
    results: list[CommandResult]

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)


class PlaybookExecutionError(RuntimeError):
    def __init__(self, result: CommandResult) -> None:
        super().__init__(f"{result.phase} failed with exit code {result.exit_code}: {result.command}")
        self.result = result


class PlaybookExecutor:
    """Local MVP playbook executor with explicit risk and phase boundaries."""

    def __init__(self, *, dry_run: bool = True, timeout_seconds: int = 120) -> None:
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        playbook: Playbook,
        *,
        values: dict[str, str],
        phases: list[str] | None = None,
        allow_risk: set[str] | None = None,
    ) -> PlaybookRun:
        allowed = allow_risk or SAFE_RISKS
        if playbook.risk not in allowed:
            raise PermissionError(f"playbook risk '{playbook.risk}' is not allowed")

        selected = phases or ["precheck", "backup", "action", "verify"]
        for phase in selected:
            if phase not in SAFE_PHASES:
                raise ValueError(f"unsupported phase: {phase}")

        results: list[CommandResult] = []
        for phase in selected:
            for command in playbook.render_phase(phase, values):
                result = self._run_command(phase, command)
                results.append(result)
                if not result.ok:
                    raise PlaybookExecutionError(result)
        return PlaybookRun(playbook_id=playbook.id, started_at=datetime.now(timezone.utc), results=results)

    def _run_command(self, phase: str, command: str) -> CommandResult:
        if self.dry_run:
            return CommandResult(phase=phase, command=command, exit_code=0, stdout="", stderr="")
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
        )
        return CommandResult(
            phase=phase,
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
