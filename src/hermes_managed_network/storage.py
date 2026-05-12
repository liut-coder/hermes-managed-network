from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
from typing import Any, Iterator

from .inventory import Node
from .tokens import JoinToken
from .components import ComponentManifest


@dataclass
class NodeComponent:
    node_id: str
    component_id: str
    desired_state: str
    current_state: str
    config: dict[str, Any]
    installed_version: str = ""
    driver: str = ""
    last_plan_id: str = ""
    last_run_id: str = ""
    last_verified_at: datetime | None = None


@dataclass
class ComponentRun:
    run_id: str
    component_id: str
    node_id: str
    action: str
    risk: str
    status: str
    plan: dict[str, Any]
    result: dict[str, Any]
    created_by: str
    created_at: datetime
    completed_at: datetime | None = None


@dataclass
class MonitorSnapshot:
    snapshot_id: str
    node_id: str
    health: str
    reason: str
    heartbeat_at: datetime | None
    age_seconds: int | None
    runtime_profile: str
    service_manager: str
    worker_protocol_version: str
    worker_version: str
    exec_mode: str
    facts_summary: dict[str, Any]
    created_at: datetime


@dataclass
class ServiceRecord:
    service_id: str
    name: str
    node_id: str = ""
    kind: str = "unknown"
    runtime: str = ""
    domains: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    deploy_path: str = ""
    config_paths: list[str] = field(default_factory=list)
    env_paths: list[str] = field(default_factory=list)
    data_paths: list[str] = field(default_factory=list)
    health_check_url: str = ""
    monitor_enabled: bool = False
    docs_path: str = ""
    source: str = "manual"
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class Task:
    task_id: str
    node_id: str
    command: str
    risk: str
    status: str
    created_by: str
    created_at: datetime
    executor: str = "worker"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


@dataclass
class ApprovalRequest:
    approval_id: str
    subject_type: str
    subject_id: str
    action: str
    risk: str
    status: str
    requested_by: str
    details: dict[str, Any]
    created_at: datetime
    decided_by: str = ""
    decided_at: datetime | None = None


@dataclass
class Notification:
    notification_id: str
    channel: str
    subject_type: str
    subject_id: str
    status: str
    payload: dict[str, Any]
    created_at: datetime
    delivered_at: datetime | None = None


