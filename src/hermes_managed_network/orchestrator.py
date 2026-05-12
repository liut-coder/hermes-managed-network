from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
    created_at: datetime = field(default_factory=_now)


class OrchestratorService:
    """Small persistent v1.1 orchestrator facade.

    The service stays deliberately local/safe: it persists queue, workers,
    assignments and short reports in SQLite when the store exposes ``connect``;
    it does not call real providers, write credentials, deploy, or merge main.
    """

    def __init__(self, store: Any):
        self.store = store
        self._migrate()

    def _migrate(self) -> None:
        if not hasattr(self.store, "connect"):
            return
        with self.store.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orchestrator_tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    worker_hint TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'cli',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_workers (
                    worker_id TEXT PRIMARY KEY,
                    transport TEXT NOT NULL,
                    status TEXT NOT NULL,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    leased_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orchestrator_reports (
                    report_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(orchestrator_tasks)").fetchall()}
            if "attempts" not in columns:
                conn.execute("ALTER TABLE orchestrator_tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")

    def _record_audit(self, *, subject_id: str, action: str, outcome: str, details: dict[str, Any]) -> None:
        if hasattr(self.store, "record_audit"):
            self.store.record_audit(
                event_type="orchestrator",
                subject_type="orchestrator_task",
                subject_id=subject_id,
                action=action,
                outcome=outcome,
                details=details,
            )

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
        now = _dt(_now())
        if hasattr(self.store, "connect"):
            with self.store.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO orchestrator_tasks (
                        task_id, title, scope, risk, priority, status,
                        worker_hint, source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, title, scope, risk, int(priority), "queued", worker_hint, source, now, now),
                )
        self._record_audit(
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

    def register_worker(
        self,
        *,
        worker_id: str,
        transport: str = "bridge",
        status: str = "online",
        labels: list[str] | None = None,
    ) -> None:
        if not hasattr(self.store, "connect"):
            return
        now = _dt(_now())
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO orchestrator_workers (
                    worker_id, transport, status, labels_json, registered_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    transport=excluded.transport,
                    status=excluded.status,
                    labels_json=excluded.labels_json,
                    updated_at=excluded.updated_at
                """,
                (worker_id, transport, status, json.dumps(labels or []), now, now),
            )

    def update_worker(
        self,
        *,
        worker_id: str,
        status: str,
        transport: str | None = None,
        labels: list[str] | None = None,
    ) -> None:
        if not hasattr(self.store, "connect"):
            return
        now = _dt(_now())
        with self.store.connect() as conn:
            current = conn.execute("SELECT * FROM orchestrator_workers WHERE worker_id = ?", (worker_id,)).fetchone()
            if current is None:
                conn.execute(
                    """
                    INSERT INTO orchestrator_workers (
                        worker_id, transport, status, labels_json, registered_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (worker_id, transport or "bridge", status, json.dumps(labels or []), now, now),
                )
                return
            conn.execute(
                """
                UPDATE orchestrator_workers
                SET transport = ?, status = ?, labels_json = ?, updated_at = ?
                WHERE worker_id = ?
                """,
                (
                    transport if transport is not None else current["transport"],
                    status,
                    json.dumps(labels) if labels is not None else current["labels_json"],
                    now,
                    worker_id,
                ),
            )

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        if not hasattr(self.store, "connect"):
            return {"queue": [], "workers": [], "assignments": [], "reports": self._audit_reports()}

        with self.store.connect() as conn:
            task_rows = conn.execute(
                """
                SELECT task_id, title, scope, risk, priority, status, worker_hint, source, attempts
                FROM orchestrator_tasks
                ORDER BY priority DESC, created_at ASC, task_id ASC
                """
            ).fetchall()
            worker_rows = conn.execute(
                """
                SELECT worker_id, transport, status, labels_json
                FROM orchestrator_workers
                ORDER BY worker_id ASC
                """
            ).fetchall()
            assignment_rows = conn.execute(
                """
                SELECT task_id, worker_id, status
                FROM orchestrator_assignments
                ORDER BY leased_at DESC, assignment_id DESC
                """
            ).fetchall()
            report_rows = conn.execute(
                """
                SELECT task_id, status, summary
                FROM orchestrator_reports
                ORDER BY created_at DESC, report_id DESC
                LIMIT 5
                """
            ).fetchall()

        reports = [dict(row) for row in report_rows]
        if not reports:
            reports = self._audit_reports()
        queue = [dict(row) for row in task_rows]
        for task in queue:
            if task.get("attempts") == 0:
                task.pop("attempts", None)
        return {
            "queue": queue,
            "workers": [
                {
                    "worker_id": row["worker_id"],
                    "transport": row["transport"],
                    "status": row["status"],
                    "labels": json.loads(row["labels_json"]),
                }
                for row in worker_rows
            ],
            "assignments": [dict(row) for row in assignment_rows],
            "reports": reports,
        }

    def _audit_reports(self) -> list[dict[str, Any]]:
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
        return reports

    def tick(
        self,
        *,
        bridge_adapter: Any | None = None,
        max_retries: int = 3,
        lease_timeout_seconds: int = 30 * 60,
    ) -> dict[str, Any]:
        if not hasattr(self.store, "connect"):
            return {"dispatched": [], "blocked": [], "next": "暂无可分发任务"}

        with self.store.connect() as conn:
            now_dt = _now()
            now = _dt(now_dt)
            leased_rows = conn.execute(
                """
                SELECT a.task_id, a.leased_at
                FROM orchestrator_assignments a
                JOIN orchestrator_tasks t ON t.task_id = a.task_id
                WHERE a.status = 'leased' AND t.status = 'leased'
                """
            ).fetchall()
            for leased in leased_rows:
                try:
                    leased_at = datetime.fromisoformat(leased["leased_at"])
                except ValueError:
                    leased_at = now_dt
                age = (now_dt - leased_at).total_seconds()
                if age > lease_timeout_seconds:
                    conn.execute(
                        "UPDATE orchestrator_assignments SET status = ?, updated_at = ? WHERE task_id = ?",
                        ("expired", now, leased["task_id"]),
                    )
                    conn.execute(
                        "UPDATE orchestrator_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                        ("queued", now, leased["task_id"]),
                    )

            task = conn.execute(
                """
                SELECT * FROM orchestrator_tasks
                WHERE status = 'queued' AND risk = 'low'
                ORDER BY priority DESC, created_at ASC, task_id ASC
                LIMIT 1
                """
            ).fetchone()
            if task is None:
                return {"dispatched": [], "blocked": [], "next": "暂无低风险可分发任务"}

            worker = conn.execute(
                """
                SELECT * FROM orchestrator_workers
                WHERE status = 'online' AND transport = 'bridge'
                ORDER BY updated_at DESC, worker_id ASC
                LIMIT 1
                """
            ).fetchone()
            if worker is None:
                return {
                    "dispatched": [],
                    "blocked": [{"task_id": task["task_id"], "reason": "暂无 online bridge worker"}],
                    "next": "等待 worker 上线后重试",
                }

            dispatch_audit: dict[str, Any] | None = None
            dispatch_result: dict[str, Any] | None = None
            if bridge_adapter is not None:
                try:
                    bridge_adapter.dispatch(task=dict(task), worker=dict(worker))
                except Exception as exc:  # noqa: BLE001 - retry bookkeeping must preserve operator-facing bridge errors.
                    attempts = int(task["attempts"] or 0) + 1
                    if attempts >= max_retries:
                        summary = f"连续 {max_retries} 次分发失败，已暂停"
                        conn.execute(
                            "UPDATE orchestrator_tasks SET status = ?, attempts = ?, updated_at = ? WHERE task_id = ?",
                            ("paused", attempts, now, task["task_id"]),
                        )
                        conn.execute(
                            """
                            INSERT INTO orchestrator_reports (report_id, task_id, status, summary, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (f"orch_rep_{uuid4().hex[:12]}", task["task_id"], "paused", summary, now),
                        )
                        dispatch_audit = {
                            "subject_id": task["task_id"],
                            "action": "tick/dispatch_failed",
                            "outcome": "paused",
                            "details": {"worker_id": worker["worker_id"], "attempts": attempts, "error": str(exc)},
                        }
                        dispatch_result = {
                            "dispatched": [],
                            "blocked": [{"task_id": task["task_id"], "reason": summary}],
                            "next": "等待主控检查 worker/bridge 状态",
                        }
                    else:
                        reason = f"bridge dispatch failed: {exc}"
                        conn.execute(
                            "UPDATE orchestrator_tasks SET attempts = ?, updated_at = ? WHERE task_id = ?",
                            (attempts, now, task["task_id"]),
                        )
                        conn.execute(
                            """
                            INSERT INTO orchestrator_reports (report_id, task_id, status, summary, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (f"orch_rep_{uuid4().hex[:12]}", task["task_id"], "retry", reason, now),
                        )
                        dispatch_audit = {
                            "subject_id": task["task_id"],
                            "action": "tick/dispatch_failed",
                            "outcome": "retry",
                            "details": {"worker_id": worker["worker_id"], "attempts": attempts, "error": str(exc)},
                        }
                        dispatch_result = {
                            "dispatched": [],
                            "blocked": [{"task_id": task["task_id"], "reason": reason, "attempt": attempts}],
                            "next": "等待下一轮重试",
                        }

            if dispatch_result is not None:
                audit_after_commit = dispatch_audit
            else:
                audit_after_commit = None

            if dispatch_result is None:
                assignment_id = f"orch_asg_{uuid4().hex[:12]}"
                conn.execute(
                    "UPDATE orchestrator_tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                    ("leased", now, task["task_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO orchestrator_assignments (
                        assignment_id, task_id, worker_id, status, leased_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (assignment_id, task["task_id"], worker["worker_id"], "leased", now, now),
                )
                summary = f"已分发给 {worker['worker_id']}"
                conn.execute(
                    """
                    INSERT INTO orchestrator_reports (report_id, task_id, status, summary, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (f"orch_rep_{uuid4().hex[:12]}", task["task_id"], "leased", summary, now),
                )

        if dispatch_result is not None:
            if audit_after_commit is not None:
                self._record_audit(**audit_after_commit)
            return dispatch_result

        self._record_audit(
            subject_id=task["task_id"],
            action="tick/lease",
            outcome="leased",
            details={"worker_id": worker["worker_id"]},
        )
        return {
            "dispatched": [{"task_id": task["task_id"], "worker_id": worker["worker_id"]}],
            "blocked": [],
            "next": "等待 worker 回报",
        }

    def report(self) -> str:
        snapshot = self.snapshot()
        queue_count = len(snapshot.get("queue", []))
        worker_count = len(snapshot.get("workers", []))
        reports = snapshot.get("reports", [])
        latest = reports[0].get("summary", "暂无") if reports else "暂无"
        return f"队列 {queue_count}｜worker {worker_count}｜最近：{latest}"
