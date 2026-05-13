from hermes_managed_network.orchestrator import OrchestratorService
from hermes_managed_network.storage import SQLiteStore


def test_enqueue_persists_queue_across_service_instances(tmp_path):
    db = tmp_path / "hmn.db"
    task_id = OrchestratorService(SQLiteStore(db)).enqueue(
        title="巡检服务",
        scope="ops",
        risk="low",
        priority=7,
        worker_hint="bridge-1",
    )

    snapshot = OrchestratorService(SQLiteStore(db)).snapshot()

    assert snapshot["queue"] == [
        {
            "task_id": task_id,
            "title": "巡检服务",
            "scope": "ops",
            "risk": "low",
            "priority": 7,
            "status": "queued",
            "worker_hint": "bridge-1",
            "source": "cli",
        }
    ]


def test_register_update_worker_is_visible_in_snapshot(tmp_path):
    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))

    service.register_worker(worker_id="bridge-1", transport="bridge", status="online", labels=["code"])
    service.update_worker(worker_id="bridge-1", status="offline")

    assert service.snapshot()["workers"] == [
        {"worker_id": "bridge-1", "transport": "bridge", "status": "offline", "labels": ["code"]}
    ]


def test_tick_blocks_when_no_online_bridge_worker(tmp_path):
    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    task_id = service.enqueue(title="整理 backlog", scope="docs", risk="low", priority=3)

    result = service.tick()

    assert result["dispatched"] == []
    assert result["blocked"] == [{"task_id": task_id, "reason": "暂无 online bridge worker"}]
    assert "等待 worker" in result["next"]
    assert service.snapshot()["queue"][0]["status"] == "queued"


def test_tick_assigns_queued_low_risk_task_to_online_bridge_worker(tmp_path):
    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    low = service.enqueue(title="跑测试", scope="code", risk="low", priority=10)
    service.enqueue(title="高危发布", scope="prod", risk="high", priority=20)
    service.register_worker(worker_id="bridge-1", transport="bridge", status="online")

    result = service.tick()
    snapshot = service.snapshot()

    assert result["dispatched"] == [{"task_id": low, "worker_id": "bridge-1"}]
    assert result["blocked"] == []
    leased_task = next(item for item in snapshot["queue"] if item["task_id"] == low)
    assert leased_task["status"] == "leased"
    assert snapshot["assignments"] == [{"task_id": low, "worker_id": "bridge-1", "status": "leased"}]
    assert snapshot["reports"][0]["summary"] == "已分发给 bridge-1"


def test_tick_records_bridge_dispatch_failure_and_retries_without_leasing(tmp_path):
    class BrokenBridge:
        def __init__(self):
            self.calls = 0

        def dispatch(self, *, task, worker):
            self.calls += 1
            raise RuntimeError("No route to host")

    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    task_id = service.enqueue(title="分发到备用 bot", scope="code", risk="low", priority=5)
    service.register_worker(worker_id="miskrobot", transport="bridge", status="online")
    bridge = BrokenBridge()

    result = service.tick(bridge_adapter=bridge, max_retries=3)
    snapshot = service.snapshot()

    assert bridge.calls == 1
    assert result["dispatched"] == []
    assert result["blocked"] == [{"task_id": task_id, "reason": "bridge dispatch failed: No route to host", "attempt": 1}]
    assert result["next"] == "等待下一轮重试"
    assert snapshot["queue"][0]["status"] == "queued"
    assert snapshot["queue"][0]["attempts"] == 1
    assert snapshot["reports"][0]["summary"] == "bridge dispatch failed: No route to host"


def test_tick_pauses_task_after_repeated_bridge_failures(tmp_path):
    class BrokenBridge:
        def dispatch(self, *, task, worker):
            raise TimeoutError("timeout")

    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    task_id = service.enqueue(title="连续失败任务", scope="code", risk="low", priority=5)
    service.register_worker(worker_id="miskrobot", transport="bridge", status="online")

    for _ in range(3):
        result = service.tick(bridge_adapter=BrokenBridge(), max_retries=3)

    snapshot = service.snapshot()
    task = snapshot["queue"][0]
    assert task["task_id"] == task_id
    assert task["status"] == "paused"
    assert task["attempts"] == 3
    assert result["blocked"] == [{"task_id": task_id, "reason": "连续 3 次分发失败，已暂停"}]
    assert result["next"] == "等待主控检查 worker/bridge 状态"


