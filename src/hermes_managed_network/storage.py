from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .inventory import Node
from .tokens import JoinToken


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
                    permission_bundles_json TEXT NOT NULL
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
                """
            )

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

    def list_tokens(self) -> list[JoinToken]:
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
                    labels_json, status, permission_bundles_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    hostname=excluded.hostname,
                    addresses_json=excluded.addresses_json,
                    trust_level=excluded.trust_level,
                    labels_json=excluded.labels_json,
                    status=excluded.status,
                    permission_bundles_json=excluded.permission_bundles_json
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
                ),
            )

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
        )

    def list_nodes(self) -> list[Node]:
        with self.connect() as conn:
            rows = conn.execute("SELECT node_id FROM nodes ORDER BY hostname").fetchall()
        return [node for row in rows if (node := self.load_node(row["node_id"])) is not None]
