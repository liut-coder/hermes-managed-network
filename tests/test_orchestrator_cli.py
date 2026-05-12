from typer.testing import CliRunner

from hermes_managed_network.cli import app


class FakeOrchestratorService:
    last_instance = None

    def __init__(self, store):
        self.store = store
        FakeOrchestratorService.last_instance = self
        self.enqueued = None

    def enqueue(self, *, title, scope, risk, priority, worker_hint="", source="cli"):
        self.enqueued = {
            "title": title,
            "scope": scope,
            "risk": risk,
            "priority": priority,
            "worker_hint": worker_hint,
            "source": source,
        }
        return "orch_123"

    def snapshot(self):
        return {
            "queue": [{"task_id": "orch_123", "title": "巡检任务", "status": "queued", "priority": 7}],
            "workers": [{"worker_id": "miskrobot", "status": "online", "transport": "bridge"}],
            "assignments": [{"task_id": "orch_123", "worker_id": "miskrobot", "status": "leased"}],
            "reports": [{"task_id": "orch_123", "summary": "已派发", "status": "ok"}],
        }

    def tick(self):
        return {
            "dispatched": [{"task_id": "orch_123", "worker_id": "miskrobot"}],
            "blocked": [{"task_id": "orch_456", "reason": "等待审批"}],
            "next": "等待 worker 回报",
        }

    def report(self):
        return "队列 1｜worker 1｜最近：已派发"


def test_orchestrator_enqueue_passes_cli_options_to_service(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_managed_network.cli.OrchestratorService", FakeOrchestratorService)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "orchestrator",
            "enqueue",
            "--db",
            str(tmp_path / "hmn.db"),
            "--title",
            "实现 CLI 骨架",
            "--scope",
            "O2",
            "--risk",
            "low",
            "--priority",
            "8",
            "--worker",
            "secondary",
        ],
    )

    assert result.exit_code == 0
    assert "已入队" in result.stdout
    assert "orch_123" in result.stdout
    assert FakeOrchestratorService.last_instance.enqueued == {
        "title": "实现 CLI 骨架",
        "scope": "O2",
        "risk": "low",
        "priority": 8,
        "worker_hint": "secondary",
        "source": "cli",
    }


def test_orchestrator_status_tick_report_render_short_chinese_output(monkeypatch, tmp_path):
    monkeypatch.setattr("hermes_managed_network.cli.OrchestratorService", FakeOrchestratorService)
    runner = CliRunner()
    db_args = ["--db", str(tmp_path / "hmn.db")]

    status = runner.invoke(app, ["orchestrator", "status", *db_args])
    tick = runner.invoke(app, ["orchestrator", "tick", *db_args])
    report = runner.invoke(app, ["orchestrator", "report", *db_args])

    assert status.exit_code == 0
    assert "队列" in status.stdout
    assert "巡检任务" in status.stdout
    assert "worker" in status.stdout
    assert "miskrobot" in status.stdout
    assert "assignment" in status.stdout
    assert "已派发" in status.stdout

    assert tick.exit_code == 0
    assert "已分发" in tick.stdout
    assert "orch_123 -> miskrobot" in tick.stdout
    assert "阻塞" in tick.stdout
    assert "等待审批" in tick.stdout
    assert "下一步：等待 worker 回报" in tick.stdout

    assert report.exit_code == 0
    assert report.stdout.strip() == "队列 1｜worker 1｜最近：已派发"


def test_top_help_and_plain_menu_show_orchestrator_commands():
    runner = CliRunner()

    help_result = runner.invoke(app, ["--help"])
    menu_result = runner.invoke(app, ["menu", "--plain"])

    assert help_result.exit_code == 0
    assert "hmn orchestrator enqueue" in help_result.stdout
    assert "hmn orchestrator status" in help_result.stdout
    assert "hmn orchestrator tick" in help_result.stdout
    assert "hmn orchestrator report" in help_result.stdout
    assert menu_result.exit_code == 0
    assert "hmn orchestrator enqueue" in menu_result.stdout
    assert "hmn orchestrator status" in menu_result.stdout
    assert "hmn orchestrator tick" in menu_result.stdout
    assert "hmn orchestrator report" in menu_result.stdout
