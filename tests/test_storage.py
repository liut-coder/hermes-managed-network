from datetime import timedelta

from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore
from hermes_managed_network.tokens import JoinTokenStore


def test_sqlite_persists_join_tokens(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token = JoinTokenStore().create(trust_level="B", labels=["managed"], ttl=timedelta(minutes=5))

    store.save_token(token)
    loaded = store.load_token(token.value)

    assert loaded is not None
    assert loaded.value == token.value
    assert loaded.trust_level == "B"
    assert loaded.labels == ["managed"]


def test_sqlite_persists_nodes(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    node = Node(
        node_id="node-1",
        fingerprint="sha256:abc",
        hostname="demo",
        addresses=["100.64.0.10"],
        trust_level="A",
        labels=["prod"],
        status="managed",
        permission_bundles=["observe"],
    )

    store.save_node(node)
    loaded = store.load_node("node-1")

    assert loaded == node
    assert store.list_nodes() == [node]


def test_sqlite_records_audit_events(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)

    event = store.record_audit(
        event_type="token",
        subject_type="join_token",
        subject_id="j_demo",
        action="create",
        outcome="ok",
        details={"trust_level": "B"},
    )

    assert event.outcome == "ok"
    events = store.list_audit_events()
    assert len(events) == 1
    assert events[0].subject_id == "j_demo"
    assert events[0].details == {"trust_level": "B"}