@dataclass
class AuditEvent:
    event_type: str
    subject_type: str
    subject_id: str
    action: str
    outcome: str
    details: dict[str, Any]
    created_at: datetime


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLiteStore:
    """Small SQLite persistence layer for the local control-plane MVP."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if self.path.parent:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS join_tokens (
                    value TEXT PRIMARY KEY,
                    trust_level TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    used_at TEXT,
                    node_fingerprint TEXT
                );

                CREATE TABLE IF NOT EXISTS nodes (
                    node_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    addresses_json TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    permission_bundles_json TEXT NOT NULL DEFAULT '[]',
                    ssh_host TEXT NOT NULL DEFAULT '',
                    ssh_user TEXT NOT NULL DEFAULT '',
                    ssh_port INTEGER NOT NULL DEFAULT 22,
                    network_provider TEXT NOT NULL DEFAULT '',
                    network_node_id TEXT NOT NULL DEFAULT '',
                    network_ip TEXT NOT NULL DEFAULT '',
                    network_tags_json TEXT NOT NULL DEFAULT '[]',
                    network_online INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    executor TEXT NOT NULL DEFAULT 'worker',
                    started_at TEXT,
                    completed_at TEXT,
                    exit_code INTEGER,
                    stdout TEXT NOT NULL DEFAULT '',
                    stderr TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS approval_requests (
                    approval_id TEXT PRIMARY KEY,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_by TEXT NOT NULL DEFAULT '',
                    decided_at TEXT
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT
                );

                CREATE TABLE IF NOT EXISTS components (
                    component_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    api_version INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    enabled_at TEXT,
                    manifest_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS node_components (
                    node_id TEXT NOT NULL,
                    component_id TEXT NOT NULL,
                    desired_state TEXT NOT NULL,
                    current_state TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    installed_version TEXT NOT NULL DEFAULT '',
                    driver TEXT NOT NULL DEFAULT '',
                    last_plan_id TEXT NOT NULL DEFAULT '',
                    last_run_id TEXT NOT NULL DEFAULT '',
                    last_verified_at TEXT,
                    PRIMARY KEY (node_id, component_id)
                );

                CREATE TABLE IF NOT EXISTS component_runs (
                    run_id TEXT PRIMARY KEY,
                    component_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS monitor_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    health TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    heartbeat_at TEXT,
                    age_seconds INTEGER,
                    runtime_profile TEXT NOT NULL,
                    service_manager TEXT NOT NULL,
                    worker_protocol_version TEXT NOT NULL,
                    worker_version TEXT NOT NULL,
                    exec_mode TEXT NOT NULL,
                    facts_summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS services (
                    service_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    node_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT 'unknown',
                    runtime TEXT NOT NULL DEFAULT '',
                    domains_json TEXT NOT NULL DEFAULT '[]',
                    ports_json TEXT NOT NULL DEFAULT '[]',
                    deploy_path TEXT NOT NULL DEFAULT '',
                    config_paths_json TEXT NOT NULL DEFAULT '[]',
                    env_paths_json TEXT NOT NULL DEFAULT '[]',
                    data_paths_json TEXT NOT NULL DEFAULT '[]',
                    health_check_url TEXT NOT NULL DEFAULT '',
                    monitor_enabled INTEGER NOT NULL DEFAULT 0,
                    docs_path TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    status TEXT NOT NULL DEFAULT 'active',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            for statement in (
                "ALTER TABLE nodes ADD COLUMN ssh_host TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE nodes ADD COLUMN ssh_user TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE nodes ADD COLUMN ssh_port INTEGER NOT NULL DEFAULT 22",
                "ALTER TABLE nodes ADD COLUMN network_provider TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE nodes ADD COLUMN network_node_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE nodes ADD COLUMN network_ip TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE nodes ADD COLUMN network_tags_json TEXT NOT NULL DEFAULT '[]'",
                "ALTER TABLE nodes ADD COLUMN network_online INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE tasks ADD COLUMN executor TEXT NOT NULL DEFAULT 'worker'",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc):
                        raise

    def save_service_record(self, service: ServiceRecord) -> ServiceRecord:
        now = datetime.now(timezone.utc)
        existing = self.load_service_record(service.service_id)
        created_at = service.created_at or (existing.created_at if existing else now)
        saved = ServiceRecord(
            service_id=service.service_id,
            name=service.name,
            node_id=service.node_id,
            kind=service.kind,
            runtime=service.runtime,
            domains=list(service.domains),
            ports=list(service.ports),
            deploy_path=service.deploy_path,
            config_paths=list(service.config_paths),
            env_paths=list(service.env_paths),
            data_paths=list(service.data_paths),
            health_check_url=service.health_check_url,
            monitor_enabled=service.monitor_enabled,
            docs_path=service.docs_path,
            source=service.source,
            status=service.status,
            metadata=dict(service.metadata),
            created_at=created_at,
            updated_at=now,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO services (
                    service_id, name, node_id, kind, runtime, domains_json, ports_json,
                    deploy_path, config_paths_json, env_paths_json, data_paths_json,
                    health_check_url, monitor_enabled, docs_path, source, status,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    name=excluded.name,
                    node_id=excluded.node_id,
                    kind=excluded.kind,
                    runtime=excluded.runtime,
                    domains_json=excluded.domains_json,
                    ports_json=excluded.ports_json,
                    deploy_path=excluded.deploy_path,
                    config_paths_json=excluded.config_paths_json,
                    env_paths_json=excluded.env_paths_json,
                    data_paths_json=excluded.data_paths_json,
                    health_check_url=excluded.health_check_url,
                    monitor_enabled=excluded.monitor_enabled,
                    docs_path=excluded.docs_path,
                    source=excluded.source,
                    status=excluded.status,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    saved.service_id,
                    saved.name,
                    saved.node_id,
                    saved.kind,
                    saved.runtime,
                    json.dumps(saved.domains),
                    json.dumps(saved.ports),
                    saved.deploy_path,
                    json.dumps(saved.config_paths),
                    json.dumps(saved.env_paths),
                    json.dumps(saved.data_paths),
                    saved.health_check_url,
                    1 if saved.monitor_enabled else 0,
                    saved.docs_path,
                    saved.source,
                    saved.status,
                    json.dumps(saved.metadata, sort_keys=True),
                    _dt(saved.created_at),
                    _dt(saved.updated_at),
                ),
            )
        return saved

    def _service_record_from_row(self, row) -> ServiceRecord:
        return ServiceRecord(
            service_id=row["service_id"],
            name=row["name"],
            node_id=row["node_id"],
            kind=row["kind"],
            runtime=row["runtime"],
            domains=json.loads(row["domains_json"]),
            ports=[int(port) for port in json.loads(row["ports_json"])],
            deploy_path=row["deploy_path"],
            config_paths=json.loads(row["config_paths_json"]),
            env_paths=json.loads(row["env_paths_json"]),
            data_paths=json.loads(row["data_paths_json"]),
            health_check_url=row["health_check_url"],
            monitor_enabled=bool(row["monitor_enabled"]),
            docs_path=row["docs_path"],
            source=row["source"],
            status=row["status"],
            metadata=json.loads(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def load_service_record(self, service_id: str) -> ServiceRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM services WHERE service_id = ?", (service_id,)).fetchone()
        return self._service_record_from_row(row) if row else None

    def list_service_records(self, node_id: str | None = None) -> list[ServiceRecord]:
        with self.connect() as conn:
            if node_id:
                rows = conn.execute(
                    "SELECT * FROM services WHERE node_id = ? ORDER BY service_id",
                    (node_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM services ORDER BY service_id").fetchall()
        return [self._service_record_from_row(row) for row in rows]

    def record_audit(
        self,
        *,
        event_type: str,
        subject_type: str,
        subject_id: str,
        action: str,
        outcome: str,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            outcome=outcome,
            details=details or {},
            created_at=datetime.now(timezone.utc),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                    event_type, subject_type, subject_id, action, outcome,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_type,
                    event.subject_type,
                    event.subject_id,
                    event.action,
                    event.outcome,
                    json.dumps(event.details, sort_keys=True),
                    _dt(event.created_at),
                ),
            )
        return event

    def list_audit_events(self) -> list[AuditEvent]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT event_type, subject_type, subject_id, action, outcome, details_json, created_at FROM audit_events ORDER BY id"
            ).fetchall()
        return [
            AuditEvent(
                event_type=row["event_type"],
                subject_type=row["subject_type"],
                subject_id=row["subject_id"],
                action=row["action"],
                outcome=row["outcome"],
                details=json.loads(row["details_json"]),
                created_at=_parse_dt(row["created_at"]),
            )
            for row in rows
        ]

    def enqueue_notification(
        self,
        *,
        channel: str,
        subject_type: str,
        subject_id: str,
        payload: dict[str, Any],
    ) -> Notification:
        notification = Notification(
            notification_id="notif_" + uuid4().hex[:12],
            channel=channel,
            subject_type=subject_type,
            subject_id=subject_id,
            status="pending",
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO notifications (
                    notification_id, channel, subject_type, subject_id, status,
                    payload_json, created_at, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification.notification_id,
                    notification.channel,
                    notification.subject_type,
                    notification.subject_id,
                    notification.status,
                    json.dumps(notification.payload, sort_keys=True),
                    _dt(notification.created_at),
                    _dt(notification.delivered_at),
                ),
            )
        return notification

    def _notification_from_row(self, row) -> Notification:
        return Notification(
            notification_id=row["notification_id"],
            channel=row["channel"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            status=row["status"],
            payload=json.loads(row["payload_json"]),
            created_at=_parse_dt(row["created_at"]),
            delivered_at=_parse_dt(row["delivered_at"]),
        )

    def list_notifications(self, status: str | None = None) -> list[Notification]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM notifications WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM notifications ORDER BY created_at DESC").fetchall()
        return [self._notification_from_row(row) for row in rows]

    def mark_notification_delivered(self, notification_id: str) -> Notification | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM notifications WHERE notification_id = ?", (notification_id,)).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            conn.execute(
                "UPDATE notifications SET status = ?, delivered_at = ? WHERE notification_id = ?",
                ("delivered", _dt(now), notification_id),
            )
            updated = conn.execute("SELECT * FROM notifications WHERE notification_id = ?", (notification_id,)).fetchone()
        return self._notification_from_row(updated)

    def create_approval_request(
        self,
        *,
        subject_type: str,
        subject_id: str,
        action: str,
        risk: str,
        requested_by: str,
        details: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            approval_id="appr_" + uuid4().hex[:12],
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            risk=risk,
            status="pending",
            requested_by=requested_by,
            details=details or {},
            created_at=datetime.now(timezone.utc),
        )
        self.save_approval_request(approval)
        self.record_audit(
            event_type="approval",
            subject_type=subject_type,
            subject_id=approval.approval_id,
            action="approval/request",
            outcome="pending",
            details={"subject_id": subject_id, "action": action, "risk": risk, **approval.details},
        )
        return approval

    def save_approval_request(self, approval: ApprovalRequest) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO approval_requests (
                    approval_id, subject_type, subject_id, action, risk, status,
                    requested_by, details_json, created_at, decided_by, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(approval_id) DO UPDATE SET
                    subject_type=excluded.subject_type,
                    subject_id=excluded.subject_id,
                    action=excluded.action,
                    risk=excluded.risk,
                    status=excluded.status,
                    requested_by=excluded.requested_by,
                    details_json=excluded.details_json,
                    created_at=excluded.created_at,
                    decided_by=excluded.decided_by,
                    decided_at=excluded.decided_at
                """,
                (
                    approval.approval_id,
                    approval.subject_type,
                    approval.subject_id,
                    approval.action,
                    approval.risk,
                    approval.status,
                    approval.requested_by,
                    json.dumps(approval.details, sort_keys=True),
                    _dt(approval.created_at),
                    approval.decided_by,
                    _dt(approval.decided_at),
                ),
            )

    def _approval_from_row(self, row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=row["approval_id"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            action=row["action"],
            risk=row["risk"],
            status=row["status"],
            requested_by=row["requested_by"],
            details=json.loads(row["details_json"]),
            created_at=_parse_dt(row["created_at"]),
            decided_by=row["decided_by"],
            decided_at=_parse_dt(row["decided_at"]),
        )

    def load_approval_request(self, approval_id: str) -> ApprovalRequest | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)).fetchone()
        return self._approval_from_row(row) if row else None

    def list_approval_requests(self, status: str | None = None) -> list[ApprovalRequest]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM approval_requests WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM approval_requests ORDER BY created_at DESC").fetchall()
        return [self._approval_from_row(row) for row in rows]

    def resolve_approval_request(self, approval_id: str, *, status: str, decided_by: str) -> ApprovalRequest | None:
        if status not in {"approved", "rejected"}:
            raise ValueError("approval status must be approved or rejected")
        approval = self.load_approval_request(approval_id)
        if approval is None:
            return None
        if approval.status != "pending":
            return approval
        approval.status = status
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(timezone.utc)
        self.save_approval_request(approval)
        self.record_audit(
            event_type="approval",
            subject_type=approval.subject_type,
            subject_id=approval.approval_id,
            action=f"approval/{status}",
            outcome=status,
            details={"subject_id": approval.subject_id, "risk": approval.risk, "decided_by": decided_by},
        )
        if status == "approved" and approval.subject_type == "task" and approval.action == "task.run":
            self.dispatch_approved_task_request(approval.approval_id)
        if status == "approved" and approval.subject_type == "component_run" and approval.action.startswith("component."):
            self.dispatch_approved_component_action(approval.approval_id)
        return approval

    def dispatch_approved_component_apply(self, approval_id: str) -> ComponentRun | None:
        return self.dispatch_approved_component_action(approval_id)

    def _insert_audit_event(self, conn: sqlite3.Connection, event: AuditEvent) -> None:
        conn.execute(
            """
            INSERT INTO audit_events (
                event_type, subject_type, subject_id, action, outcome,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_type,
                event.subject_type,
                event.subject_id,
                event.action,
                event.outcome,
                json.dumps(event.details, sort_keys=True),
                _dt(event.created_at),
            ),
        )

    def _record_component_dispatch_failure(
        self,
        conn: sqlite3.Connection,
        approval: ApprovalRequest,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        event = AuditEvent(
            event_type="approval",
            subject_type="component_run",
            subject_id=approval.approval_id,
            action="approval/dispatch",
            outcome="failed",
            details={"subject_id": approval.subject_id, "reason": reason, **(details or {})},
            created_at=datetime.now(timezone.utc),
        )
        self._insert_audit_event(conn, event)

    def dispatch_approved_component_action(self, approval_id: str) -> ComponentRun | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            approval_row = conn.execute("SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)).fetchone()
            if approval_row is None:
                return None
            approval = self._approval_from_row(approval_row)
            if approval.status != "approved":
                return None
            if approval.subject_type != "component_run" or not approval.action.startswith("component."):
                return None
            run_row = conn.execute("SELECT * FROM component_runs WHERE run_id = ?", (approval.subject_id,)).fetchone()
            if run_row is None:
                return None
            run = self._component_run_from_row(run_row)
            if run.status == "state_recorded":
                return run

            action = str(approval.details.get("action") or approval.action.removeprefix("component.") or run.action)
            mismatched = []
            expected = {
                "run_id": run.run_id,
                "component_id": run.component_id,
                "node_id": run.node_id,
                "action": run.action,
                "risk": run.risk,
            }
            observed = {
                "run_id": str(approval.details.get("run_id") or approval.subject_id),
                "component_id": str(approval.details.get("component_id") or ""),
                "node_id": str(approval.details.get("node_id") or ""),
                "action": action,
                "risk": approval.risk,
            }
            approval_action = approval.action.removeprefix("component.")
            if approval_action != run.action:
                mismatched.append("approval.action")
            for key, expected_value in expected.items():
                if observed[key] != expected_value:
                    mismatched.append(key)
            if mismatched:
                self._record_component_dispatch_failure(
                    conn,
                    approval,
                    reason="component approval details mismatch",
                    details={"mismatched": sorted(set(mismatched)), "run_id": run.run_id},
                )
                return None
            if run.status != "pending_approval":
                self._record_component_dispatch_failure(
                    conn,
                    approval,
                    reason="component run is not pending approval",
                    details={"run_id": run.run_id, "run_status": run.status},
                )
                return None
            if action not in {"apply", "uninstall"}:
                self._record_component_dispatch_failure(
                    conn,
                    approval,
                    reason="component action is not dispatchable",
                    details={"run_id": run.run_id, "component_action": action},
                )
                return None
            details = approval.details
            component_id = run.component_id
            node_id = run.node_id
            config = details.get("config") if isinstance(details.get("config"), dict) else {}
            if action == "uninstall":
                existing = conn.execute(
                    "SELECT * FROM node_components WHERE node_id = ? AND component_id = ?",
                    (node_id, component_id),
                ).fetchone()
                if existing is not None:
                    config = json.loads(existing["config_json"])
                    installed_version = existing["installed_version"]
                    driver = existing["driver"]
                else:
                    installed_version = str(details.get("version") or "")
                    driver = str(details.get("driver") or "")
                desired_state = "absent"
            else:
                installed_version = str(details.get("version") or "")
                driver = str(details.get("driver") or "")
                desired_state = "enabled"
            conn.execute(
                """
                INSERT INTO node_components (
                    node_id, component_id, desired_state, current_state, config_json,
                    installed_version, driver, last_plan_id, last_run_id, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id, component_id) DO UPDATE SET
                    desired_state=excluded.desired_state,
                    current_state=excluded.current_state,
                    config_json=excluded.config_json,
                    installed_version=excluded.installed_version,
                    driver=excluded.driver,
                    last_plan_id=excluded.last_plan_id,
                    last_run_id=excluded.last_run_id,
                    last_verified_at=excluded.last_verified_at
                """,
                (
                    node_id,
                    component_id,
                    desired_state,
                    "planned",
                    json.dumps(config, sort_keys=True),
                    installed_version,
                    driver,
                    "",
                    run.run_id,
                    None,
                ),
            )
            run.status = "state_recorded"
            run.result = {
                **run.result,
                "machine_changed": False,
                "state_closed_loop": True,
                "remote_execution": "not_enabled",
                "approval_id": approval.approval_id,
                "approved_by": approval.decided_by,
            }
            run.completed_at = datetime.now(timezone.utc)
            updated = conn.execute(
                """
                UPDATE component_runs
                SET status = ?, plan_json = ?, result_json = ?, completed_at = ?
                WHERE run_id = ? AND status != 'state_recorded'
                """,
                (
                    run.status,
                    json.dumps(run.plan, sort_keys=True),
                    json.dumps(run.result, sort_keys=True),
                    _dt(run.completed_at),
                    run.run_id,
                ),
            )
            if updated.rowcount == 0:
                refreshed = conn.execute("SELECT * FROM component_runs WHERE run_id = ?", (run.run_id,)).fetchone()
                return self._component_run_from_row(refreshed) if refreshed else None
            event = AuditEvent(
                event_type="component",
                subject_type="component",
                subject_id=component_id,
                action=action,
                outcome="state_recorded",
                details={"node_id": node_id, "run_id": run.run_id, "risk": run.risk, "approval_id": approval.approval_id},
                created_at=datetime.now(timezone.utc),
            )
            conn.execute(
                """
                INSERT INTO audit_events (
                    event_type, subject_type, subject_id, action, outcome,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_type,
                    event.subject_type,
                    event.subject_id,
                    event.action,
                    event.outcome,
                    json.dumps(event.details, sort_keys=True),
                    _dt(event.created_at),
                ),
            )
            return run

    def dispatch_approved_task_request(self, approval_id: str) -> Task | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                return None
            approval = self._approval_from_row(row)
            if approval.status != "approved":
                return None
            if approval.subject_type != "task" or approval.action != "task.run":
                return None
            if approval.details.get("dispatched_task_id"):
                task_row = conn.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (str(approval.details["dispatched_task_id"]),)
                ).fetchone()
                return self._task_from_row(task_row) if task_row else None

            missing = [key for key in ("node_id", "command") if not approval.details.get(key)]
            if missing:
                event = AuditEvent(
                    event_type="approval",
                    subject_type="task",
                    subject_id=approval.approval_id,
                    action="approval/dispatch",
                    outcome="failed",
                    details={"missing": missing, "subject_id": approval.subject_id},
                    created_at=datetime.now(timezone.utc),
                )
                conn.execute(
                    """
                    INSERT INTO audit_events (
                        event_type, subject_type, subject_id, action, outcome,
                        details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_type,
                        event.subject_type,
                        event.subject_id,
                        event.action,
                        event.outcome,
                        json.dumps(event.details, sort_keys=True),
                        _dt(event.created_at),
                    ),
                )
                return None

            task = Task(
                task_id="task_" + uuid4().hex[:12],
                node_id=str(approval.details["node_id"]),
                command=str(approval.details["command"]),
                risk=approval.risk,
                status="pending",
                created_by=str(approval.details.get("created_by") or approval.requested_by),
                created_at=datetime.now(timezone.utc),
                executor=str(approval.details.get("executor") or "worker"),
            )
            # Verify node is managed before creating task
            node_row = conn.execute(
                "SELECT status FROM nodes WHERE node_id = ?", (task.node_id,)
            ).fetchone()
            if node_row is None or node_row["status"] != "managed":
                conn.execute(
                    "INSERT INTO audit_events (event_type, subject_type, subject_id, action, outcome, details_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("approval", "task", approval.approval_id, "approval/dispatch", "failed",
                     json.dumps({"reason": "node not managed", "node_id": task.node_id}, sort_keys=True),
                     _dt(datetime.now(timezone.utc))),
                )
                return None
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, node_id, command, risk, status, created_by, created_at,
                    executor, started_at, completed_at, exit_code, stdout, stderr
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.node_id,
                    task.command,
                    task.risk,
                    task.status,
                    task.created_by,
                    _dt(task.created_at),
                    task.executor,
                    _dt(task.started_at),
                    _dt(task.completed_at),
                    task.exit_code,
                    task.stdout,
                    task.stderr,
                ),
            )
            approval.details = {**approval.details, "dispatched_task_id": task.task_id}
            conn.execute(
                "UPDATE approval_requests SET details_json = ? WHERE approval_id = ?",
                (json.dumps(approval.details, sort_keys=True), approval.approval_id),
            )
            task_event = AuditEvent(
                event_type="task",
                subject_type="task",
                subject_id=task.task_id,
                action="create",
                outcome="ok",
                details={"node_id": task.node_id, "command": task.command, "risk": task.risk, "executor": task.executor},
                created_at=datetime.now(timezone.utc),
            )
            dispatch_event = AuditEvent(
                event_type="approval",
                subject_type="task",
                subject_id=approval.approval_id,
                action="approval/dispatch",
                outcome="ok",
                details={
                    "subject_id": approval.subject_id,
                    "task_id": task.task_id,
                    "node_id": task.node_id,
                    "command": task.command,
                    "risk": task.risk,
                    "executor": task.executor,
                    "created_by": task.created_by,
                },
                created_at=datetime.now(timezone.utc),
            )
            for event in (task_event, dispatch_event):
                conn.execute(
                    """
                    INSERT INTO audit_events (
                        event_type, subject_type, subject_id, action, outcome,
                        details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_type,
                        event.subject_type,
                        event.subject_id,
                        event.action,
                        event.outcome,
                        json.dumps(event.details, sort_keys=True),
                        _dt(event.created_at),
                    ),
                )
            return task

    def create_task(
        self,
        *,
        node_id: str,
        command: str,
        risk: str = "low",
        created_by: str = "hmn",
        executor: str = "worker",
    ) -> Task:
        task = Task(
            task_id="task_" + uuid4().hex[:12],
            node_id=node_id,
            command=command,
            risk=risk,
            status="pending",
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            executor=executor,
        )
        self.save_task(task)
        self.record_audit(
            event_type="task",
            subject_type="task",
            subject_id=task.task_id,
            action="create",
            outcome="ok",
            details={"node_id": node_id, "command": command, "risk": risk, "executor": executor},
        )
        return task

    def save_task(self, task: Task) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, node_id, command, risk, status, created_by, created_at,
                    executor, started_at, completed_at, exit_code, stdout, stderr
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    node_id=excluded.node_id,
                    command=excluded.command,
                    risk=excluded.risk,
                    status=excluded.status,
                    created_by=excluded.created_by,
                    created_at=excluded.created_at,
                    executor=excluded.executor,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    exit_code=excluded.exit_code,
                    stdout=excluded.stdout,
                    stderr=excluded.stderr
                """,
                (
                    task.task_id,
                    task.node_id,
                    task.command,
                    task.risk,
                    task.status,
                    task.created_by,
                    _dt(task.created_at),
                    task.executor,
                    _dt(task.started_at),
                    _dt(task.completed_at),
                    task.exit_code,
                    task.stdout,
                    task.stderr,
                ),
            )

    def _task_from_row(self, row) -> Task:
        return Task(
            task_id=row["task_id"],
            node_id=row["node_id"],
            command=row["command"],
            risk=row["risk"],
            status=row["status"],
            created_by=row["created_by"],
            created_at=_parse_dt(row["created_at"]),
            executor=row["executor"] if "executor" in row.keys() else "worker",
            started_at=_parse_dt(row["started_at"]),
            completed_at=_parse_dt(row["completed_at"]),
            exit_code=row["exit_code"],
            stdout=row["stdout"],
            stderr=row["stderr"],
        )

    def load_task(self, task_id: str) -> Task | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._task_from_row(row) if row else None

    def list_tasks(self) -> list[Task]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [self._task_from_row(row) for row in rows]

    def next_pending_task(self, node_id: str, *, executor: str = "worker") -> Task | None:
        with self.connect() as conn:
            node_row = conn.execute("SELECT status FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
            if node_row is None or node_row["status"] != "managed":
                return None
            row = conn.execute(
                "SELECT * FROM tasks WHERE node_id = ? AND executor = ? AND status = 'pending' ORDER BY created_at LIMIT 1",
                (node_id, executor),
            ).fetchone()
        if row is None:
            return None
        task = self._task_from_row(row)
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        self.save_task(task)
        self.record_audit(
            event_type="task",
            subject_type="task",
            subject_id=task.task_id,
            action="dispatch",
            outcome="ok",
            details={"node_id": node_id},
        )
        return task

    def complete_task(self, task_id: str, *, exit_code: int, stdout: str, stderr: str) -> Task | None:
        task = self.load_task(task_id)
        if task is None:
            return None
        task.exit_code = exit_code
        task.stdout = stdout
        task.stderr = stderr
        task.completed_at = datetime.now(timezone.utc)
        task.status = "succeeded" if exit_code == 0 else "failed"
        self.save_task(task)
        self.record_audit(
            event_type="task",
            subject_type="task",
            subject_id=task.task_id,
            action="task_result",
            outcome=task.status,
            details={"node_id": task.node_id, "exit_code": exit_code},
        )
        return task

    def save_component(self, component: ComponentManifest) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO components (
                    component_id, name, version, api_version, source, status,
                    enabled_at, manifest_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(component_id) DO UPDATE SET
                    name=excluded.name,
                    version=excluded.version,
                    api_version=excluded.api_version,
                    source=excluded.source,
                    status=excluded.status,
                    manifest_json=excluded.manifest_json
                """,
                (
                    component.id,
                    component.name,
                    component.version,
                    component.api_version,
                    component.source,
                    "available",
                    None,
                    json.dumps(component.manifest_json, sort_keys=True),
                ),
            )

    def load_component(self, component_id: str) -> ComponentManifest | None:
        from .components import _from_dict

        with self.connect() as conn:
            row = conn.execute("SELECT manifest_json, source FROM components WHERE component_id = ?", (component_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["manifest_json"])
        return _from_dict(data, source=row["source"])

    def list_components(self) -> list[ComponentManifest]:
        with self.connect() as conn:
            rows = conn.execute("SELECT component_id FROM components ORDER BY component_id").fetchall()
        return [component for row in rows if (component := self.load_component(row["component_id"])) is not None]

    def set_node_component(
        self,
        *,
        node_id: str,
        component_id: str,
        desired_state: str,
        current_state: str,
        config: dict[str, Any] | None = None,
        installed_version: str = "",
        driver: str = "",
        last_plan_id: str = "",
        last_run_id: str = "",
        last_verified_at: datetime | None = None,
    ) -> NodeComponent:
        item = NodeComponent(
            node_id=node_id,
            component_id=component_id,
            desired_state=desired_state,
            current_state=current_state,
            config=config or {},
            installed_version=installed_version,
            driver=driver,
            last_plan_id=last_plan_id,
            last_run_id=last_run_id,
            last_verified_at=last_verified_at,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO node_components (
                    node_id, component_id, desired_state, current_state, config_json,
                    installed_version, driver, last_plan_id, last_run_id, last_verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id, component_id) DO UPDATE SET
                    desired_state=excluded.desired_state,
                    current_state=excluded.current_state,
                    config_json=excluded.config_json,
                    installed_version=excluded.installed_version,
                    driver=excluded.driver,
                    last_plan_id=excluded.last_plan_id,
                    last_run_id=excluded.last_run_id,
                    last_verified_at=excluded.last_verified_at
                """,
                (
                    item.node_id,
                    item.component_id,
                    item.desired_state,
                    item.current_state,
                    json.dumps(item.config, sort_keys=True),
                    item.installed_version,
                    item.driver,
                    item.last_plan_id,
                    item.last_run_id,
                    _dt(item.last_verified_at),
                ),
            )
        return item

    def _node_component_from_row(self, row) -> NodeComponent:
        return NodeComponent(
            node_id=row["node_id"],
            component_id=row["component_id"],
            desired_state=row["desired_state"],
            current_state=row["current_state"],
            config=json.loads(row["config_json"]),
            installed_version=row["installed_version"],
            driver=row["driver"],
            last_plan_id=row["last_plan_id"],
            last_run_id=row["last_run_id"],
            last_verified_at=_parse_dt(row["last_verified_at"]),
        )

    def list_node_components(self, node_id: str | None = None) -> list[NodeComponent]:
        with self.connect() as conn:
            if node_id:
                rows = conn.execute(
                    "SELECT * FROM node_components WHERE node_id = ? ORDER BY component_id",
                    (node_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM node_components ORDER BY node_id, component_id").fetchall()
        return [self._node_component_from_row(row) for row in rows]

    def record_component_run(
        self,
        *,
        component_id: str,
        node_id: str,
        action: str,
        risk: str,
        status: str,
        plan: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        created_by: str = "hmn",
    ) -> ComponentRun:
        run = ComponentRun(
            run_id="crun_" + uuid4().hex[:12],
            component_id=component_id,
            node_id=node_id,
            action=action,
            risk=risk,
            status=status,
            plan=plan or {},
            result=result or {},
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO component_runs (
                    run_id, component_id, node_id, action, risk, status,
                    plan_json, result_json, created_by, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.component_id,
                    run.node_id,
                    run.action,
                    run.risk,
                    run.status,
                    json.dumps(run.plan, sort_keys=True),
                    json.dumps(run.result, sort_keys=True),
                    run.created_by,
                    _dt(run.created_at),
                    _dt(run.completed_at),
                ),
            )
        self.record_audit(
            event_type="component",
            subject_type="component",
            subject_id=component_id,
            action=action,
            outcome=status,
            details={"node_id": node_id, "run_id": run.run_id, "risk": risk},
        )
        return run

    def _component_run_from_row(self, row) -> ComponentRun:
        return ComponentRun(
            run_id=row["run_id"],
            component_id=row["component_id"],
            node_id=row["node_id"],
            action=row["action"],
            risk=row["risk"],
            status=row["status"],
            plan=json.loads(row["plan_json"]),
            result=json.loads(row["result_json"]),
            created_by=row["created_by"],
            created_at=_parse_dt(row["created_at"]),
            completed_at=_parse_dt(row["completed_at"]),
        )

    def save_component_run(self, run: ComponentRun) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE component_runs
                SET status = ?, plan_json = ?, result_json = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (
                    run.status,
                    json.dumps(run.plan, sort_keys=True),
                    json.dumps(run.result, sort_keys=True),
                    _dt(run.completed_at),
                    run.run_id,
                ),
            )

    def load_component_run(self, run_id: str) -> ComponentRun | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM component_runs WHERE run_id = ?", (run_id,)).fetchone()
        return self._component_run_from_row(row) if row else None

    def list_component_runs(self) -> list[ComponentRun]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM component_runs ORDER BY created_at DESC").fetchall()
        return [self._component_run_from_row(row) for row in rows]

    def record_monitor_snapshot(
        self,
        *,
        node_id: str,
        health: str,
        reason: str,
        heartbeat_at: datetime | None,
        age_seconds: int | None,
        runtime_profile: str,
        service_manager: str,
        worker_protocol_version: str,
        worker_version: str,
        exec_mode: str,
        facts_summary: dict[str, Any] | None = None,
    ) -> MonitorSnapshot:
        snapshot = MonitorSnapshot(
            snapshot_id="mon_" + uuid4().hex[:12],
            node_id=node_id,
            health=health,
            reason=reason,
            heartbeat_at=heartbeat_at,
            age_seconds=age_seconds,
            runtime_profile=runtime_profile,
            service_manager=service_manager,
            worker_protocol_version=worker_protocol_version,
            worker_version=worker_version,
            exec_mode=exec_mode,
            facts_summary=facts_summary or {},
            created_at=datetime.now(timezone.utc),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitor_snapshots (
                    snapshot_id, node_id, health, reason, heartbeat_at,
                    age_seconds, runtime_profile, service_manager,
                    worker_protocol_version, worker_version, exec_mode,
                    facts_summary_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.node_id,
                    snapshot.health,
                    snapshot.reason,
                    _dt(snapshot.heartbeat_at),
                    snapshot.age_seconds,
                    snapshot.runtime_profile,
                    snapshot.service_manager,
                    snapshot.worker_protocol_version,
                    snapshot.worker_version,
                    snapshot.exec_mode,
                    json.dumps(snapshot.facts_summary, sort_keys=True),
                    _dt(snapshot.created_at),
                ),
            )
        return snapshot

    def _monitor_snapshot_from_row(self, row) -> MonitorSnapshot:
        return MonitorSnapshot(
            snapshot_id=row["snapshot_id"],
            node_id=row["node_id"],
            health=row["health"],
            reason=row["reason"],
            heartbeat_at=_parse_dt(row["heartbeat_at"]),
            age_seconds=row["age_seconds"],
            runtime_profile=row["runtime_profile"],
            service_manager=row["service_manager"],
            worker_protocol_version=row["worker_protocol_version"],
            worker_version=row["worker_version"],
            exec_mode=row["exec_mode"],
            facts_summary=json.loads(row["facts_summary_json"]),
            created_at=_parse_dt(row["created_at"]),
        )

    def latest_monitor_snapshot(self, node_id: str) -> MonitorSnapshot | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM monitor_snapshots WHERE node_id = ? ORDER BY created_at DESC LIMIT 1",
                (node_id,),
            ).fetchone()
        return self._monitor_snapshot_from_row(row) if row else None

    def list_monitor_snapshots(self, node_id: str | None = None) -> list[MonitorSnapshot]:
        with self.connect() as conn:
            if node_id:
                rows = conn.execute(
                    "SELECT * FROM monitor_snapshots WHERE node_id = ? ORDER BY created_at DESC",
                    (node_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM monitor_snapshots ORDER BY created_at DESC").fetchall()
        return [self._monitor_snapshot_from_row(row) for row in rows]

    def save_token(self, token: JoinToken) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO join_tokens (
                    value, trust_level, labels_json, expires_at, status,
                    created_at, used_at, node_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(value) DO UPDATE SET
                    trust_level=excluded.trust_level,
                    labels_json=excluded.labels_json,
                    expires_at=excluded.expires_at,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    used_at=excluded.used_at,
                    node_fingerprint=excluded.node_fingerprint
                """,
                (
                    token.value,
                    token.trust_level,
                    json.dumps(token.labels),
                    _dt(token.expires_at),
                    token.status,
                    _dt(token.created_at),
                    _dt(token.used_at),
                    token.node_fingerprint,
                ),
            )

    def load_token(self, value: str) -> JoinToken | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM join_tokens WHERE value = ?", (value,)).fetchone()
        if row is None:
            return None
        return JoinToken(
            value=row["value"],
            trust_level=row["trust_level"],
            labels=json.loads(row["labels_json"]),
            expires_at=_parse_dt(row["expires_at"]),
            status=row["status"],
            created_at=_parse_dt(row["created_at"]),
            used_at=_parse_dt(row["used_at"]),
            node_fingerprint=row["node_fingerprint"],
        )

    def consume_token(self, value: str, *, node_fingerprint: str) -> JoinToken | None:
        token = self.load_token(value)
        if token is None:
            return None
        now = datetime.now(timezone.utc)
        if token.status == "pending" and now > token.expires_at:
            token.status = "expired"
            self.save_token(token)
            return None
        if token.status != "pending":
            return None
        token.status = "used"
        token.used_at = now
        token.node_fingerprint = node_fingerprint
        self.save_token(token)
        return token

    def expire_pending_tokens(self, *, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT value FROM join_tokens
                WHERE status = 'pending' AND expires_at < ?
                ORDER BY created_at DESC
                """,
                (_dt(now),),
            ).fetchall()
            values = [row["value"] for row in rows]
            if values:
                conn.executemany("UPDATE join_tokens SET status = 'expired' WHERE value = ?", [(value,) for value in values])
        return values

    def list_tokens(self) -> list[JoinToken]:
        self.expire_pending_tokens()
        with self.connect() as conn:
            rows = conn.execute("SELECT value FROM join_tokens ORDER BY created_at DESC").fetchall()
        return [token for row in rows if (token := self.load_token(row["value"])) is not None]

    def register_pending_node(
        self,
        *,
        node_id: str,
        fingerprint: str,
        hostname: str,
        addresses: list[str],
        trust_level: str,
        labels: list[str],
    ) -> Node:
        node = Node(
            node_id=node_id,
            fingerprint=fingerprint,
            hostname=hostname,
            addresses=list(addresses),
            trust_level=trust_level,
            labels=list(labels),
        )
        self.save_node(node)
        return node

    def save_node(self, node: Node) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO nodes (
                    node_id, fingerprint, hostname, addresses_json, trust_level,
                    labels_json, status, permission_bundles_json, ssh_host, ssh_user, ssh_port,
                    network_provider, network_node_id, network_ip, network_tags_json, network_online
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    hostname=excluded.hostname,
                    addresses_json=excluded.addresses_json,
                    trust_level=excluded.trust_level,
                    labels_json=excluded.labels_json,
                    status=excluded.status,
                    permission_bundles_json=excluded.permission_bundles_json,
                    ssh_host=excluded.ssh_host,
                    ssh_user=excluded.ssh_user,
                    ssh_port=excluded.ssh_port,
                    network_provider=excluded.network_provider,
                    network_node_id=excluded.network_node_id,
                    network_ip=excluded.network_ip,
                    network_tags_json=excluded.network_tags_json,
                    network_online=excluded.network_online
                """,
                (
                    node.node_id,
                    node.fingerprint,
                    node.hostname,
                    json.dumps(node.addresses),
                    node.trust_level,
                    json.dumps(node.labels),
                    node.status,
                    json.dumps(node.permission_bundles),
                    node.ssh_host,
                    node.ssh_user,
                    node.ssh_port,
                    node.network_provider,
                    node.network_node_id,
                    node.network_ip,
                    json.dumps(node.network_tags),
                    1 if node.network_online else 0,
                ),
            )

    def rotate_node_fingerprint(self, node_id: str, *, current_fingerprint: str | None, new_fingerprint: str) -> Node | None:
        node = self.load_node(node_id)
        if node is None:
            return None
        if current_fingerprint is not None and node.fingerprint != current_fingerprint:
            return None
        node.fingerprint = new_fingerprint
        self.save_node(node)
        self.record_audit(
            event_type="node",
            subject_type="node",
            subject_id=node.node_id,
            action="rotate_fingerprint",
            outcome="ok",
            details={
                "old_fingerprint_sha256": current_fingerprint or "operator-forced",
                "new_fingerprint_sha256": new_fingerprint,
            },
        )
        return node

    def load_node(self, node_id: str) -> Node | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        return Node(
            node_id=row["node_id"],
            fingerprint=row["fingerprint"],
            hostname=row["hostname"],
            addresses=json.loads(row["addresses_json"]),
            trust_level=row["trust_level"],
            labels=json.loads(row["labels_json"]),
            status=row["status"],
            permission_bundles=json.loads(row["permission_bundles_json"]),
            ssh_host=row["ssh_host"] if "ssh_host" in row.keys() else "",
            ssh_user=row["ssh_user"] if "ssh_user" in row.keys() else "",
            ssh_port=row["ssh_port"] if "ssh_port" in row.keys() else 22,
            network_provider=row["network_provider"] if "network_provider" in row.keys() else "",
            network_node_id=row["network_node_id"] if "network_node_id" in row.keys() else "",
            network_ip=row["network_ip"] if "network_ip" in row.keys() else "",
            network_tags=json.loads(row["network_tags_json"]) if "network_tags_json" in row.keys() else [],
            network_online=bool(row["network_online"]) if "network_online" in row.keys() else False,
        )

    def list_nodes(self) -> list[Node]:
        with self.connect() as conn:
            rows = conn.execute("SELECT node_id FROM nodes ORDER BY hostname").fetchall()
        return [node for row in rows if (node := self.load_node(row["node_id"])) is not None]
