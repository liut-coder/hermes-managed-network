from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def test_approval_cli_approve_dispatches_high_risk_task(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_cli_approve",
            fingerprint="sha256:cli",
            hostname="cli-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    blocked = runner.invoke(app, ["task", "run", "reboot", "--risk", "high", "--db", str(db)])
    assert blocked.exit_code == 1
    approval = SQLiteStore(db).list_approval_requests()[0]

    approved = runner.invoke(app, ["approval", "approve", approval.approval_id, "--by", "Misk", "--db", str(db)])

    assert approved.exit_code == 0
    assert "approved" in approved.stdout
    assert "已创建任务" in approved.stdout
    tasks = SQLiteStore(db).list_tasks()
    assert len(tasks) == 1
    assert tasks[0].node_id == "node_cli_approve"
    assert tasks[0].command == "reboot"
    assert tasks[0].risk == "high"
    assert SQLiteStore(db).load_approval_request(approval.approval_id).details["dispatched_task_id"] == tasks[0].task_id


def test_approval_cli_reject_keeps_high_risk_task_non_executable(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    store.save_node(
        Node(
            node_id="node_cli_reject",
            fingerprint="sha256:cli-reject",
            hostname="cli-reject-node",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )

    blocked = runner.invoke(app, ["task", "run", "reboot", "--risk", "critical", "--db", str(db)])
    assert blocked.exit_code == 1
    approval = SQLiteStore(db).list_approval_requests()[0]

    rejected = runner.invoke(app, ["approval", "reject", approval.approval_id, "--by", "Misk", "--db", str(db)])
    later_approve = runner.invoke(app, ["approval", "approve", approval.approval_id, "--by", "Other", "--db", str(db)])

    assert rejected.exit_code == 0
    assert "rejected" in rejected.stdout
    assert later_approve.exit_code == 1
    assert SQLiteStore(db).load_approval_request(approval.approval_id).status == "rejected"
    assert SQLiteStore(db).list_tasks() == []
