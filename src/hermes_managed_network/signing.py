from __future__ import annotations

import hmac
from hashlib import sha256

SIGNATURE_PREFIX = "hmac-sha256:"


def _task_message(*, task_id: str, command: str, risk: str) -> bytes:
    return f"{task_id}\n{risk}\n{command}".encode("utf-8")


def sign_task_payload(*, node_fingerprint: str, task_id: str, command: str, risk: str) -> str:
    digest = hmac.new(
        node_fingerprint.encode("utf-8"),
        _task_message(task_id=task_id, command=command, risk=risk),
        sha256,
    ).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_task_signature(*, node_fingerprint: str, task_id: str, command: str, risk: str, signature: str) -> bool:
    expected = sign_task_payload(
        node_fingerprint=node_fingerprint,
        task_id=task_id,
        command=command,
        risk=risk,
    )
    return hmac.compare_digest(expected, signature)
