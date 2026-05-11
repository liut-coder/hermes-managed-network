from __future__ import annotations

from hermes_managed_network.signing import sign_task_payload, verify_task_signature


def test_task_signature_verifies_exact_payload_only():
    signature = sign_task_payload(
        node_fingerprint="sha256:node-secret",
        task_id="task_123",
        command="uptime",
        risk="low",
    )

    assert verify_task_signature(
        node_fingerprint="sha256:node-secret",
        task_id="task_123",
        command="uptime",
        risk="low",
        signature=signature,
    ) is True
    assert verify_task_signature(
        node_fingerprint="sha256:node-secret",
        task_id="task_123",
        command="rm -rf /",
        risk="low",
        signature=signature,
    ) is False
    assert verify_task_signature(
        node_fingerprint="sha256:other",
        task_id="task_123",
        command="uptime",
        risk="low",
        signature=signature,
    ) is False
