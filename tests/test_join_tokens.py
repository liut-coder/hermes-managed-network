from datetime import datetime, timezone, timedelta

from hermes_managed_network.tokens import JoinTokenStore


def test_join_token_can_be_created_and_consumed_once():
    now = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    store = JoinTokenStore(now=lambda: now)

    token = store.create(trust_level="B", labels=["managed", "region:hk"])

    assert token.value.startswith("j_")
    assert token.trust_level == "B"
    assert token.labels == ["managed", "region:hk"]
    assert token.status == "pending"

    consumed = store.consume(token.value, node_fingerprint="sha256:abc")
    assert consumed.status == "used"
    assert consumed.node_fingerprint == "sha256:abc"

    assert store.consume(token.value, node_fingerprint="sha256:def") is None


def test_expired_join_token_cannot_be_consumed():
    current = datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc)
    store = JoinTokenStore(now=lambda: current)
    token = store.create(trust_level="A", labels=[], ttl=timedelta(minutes=30))

    current = datetime(2026, 5, 9, 12, 31, tzinfo=timezone.utc)

    assert store.consume(token.value, node_fingerprint="sha256:abc") is None
    assert store.get(token.value).status == "expired"


def test_invalid_trust_level_is_rejected():
    store = JoinTokenStore()

    try:
        store.create(trust_level="root", labels=[])
    except ValueError as exc:
        assert "trust_level" in str(exc)
    else:
        raise AssertionError("expected ValueError")
