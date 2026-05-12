from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class OrchestratorTask:
    task_id: str
    title: str
    scope: str
    risk: str
    priority: int
    status: str = "queued"
    worker_hint: str = ""
    source: str = "cli"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OrchestratorService:
    """Minimal v1.1 orchestrator facade.

    This intentionally keeps persistence thin until the O1 storage model lands.
    CLI tests can monkeypatch this service, while the default implementation is
    safe/no-provider/no-credential and records only audit events in the existing
    store when available.
    """

    def __init__(self, store: Any):
        self.store = store

    def enqueue(
        self,
        *,
        title: str,
        scope: str,
        risk: str,
        priority: int,
        worker_hint: str = "",
        source: str = "cli",
    ) -> str:
        task_id = f"orch_{uuid4().hex[:12]}"
        if hasattr(self.store, "record_audit"):
            self.store.record_audit(
                event_type="orchestrator",
                subject_type="orchestrator_task",
                subject_id=task_id,
                action="enqueue",
                outcome="queued",
                details={
                    "title": title,
                    "scope": scope,
                    "risk": risk,
                    "priority": priority,
                    "worker_hint": worker_hint,
                    "source": source,
                },
            )
        return task_id

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        reports: list[dict[str, Any]] = []
        if hasattr(self.store, "list_audit_events"):
            events = [event for event in self.store.list_audit_events() if event.event_type == "orchestrator"]
            for event in events[:5]:
                reports.append(
                    {
                        "task_id": event.subject_id,
                        "status": event.outcome,
                        "summary": event.details.get("title") or event.action,
                    }
                )
        return {"queue": [], "workers": [], "assignments": [], "reports": reports}

    def tick(self) -> dict[str, Any]:
        return {"dispatched": [], "blocked": [], "next": "暂无可分发任务"}

    def report(self) -> str:
        snapshot = self.snapshot()
        queue_count = len(snapshot.get("queue", []))
        worker_count = len(snapshot.get("workers", []))
        reports = snapshot.get("reports", [])
        latest = reports[0].get("summary", "暂无") if reports else "暂无"
        return f"队列 {queue_count}｜worker {worker_count}｜最近：{latest}"
