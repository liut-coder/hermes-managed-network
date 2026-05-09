from __future__ import annotations

from pathlib import Path


def test_worker_script_asset_exists_and_has_task_loop():
    script = Path("src/hermes_managed_network/assets/worker.sh").read_text()
    assert "/api/v1/nodes/${HERMES_NODE_ID}/tasks/next" in script
    assert "/api/v1/tasks/${task_id}/result" in script
    assert "HMN_ENABLE_EXEC" in script
