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


def test_remote_e2e_smoke_script_documents_repeatable_p1_gate():
    script_path = Path("scripts/smoke-remote-e2e.sh")

    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "HMN_MASTER_HOST" in script
    assert "HMN_WORKER_HOST" in script
    assert "HMN_SSH_KEY" in script
    assert "HMN_REMOTE_USER" in script
    assert "HMN_PUBLIC_URL" in script
    assert "scripts/install-master.sh" in script
    assert "systemctl is-active --quiet hermes-managed-network.service" in script
    assert "/healthz" in script
    assert "/api/v1/version" in script
    assert "hmn token create" in script
    assert "hmn node confirm" in script
    assert "hmn node install-heartbeat" in script
    assert "systemctl is-active --quiet hermes-managed-network-heartbeat.timer" in script
    assert "hmn node worker-status" in script
    assert "hmn task run" in script
    assert "hmn task ssh-run-next" in script
    assert "execution disabled" in script
    assert "hmn docs generate" in script
    assert "hmn doctor" in script
    assert "HMN_ENABLE_EXEC=0" in script
    assert "?token=" not in script
    assert "BOT_TOKEN" not in script
    assert "example.invalid" in Path("docs/deployment.md").read_text(encoding="utf-8")
    assert "scripts/smoke-remote-e2e.sh" in Path("docs/roadmap.md").read_text(encoding="utf-8")


def test_telegram_approval_smoke_script_documents_real_bot_callback_gate():
    script_path = Path("scripts/smoke-telegram-approval.sh")

    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "HMN_APPROVAL_GATEWAY_TOKEN" in script
    assert "HMN_TELEGRAM_BOT_TOKEN" in script
    assert "HMN_APPROVAL_GATEWAY_TARGET" in script
    assert "HMN_TELEGRAM_CHAT_ID" in script
    assert "hmn-server" in script
    assert "/healthz" in script
    assert "/api/v1/version" in script
    assert "hmn task run" in script
    assert "--risk high" in script
    assert "approval-gateway poll-once --client telegram" in script
    assert "sent=1" in script
    assert "callbacks=" in script
    assert "approved=1" in script
    assert "hmn approval list" in script
    assert "hmn task list" in script
    assert "getUpdates" in script
    assert "answerCallbackQuery" in script
    assert "editMessageReplyMarkup" in script
    assert "BOT_TOKEN=" not in script
    assert "<bot-token>" in Path("docs/deployment.md").read_text(encoding="utf-8")
    assert "scripts/smoke-telegram-approval.sh" in Path("docs/roadmap.md").read_text(encoding="utf-8")
