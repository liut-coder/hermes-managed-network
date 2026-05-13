from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore
from hermes_managed_network.tokens import JoinTokenStore


def _save_managed_node(store: SQLiteStore, node_id: str = "node_watchdog") -> None:
    store.save_node(
        Node(
            node_id=node_id,
            fingerprint=f"sha256:{node_id}",
            hostname=node_id,
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe", "task"],
        )
    )


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


def test_expire_stuck_tasks_marks_only_expired_running_worker_tasks_failed_and_audits(tmp_path):
    store = SQLiteStore(tmp_path / "hmn.db")
    _save_managed_node(store)
    old = store.create_task(node_id="node_watchdog", command="old")
    store.claim_next_task("node_watchdog", lease_seconds=30)
    with store.connect() as conn:
        conn.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
            (datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).isoformat(), old.task_id),
        )

    expired = store.expire_stuck_tasks(now=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc))

    assert expired == [old.task_id]
    loaded = store.load_task(old.task_id)
    assert loaded.status == "failed"
    assert loaded.failure_reason == "worker_lease_expired"
    assert loaded.completed_at is not None
    assert store.list_audit_events()[-1].action == "watchdog/expire"


def test_expire_stuck_tasks_does_not_touch_fresh_pending_or_terminal_tasks(tmp_path):
    store = SQLiteStore(tmp_path / "hmn.db")
    _save_managed_node(store)
    fresh = store.create_task(node_id="node_watchdog", command="fresh")
    store.claim_next_task("node_watchdog", lease_seconds=900)
    terminal = store.create_task(node_id="node_watchdog", command="terminal")
    # complete_task only accepts claimed/running tasks; claim the terminal task explicitly.
    store.claim_next_task("node_watchdog", lease_seconds=900)
    store.complete_task(terminal.task_id, exit_code=0, stdout="ok", stderr="")
    pending = store.create_task(node_id="node_watchdog", command="pending")

    expired = store.expire_stuck_tasks(now=datetime.now(timezone.utc))

    assert expired == []
    assert store.load_task(fresh.task_id).status == "running"
    assert store.load_task(pending.task_id).status == "pending"
    assert store.load_task(terminal.task_id).status == "succeeded"


def test_claim_next_task_is_atomic_under_concurrent_workers(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store, "node_atomic")
    task = store.create_task(node_id="node_atomic", command="uptime")

    def claim_once():
        return SQLiteStore(db).claim_next_task("node_atomic", lease_seconds=900)

    with ThreadPoolExecutor(max_workers=8) as executor:
        claimed = list(executor.map(lambda _: claim_once(), range(16)))

    claimed_ids = [item.task_id for item in claimed if item is not None]
    assert claimed_ids == [task.task_id]
    loaded = SQLiteStore(db).load_task(task.task_id)
    assert loaded.status == "running"
    assert loaded.claimed_at is not None
    assert loaded.lease_expires_at is not None
    assert loaded.attempt_count == 1


def test_complete_task_does_not_overwrite_terminal_watchdog_failure(tmp_path):
    store = SQLiteStore(tmp_path / "hmn.db")
    _save_managed_node(store)
    task = store.create_task(node_id="node_watchdog", command="slow")
    store.claim_next_task("node_watchdog", lease_seconds=1)
    store.expire_stuck_tasks(now=datetime.now(timezone.utc) + timedelta(seconds=2))

    completed = store.complete_task(task.task_id, exit_code=0, stdout="late", stderr="")

    assert completed is None
    loaded = store.load_task(task.task_id)
    assert loaded.status == "failed"
    assert loaded.failure_reason == "worker_lease_expired"
    assert loaded.stdout == ""


def test_complete_task_only_accepts_running_tasks(tmp_path):
    store = SQLiteStore(tmp_path / "hmn.db")
    _save_managed_node(store)
    task = store.create_task(node_id="node_watchdog", command="pending")

    completed = store.complete_task(task.task_id, exit_code=0, stdout="bad", stderr="")

    assert completed is None
    loaded = store.load_task(task.task_id)
    assert loaded.status == "pending"
    assert loaded.stdout == ""


def test_complete_task_does_not_race_over_watchdog_expiry(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_managed_node(store)
    task = store.create_task(node_id="node_watchdog", command="slow")
    stale_loaded = store.claim_next_task("node_watchdog", lease_seconds=1)
    assert stale_loaded is not None
    store.expire_stuck_tasks(now=datetime.now(timezone.utc) + timedelta(seconds=2))

    completed = SQLiteStore(db).complete_task(task.task_id, exit_code=0, stdout="late", stderr="")

    assert completed is None
    loaded = store.load_task(task.task_id)
    assert loaded.status == "failed"
    assert loaded.failure_reason == "worker_lease_expired"
    assert loaded.stdout == ""


def test_complete_task_caps_stdout_and_stderr(tmp_path):
    store = SQLiteStore(tmp_path / "hmn.db")
    _save_managed_node(store)
    task = store.create_task(node_id="node_watchdog", command="noisy")
    store.claim_next_task("node_watchdog")

    completed = store.complete_task(task.task_id, exit_code=1, stdout="o" * 70000, stderr="e" * 70000)

    assert completed is not None
    assert len(completed.stdout) < 70000
    assert len(completed.stderr) < 70000
    assert "truncated" in completed.stdout
    assert "truncated" in completed.stderr


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


def test_sqlite_persists_node_ssh_fields(tmp_path):
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    node = Node(
        node_id="node-ssh",
        fingerprint="sha256:ssh",
        hostname="ssh-node",
        addresses=["10.0.0.10"],
        trust_level="B",
        labels=["role=edge"],
        status="managed",
        permission_bundles=["observe"],
        ssh_host="100.64.0.10",
        ssh_user="ops",
        ssh_port=2222,
    )

    store.save_node(node)
    loaded = store.load_node("node-ssh")

    assert loaded is not None
    assert loaded.ssh_host == "100.64.0.10"
    assert loaded.ssh_user == "ops"
    assert loaded.ssh_port == 2222


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

    store.save_node(
        Node(node_id="node_1", fingerprint="sha256:node1", hostname="node1", addresses=[],
             trust_level="B", labels=[], status="managed", permission_bundles=[])
    )

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
    store.save_node(
        Node(node_id="node_approve", fingerprint="sha256:naprv", hostname="naprv",
             addresses=[], trust_level="B", labels=[], status="managed", permission_bundles=[])
    )
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
    store.save_node(
        Node(node_id="node_repeat", fingerprint="sha256:nrep", hostname="nrep",
             addresses=[], trust_level="B", labels=[], status="managed", permission_bundles=[])
    )
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
    store.save_node(
        Node(node_id="node_risk", fingerprint="sha256:nrisk", hostname="nrisk",
             addresses=[], trust_level="B", labels=[], status="managed", permission_bundles=[])
    )
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
    assert SQLiteStore(db).list_tasks() == []

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
