from datetime import datetime, timedelta, timezone

from typer.testing import CliRunner

from hermes_managed_network.cli import app
from hermes_managed_network.inventory import Node
from hermes_managed_network.storage import SQLiteStore


def _save_node(store: SQLiteStore, node_id: str, hostname: str | None = None) -> None:
    store.save_node(
        Node(
            node_id=node_id,
            fingerprint="sha256:" + node_id,
            hostname=hostname or f"{node_id}-host",
            addresses=[],
            trust_level="B",
            labels=[],
            status="managed",
            permission_bundles=["observe"],
        )
    )


def _record_heartbeat(
    store: SQLiteStore,
    node_id: str,
    *,
    outcome: str = "ok",
    compatible: bool = True,
    facts: dict | None = None,
) -> None:
    store.record_audit(
        event_type="node",
        subject_type="node",
        subject_id=node_id,
        action="heartbeat",
        outcome=outcome,
        details={
            "status": outcome,
            "facts": facts or {"worker_protocol_version": "0.1", "worker_version": "0.1.0"},
            "worker_compatible": compatible,
        },
    )


def test_monitor_run_once_classifies_nodes_persists_snapshots_and_audit(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_node(store, "node_ok", "ok-host")
    _save_node(store, "node_bad", "bad-host")
    _save_node(store, "node_missing", "missing-host")
    _record_heartbeat(
        store,
        "node_ok",
        facts={
            "worker_protocol_version": "0.1",
            "worker_version": "0.1.0",
            "exec_enabled": False,
            "load_average": {"1m": "0.12", "5m": "0.08", "15m": "0.05"},
            "memory": {"total_kb": 1000, "available_kb": 400, "free_kb": 200},
            "disk": {"path": "/", "total_bytes": 1000, "used_bytes": 420, "free_bytes": 580},
        },
    )
    _record_heartbeat(store, "node_bad", compatible=False)

    result = runner.invoke(app, ["monitor", "run-once", "--db", str(db)])

    assert result.exit_code == 1
    assert "monitor run: nodes=3 ok=1 warn=0 critical=2" in result.stdout
    assert "node_ok\tok\tfresh_heartbeat" in result.stdout
    assert "node_bad\tcritical\tworker_protocol_incompatible" in result.stdout
    assert "node_missing\tcritical\tmissing_heartbeat" in result.stdout
    snapshots = SQLiteStore(db).list_monitor_snapshots()
    assert sorted(snapshot.node_id for snapshot in snapshots) == ["node_bad", "node_missing", "node_ok"]
    by_node = {snapshot.node_id: snapshot for snapshot in snapshots}
    assert by_node["node_ok"].health == "ok"
    assert by_node["node_ok"].facts_summary["disk_used_percent"] == 42
    assert by_node["node_bad"].health == "critical"
    assert by_node["node_bad"].reason == "worker_protocol_incompatible"
    assert by_node["node_missing"].reason == "missing_heartbeat"
    events = SQLiteStore(db).list_audit_events()
    monitor_events = [event for event in events if event.event_type == "monitor" and event.action == "run-once"]
    assert len(monitor_events) == 3
    assert monitor_events[-1].details["health"] in {"ok", "critical"}


def test_monitor_status_prefers_latest_snapshot_and_shows_compact_health(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_node(store, "node_status", "status-host")
    _record_heartbeat(
        store,
        "node_status",
        facts={
            "worker_protocol_version": "0.1",
            "worker_version": "0.1.0",
            "exec_enabled": False,
            "load_average": {"1m": "0.12", "5m": "0.08", "15m": "0.05"},
            "memory": {"total_kb": 1000, "available_kb": 320, "free_kb": 120},
            "disk": {"path": "/", "total_bytes": 1000, "used_bytes": 420, "free_bytes": 580},
        },
    )
    runner.invoke(app, ["monitor", "run-once", "--db", str(db)])

    result = runner.invoke(app, ["monitor", "status", "--node", "node_status", "--db", str(db)])

    assert result.exit_code == 0
    assert "node: node_status" in result.stdout
    assert "health: ok" in result.stdout
    assert "reason: fresh_heartbeat" in result.stdout
    assert "runtime:" in result.stdout
    assert "worker_protocol: 0.1" in result.stdout
    assert "exec: SAFE" in result.stdout
    assert "disk_used_percent: 42" in result.stdout
    assert "memory_used_percent: 68" in result.stdout
    assert "load_1m: 0.12" in result.stdout


def test_monitor_report_summarizes_fleet_for_mobile(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_node(store, "node_ok", "ok-host")
    _save_node(store, "node_warn", "warn-host")
    _record_heartbeat(store, "node_ok")
    _record_heartbeat(store, "node_warn", outcome="warn")
    runner.invoke(app, ["monitor", "run-once", "--db", str(db)])

    result = runner.invoke(app, ["monitor", "report", "--db", str(db)])

    assert result.exit_code == 0
    assert "monitor report: nodes=2 ok=1 warn=1 critical=0" in result.stdout
    assert "node_ok ok reason=fresh_heartbeat" in result.stdout
    assert "node_warn warn reason=heartbeat_warn" in result.stdout


def test_monitor_run_once_marks_stale_heartbeat_warn(tmp_path):
    runner = CliRunner()
    db = tmp_path / "hmn.db"
    store = SQLiteStore(db)
    _save_node(store, "node_stale")
    _record_heartbeat(store, "node_stale")
    now = datetime.now(timezone.utc) + timedelta(seconds=700)

    result = runner.invoke(app, ["monitor", "run-once", "--now", now.isoformat(), "--db", str(db)])

    assert result.exit_code == 1
    snapshot = SQLiteStore(db).latest_monitor_snapshot("node_stale")
    assert snapshot is not None
    assert snapshot.health == "warn"
    assert snapshot.reason == "stale_heartbeat"
