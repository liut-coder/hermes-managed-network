from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from typing import Callable

VALID_TRUST_LEVELS = {"A", "B", "C"}


@dataclass
class JoinToken:
    value: str
    trust_level: str
    labels: list[str]
    expires_at: datetime
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    used_at: datetime | None = None
    node_fingerprint: str | None = None


class JoinTokenStore:
    """In-memory join token store for the MVP control plane.

    This intentionally keeps persistence out of v0.2 so the token lifecycle can
    be tested and stabilized before adding SQLite/Postgres storage.
    """

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._tokens: dict[str, JoinToken] = {}

    def create(
        self,
        *,
        trust_level: str,
        labels: list[str],
        ttl: timedelta = timedelta(minutes=30),
    ) -> JoinToken:
        if trust_level not in VALID_TRUST_LEVELS:
            raise ValueError("trust_level must be one of A, B, C")
        created_at = self._now()
        value = "j_" + token_urlsafe(24)
        token = JoinToken(
            value=value,
            trust_level=trust_level,
            labels=list(labels),
            created_at=created_at,
            expires_at=created_at + ttl,
        )
        self._tokens[value] = token
        return token

    def get(self, value: str) -> JoinToken | None:
        token = self._tokens.get(value)
        if token is not None and token.status == "pending" and self._now() > token.expires_at:
            token.status = "expired"
        return token

    def consume(self, value: str, *, node_fingerprint: str) -> JoinToken | None:
        token = self.get(value)
        if token is None or token.status != "pending":
            return None
        token.status = "used"
        token.used_at = self._now()
        token.node_fingerprint = node_fingerprint
        return token
