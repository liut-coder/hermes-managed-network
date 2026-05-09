from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from .playbook import Playbook

SAFE_PHASES = {"precheck", "backup", "action", "verify", "rollback_hint"}
SAFE_RISKS = {"low", "medium"}


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
