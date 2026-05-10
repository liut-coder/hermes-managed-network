from concurrent.futures import ThreadPoolExecutor
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
    assert [event.action for event in events] == ["approval/request", "approval/approved", "create", "approval/dispatch"]
    assert events[0].details["risk"] == "high"
    assert len(store.list_tasks()) == 1


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
    assert approvals[0].details["node_id"] == "node_high_risk"
    assert approvals[0].details["command"] == "reboot"
    assert approvals[0].details["risk"] == "high"
    assert approvals[0].details["created_by"] == "hmn"


def test_approving_task_request_dispatches_real_task_and_audits(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="high",
        requested_by="hmn",
        details={
            "node_id": "node_approve",
            "command": "systemctl restart nginx",
            "risk": "high",
            "created_by": "operator-request",
        },
    )

    approved = store.resolve_approval_request(approval.approval_id, status="approved", decided_by="Misk")

    assert approved is not None
    tasks = store.list_tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.node_id == "node_approve"
    assert task.command == "systemctl restart nginx"
    assert task.risk == "high"
    assert task.created_by == "operator-request"
    assert store.load_approval_request(approval.approval_id).details["dispatched_task_id"] == task.task_id
    events = store.list_audit_events()
    assert [event.action for event in events] == ["approval/request", "approval/approved", "create", "approval/dispatch"]
    assert events[-1].subject_id == approval.approval_id
    assert events[-1].details["task_id"] == task.task_id


def test_dispatch_approved_task_request_is_idempotent_under_repeated_calls(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"node_id": "node_repeat", "command": "reboot", "risk": "critical", "created_by": "hmn"},
    )
    store.resolve_approval_request(approval.approval_id, status="approved", decided_by="Misk")

    def dispatch_again():
        return SQLiteStore(db).dispatch_approved_task_request(approval.approval_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        dispatched = list(executor.map(lambda _: dispatch_again(), range(16)))

    tasks = SQLiteStore(db).list_tasks()
    assert len(tasks) == 1
    assert {task.task_id for task in dispatched if task is not None} == {tasks[0].task_id}
    assert SQLiteStore(db).load_approval_request(approval.approval_id).details["dispatched_task_id"] == tasks[0].task_id
    assert [event.action for event in SQLiteStore(db).list_audit_events()].count("approval/dispatch") == 1


def test_dispatch_uses_approval_risk_even_when_details_risk_disagrees(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"node_id": "node_risk", "command": "reboot", "risk": "low", "created_by": "hmn"},
    )

    store.resolve_approval_request(approval.approval_id, status="approved", decided_by="Misk")

    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].risk == "critical"
    assert store.load_approval_request(approval.approval_id).details["risk"] == "low"


def test_rejecting_task_request_never_dispatches_task_and_audits(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    approval = store.create_approval_request(
        subject_type="task",
        subject_id="pending-task",
        action="task.run",
        risk="critical",
        requested_by="hmn",
        details={"node_id": "node_reject", "command": "reboot", "risk": "critical", "created_by": "hmn"},
    )

    rejected = store.resolve_approval_request(approval.approval_id, status="rejected", decided_by="Misk")
    later_approval_attempt = store.resolve_approval_request(approval.approval_id, status="approved", decided_by="Other")

    assert rejected is not None
    assert rejected.status == "rejected"
    assert later_approval_attempt.status == "rejected"
    assert store.list_tasks() == []
    assert [event.action for event in store.list_audit_events()] == ["approval/request", "approval/rejected"]


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