def test_tick_requeues_expired_lease_before_dispatch(tmp_path):
    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    task_id = service.enqueue(title="旧 lease", scope="code", risk="low", priority=5)
    service.register_worker(worker_id="old-worker", transport="bridge", status="online")
    first = service.tick()
    assert first["dispatched"] == [{"task_id": task_id, "worker_id": "old-worker"}]

    with SQLiteStore(tmp_path / "hmn.db").connect() as conn:
        conn.execute("UPDATE orchestrator_assignments SET leased_at = '2000-01-01T00:00:00+00:00' WHERE task_id = ?", (task_id,))
        conn.execute("UPDATE orchestrator_workers SET status = 'offline' WHERE worker_id = 'old-worker'")
    service.register_worker(worker_id="new-worker", transport="bridge", status="online")

    second = service.tick(lease_timeout_seconds=1)
    snapshot = service.snapshot()

    assert second["dispatched"] == [{"task_id": task_id, "worker_id": "new-worker"}]
    assert snapshot["reports"][0]["summary"] == "已分发给 new-worker"


def test_report_is_short_chinese_summary(tmp_path):
    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    service.enqueue(title="巡检", scope="ops", risk="low", priority=1)

    assert service.report().startswith("队列 1｜worker 0｜最近：")


def test_branch_backlog_classifies_absorbed_and_unmerged_branches(tmp_path):
    repo = tmp_path / "repo"
    run = __import__("subprocess").run
    run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
    run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n")
    run(["git", "add", "README.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "-b", "feature/absorbed"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "absorbed.txt").write_text("done\n")
    run(["git", "add", "absorbed.txt"], cwd=repo, check=True)
    run(["git", "commit", "-m", "absorbed"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "merge", "--ff-only", "feature/absorbed"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "-b", "feature/open", "HEAD~1"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "open.txt").write_text("open\n")
    run(["git", "add", "open.txt"], cwd=repo, check=True)
    run(["git", "commit", "-m", "open"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True, text=True)

    backlog = OrchestratorService(SQLiteStore(tmp_path / "hmn.db")).branch_backlog(repo_path=repo, base="main")

    assert backlog["total"] == 3
    assert {item["branch"] for item in backlog["buckets"]["merged"]} >= {"feature/absorbed", "main"}
    assert [item["branch"] for item in backlog["buckets"]["needs-review"]] == ["feature/open"]
    assert backlog["cleanup"] == ["feature/absorbed"]
    assert backlog["wip_count"] == 1
    assert backlog["wip_limit"] == 3


def test_branch_backlog_marks_known_task_absorbed_stale_base_branches(tmp_path):
    repo = tmp_path / "repo"
    run = __import__("subprocess").run
    run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
    run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n")
    run(["git", "add", "README.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "-b", "feat/monitor-closed-loop", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "stale.txt").write_text("old task\n")
    run(["git", "add", "stale.txt"], cwd=repo, check=True)
    run(["git", "commit", "-m", "old task"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "mainline.txt").write_text("newer mainline\n")
    run(["git", "add", "mainline.txt"], cwd=repo, check=True)
    run(["git", "commit", "-m", "mainline"], cwd=repo, check=True, capture_output=True, text=True)

    backlog = OrchestratorService(SQLiteStore(tmp_path / "hmn.db")).branch_backlog(repo_path=repo, base="main")

    merged = {item["branch"]: item for item in backlog["buckets"]["merged"]}
    assert "feat/monitor-closed-loop" in merged
    assert "stale-base cleanup candidate" in merged["feat/monitor-closed-loop"]["reason"]
    assert "feat/monitor-closed-loop" in backlog["cleanup"]
    assert backlog["wip_count"] == 0
