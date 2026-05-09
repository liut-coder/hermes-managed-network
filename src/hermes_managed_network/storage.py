from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .inventory import Node
from .tokens import JoinToken


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
                """
            )

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

    def list_tokens(self) -> list[JoinToken]:
        with self.connect() as conn:
            rows = conn.execute("SELECT value FROM join_tokens ORDER BY created_at DESC").fetchall()
        return [token for row in rows if (token := self.load_token(row["value"])) is not None]

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
