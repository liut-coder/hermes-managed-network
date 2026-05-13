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


def test_branch_backlog_marks_useful_ops_summary_branch_absorbed(tmp_path):
    repo = tmp_path / "repo"
    run = __import__("subprocess").run
    run(["git", "init", "-b", "feat/v1-1-useful-ops-mvp", str(repo)], check=True, capture_output=True, text=True)
    run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
    run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n")
    run(["git", "add", "README.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "-b", "feat/useful-ops-mvp", "HEAD"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "summary.md").write_text("managed ops summary\n")
    run(["git", "add", "summary.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "summary"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "feat/v1-1-useful-ops-mvp"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "mainline.md").write_text("newer mainline\n")
    run(["git", "add", "mainline.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "mainline"], cwd=repo, check=True, capture_output=True, text=True)

    backlog = OrchestratorService(SQLiteStore(tmp_path / "hmn.db")).branch_backlog(
        repo_path=repo, base="feat/v1-1-useful-ops-mvp"
    )

    merged = {item["branch"]: item for item in backlog["buckets"]["merged"]}
    assert "feat/useful-ops-mvp" in merged
    assert "summary doc" in merged["feat/useful-ops-mvp"]["reason"]
    assert "feat/useful-ops-mvp" in backlog["cleanup"]
    assert backlog["wip_count"] == 0


def test_prepare_agent_worktree_creates_isolated_worktrees_and_persists_snapshot(tmp_path):
    repo = tmp_path / "repo"
    run = __import__("subprocess").run
    run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
    run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)

    db = tmp_path / "hmn.db"
    service = OrchestratorService(SQLiteStore(db))
    first = service.enqueue(title="agent A", scope="code", risk="low", priority=10)
    second = service.enqueue(title="agent B", scope="code", risk="low", priority=9)

    wt_a = service.prepare_agent_worktree(
        task_id=first,
        worker_id="agent-a",
        repo_path=repo,
        base="main",
        worktree_root=tmp_path / "worktrees",
    )
    wt_b = service.prepare_agent_worktree(
        task_id=second,
        worker_id="agent-b",
        repo_path=repo,
        base="main",
        worktree_root=tmp_path / "worktrees",
    )
    snapshot = OrchestratorService(SQLiteStore(db)).snapshot()

    assert wt_a["path"] != wt_b["path"]
    assert wt_a["branch"] != wt_b["branch"]
    assert wt_a["status"] == "prepared"
    assert (tmp_path / "worktrees" / first).is_dir()
    assert (tmp_path / "worktrees" / second).is_dir()
    assert snapshot["worktrees"] == [
        {
            "task_id": first,
            "worker_id": "agent-a",
            "branch": wt_a["branch"],
            "path": wt_a["path"],
            "base": "main",
            "status": "prepared",
        },
        {
            "task_id": second,
            "worker_id": "agent-b",
            "branch": wt_b["branch"],
            "path": wt_b["path"],
            "base": "main",
            "status": "prepared",
        },
    ]


def test_merge_queue_classifies_worktree_branches_without_merging_main(tmp_path):
    repo = tmp_path / "repo"
    run = __import__("subprocess").run
    run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
    run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    run(["git", "add", "README.md", "shared.txt"], cwd=repo, check=True)
    run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)

    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    ready_task = service.enqueue(title="ready", scope="code", risk="low", priority=10)
    conflict_task = service.enqueue(title="conflict", scope="code", risk="low", priority=9)
    ready = service.prepare_agent_worktree(
        task_id=ready_task,
        worker_id="agent-a",
        repo_path=repo,
        base="main",
        worktree_root=tmp_path / "worktrees",
    )
    conflict = service.prepare_agent_worktree(
        task_id=conflict_task,
        worker_id="agent-b",
        repo_path=repo,
        base="main",
        worktree_root=tmp_path / "worktrees",
    )

    ready_path = tmp_path / "worktrees" / ready_task
    (ready_path / "ready.txt").write_text("ready\n", encoding="utf-8")
    run(["git", "add", "ready.txt"], cwd=ready_path, check=True)
    run(["git", "commit", "-m", "ready"], cwd=ready_path, check=True, capture_output=True, text=True)

    conflict_path = tmp_path / "worktrees" / conflict_task
    (conflict_path / "shared.txt").write_text("agent edit\n", encoding="utf-8")
    run(["git", "add", "shared.txt"], cwd=conflict_path, check=True)
    run(["git", "commit", "-m", "agent edit"], cwd=conflict_path, check=True, capture_output=True, text=True)

    (repo / "shared.txt").write_text("main edit\n", encoding="utf-8")
    run(["git", "add", "shared.txt"], cwd=repo, check=True)
    run(["git", "commit", "-m", "main edit"], cwd=repo, check=True, capture_output=True, text=True)

    queue = service.merge_queue(repo_path=repo, base="main")

    assert queue["base"] == "main"
    assert {item["task_id"] for item in queue["merge_ready"]} == {ready_task}
    assert {item["task_id"] for item in queue["conflict"]} == {conflict_task}
    assert queue["merge_ready"][0]["branch"] == ready["branch"]
    assert queue["conflict"][0]["branch"] == conflict["branch"]
    assert run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip() == "main"


def test_merge_queue_filters_worktrees_by_repo_and_base(tmp_path):
    run = __import__("subprocess").run
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    for repo in (repo_a, repo_b):
        run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
        run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
        run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=repo, check=True)
        run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    run(["git", "checkout", "-b", "develop"], cwd=repo_a, check=True, capture_output=True, text=True)
    (repo_a / "develop.txt").write_text("develop\n", encoding="utf-8")
    run(["git", "add", "develop.txt"], cwd=repo_a, check=True)
    run(["git", "commit", "-m", "develop"], cwd=repo_a, check=True, capture_output=True, text=True)
    run(["git", "checkout", "main"], cwd=repo_a, check=True, capture_output=True, text=True)

    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    task_a = service.enqueue(title="repo a", scope="code", risk="low", priority=10)
    task_b = service.enqueue(title="repo b", scope="code", risk="low", priority=9)
    task_c = service.enqueue(title="repo a develop", scope="code", risk="low", priority=8)
    service.prepare_agent_worktree(task_id=task_a, worker_id="agent-a", repo_path=repo_a, base="main", worktree_root=tmp_path / "worktrees-a")
    service.prepare_agent_worktree(task_id=task_b, worker_id="agent-b", repo_path=repo_b, base="main", worktree_root=tmp_path / "worktrees-b")
    service.prepare_agent_worktree(task_id=task_c, worker_id="agent-c", repo_path=repo_a, base="develop", worktree_root=tmp_path / "worktrees-c")

    queue = service.merge_queue(repo_path=repo_a, base="main")

    assert {item["task_id"] for item in queue["merge_ready"]} == {task_a}
    assert queue["missing"] == []


def test_prepare_agent_worktree_rejects_preexisting_non_worktree_path(tmp_path):
    repo = tmp_path / "repo"
    run = __import__("subprocess").run
    run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
    run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)

    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    task_id = service.enqueue(title="bad path", scope="code", risk="low", priority=10)
    bad_path = tmp_path / "worktrees" / task_id
    bad_path.mkdir(parents=True)
    (bad_path / "not-a-worktree.txt").write_text("oops\n", encoding="utf-8")

    try:
        service.prepare_agent_worktree(
            task_id=task_id,
            worker_id="agent-a",
            repo_path=repo,
            base="main",
            worktree_root=tmp_path / "worktrees",
        )
    except RuntimeError as exc:
        assert "not branch" in str(exc) or "not registered" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for preexisting non-worktree path")


def test_prepare_agent_worktree_rejects_path_traversal_task_id(tmp_path):
    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))

    try:
        service.prepare_agent_worktree(
            task_id="../escape",
            worker_id="agent-a",
            repo_path=tmp_path,
            base="main",
            worktree_root=tmp_path / "worktrees",
        )
    except ValueError as exc:
        assert "path separators" in str(exc)
    else:
        raise AssertionError("expected ValueError for unsafe task_id")


def test_merge_queue_marks_deleted_worktree_missing(tmp_path):
    repo = tmp_path / "repo"
    run = __import__("subprocess").run
    run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    run(["git", "config", "user.email", "hmn@example.invalid"], cwd=repo, check=True)
    run(["git", "config", "user.name", "HMN Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repo, check=True)
    run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)

    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    task_id = service.enqueue(title="missing", scope="code", risk="low", priority=10)
    worktree = service.prepare_agent_worktree(task_id=task_id, worker_id="agent-a", repo_path=repo, base="main", worktree_root=tmp_path / "worktrees")
    run(["git", "worktree", "remove", "--force", worktree["path"]], cwd=repo, check=True, capture_output=True, text=True)

    queue = service.merge_queue(repo_path=repo, base="main")

    assert {item["task_id"] for item in queue["missing"]} == {task_id}
