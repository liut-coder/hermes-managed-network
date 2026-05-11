from __future__ import annotations

from pathlib import Path


def test_local_e2e_smoke_script_covers_real_deploy_closure():
    script_path = Path("scripts/smoke-local-e2e.sh")

    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "HMN_DB=\"$DB\" HMN_HOST=127.0.0.1 HMN_PORT=\"$PORT\"" in script
    assert ".venv/bin/hmn-server" in script
    assert "/healthz" in script
    assert "/api/v1/version" in script
    assert "hmn token create" in script
    assert "/api/v1/join" in script
    assert "hmn node confirm" in script
    assert "hmn task run" in script
    assert "HMN_ENV_FILE" in script
    assert "scripts/worker.sh" in script
    assert "hmn node worker-status" in script
    assert "hmn docs generate" in script
    assert "print('TASK_STATUS', task.status)" in script
    assert "print('TASK_EXIT', task.exit_code)" in script
    assert "execution disabled" in script
    assert "kill \"$SERVER_PID\"" in script
