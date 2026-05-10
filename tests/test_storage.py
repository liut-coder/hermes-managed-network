from datetime import datetime, timedelta, timezone

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


def test_sqlite_marks_pending_expired_tokens(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    token_store = JoinTokenStore(now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    expired = token_store.create(trust_level="B", labels=[], ttl=timedelta(minutes=1))
    fresh = JoinTokenStore().create(trust_level="C", labels=["fresh"], ttl=timedelta(minutes=30))
    store.save_token(expired)
    store.save_token(fresh)

    changed = store.expire_pending_tokens(now=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc))

    assert changed == [expired.value]
    assert store.load_token(expired.value).status == "expired"
    assert store.load_token(fresh.value).status == "pending"


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


def test_sqlite_persists_approval_requests(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)

    approval = store.create_approval_request(
        subject_type="task",
        subject_id="task_high",
        action="task.run",
        risk="high",
        requested_by="hmn",
        details={"node_id": "node_1", "command": "systemctl restart nginx"},
    )

    assert approval.approval_id.startswith("appr_")
    assert approval.status == "pending"
    assert approval.risk == "high"
    loaded = store.load_approval_request(approval.approval_id)
    assert loaded == approval
    assert store.list_approval_requests()[0] == approval

    approved = store.resolve_approval_request(approval.approval_id, status="approved", decided_by="operator")
    assert approved is not None
    assert approved.status == "approved"
    assert approved.decided_by == "operator"
    assert approved.decided_at is not None

    events = store.list_audit_events()
    assert [event.action for event in events] == ["approval/request", "approval/approved"]
    assert events[0].details["risk"] == "high"


def test_high_risk_task_cli_creates_pending_approval_instead_of_task(tmp_path):
    from typer.testing import CliRunner
    from hermes_managed_network.cli import app

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_high_risk",
            fingerprint="sha256:task",
            hostname="task-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    result = runner.invoke(app, ["task", "run", "reboot", "--risk", "high", "--db", str(db)])

    assert result.exit_code == 1
    assert "需要审批" in result.stdout
    assert SQLiteStore(db).list_tasks() == []
    approvals = SQLiteStore(db).list_approval_requests()
    assert len(approvals) == 1
    assert approvals[0].status == "pending"
    assert approvals[0].subject_type == "task"
    assert approvals[0].details["command"] == "reboot"


def test_approval_cli_list_show_approve_reject(tmp_path):
    from typer.testing import CliRunner
    from hermes_managed_network.cli import app

    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    approval = store.create_approval_request(
        subject_type="component",
        subject_id="run_1",
        action="component.apply",
        risk="high",
        requested_by="hmn",
        details={"component_id": "reverse-proxy"},
    )

    listed = runner.invoke(app, ["approval", "list", "--db", str(db)])
    assert listed.exit_code == 0
    assert approval.approval_id in listed.stdout
    assert "pending" in listed.stdout

    shown = runner.invoke(app, ["approval", "show", approval.approval_id, "--db", str(db)])
    assert shown.exit_code == 0
    assert "component.apply" in shown.stdout
    assert "reverse-proxy" in shown.stdout

    approved = runner.invoke(app, ["approval", "approve", approval.approval_id, "--by", "Misk", "--db", str(db)])
    assert approved.exit_code == 0
    assert "approved" in approved.stdout
    assert SQLiteStore(db).load_approval_request(approval.approval_id).status == "approved"

    second = store.create_approval_request(
        subject_type="task",
        subject_id="task_2",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"command": "rm -rf /tmp/demo"},
    )
    rejected = runner.invoke(app, ["approval", "reject", second.approval_id, "--by", "Misk", "--db", str(db)])
    assert rejected.exit_code == 0
    assert "rejected" in rejected.stdout
    assert SQLiteStore(db).load_approval_request(second.approval_id).status == "rejected"
