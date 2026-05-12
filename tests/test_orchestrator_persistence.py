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


def test_report_is_short_chinese_summary(tmp_path):
    service = OrchestratorService(SQLiteStore(tmp_path / "hmn.db"))
    service.enqueue(title="巡检", scope="ops", risk="low", priority=1)

    assert service.report().startswith("队列 1｜worker 0｜最近：")
