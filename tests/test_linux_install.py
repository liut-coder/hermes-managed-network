from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_entry_uses_raw_master_installer_and_git_package():
    script = (ROOT / "install.sh").read_text()

    assert "HMN_PACKAGE" in script
    assert "git+https://github.com/liut-coder/hermes-managed-network.git@feat/control-plane-mvp" in script
    assert "scripts/install-master.sh" in script
    assert "HMN_HOST=\"${HMN_HOST:-0.0.0.0}\"" in script


def test_master_installer_writes_systemd_service_and_cli_links():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "command -v systemctl" in script
    assert "verify_install" in script
    assert "hmn\" version >/dev/null" in script
    assert "[Service]" in script
    assert "EnvironmentFile=/etc/hermes-managed-network/master.env" in script
    assert "ExecStart=${HMN_HOME}/.venv/bin/python -m hermes_managed_network.server" in script
    assert "ln -sf \"$HMN_HOME/.venv/bin/hmn\" /usr/local/bin/hmn" in script
    assert "ln -sf \"$HMN_HOME/.venv/bin/hmn-server\" /usr/local/bin/hmn-server" in script
    assert "systemctl enable hermes-managed-network.service" in script
    assert "systemctl restart hermes-managed-network.service" in script


def test_master_installer_has_linux_dependency_detection():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "apt-get install -y python3 python3-venv python3-pip curl" in script
    assert "dnf install -y python3 python3-pip curl" in script
    assert "yum install -y python3 python3-pip curl" in script
    assert "apk add --no-cache python3 py3-pip curl" in script


def test_master_installer_prompts_for_optional_values_with_defaults():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "HMN_ASSUME_YES" in script
    assert "prompt_default HMN_HOST" in script
    assert "prompt_default HMN_PORT" in script
    assert "prompt_default HMN_USER" in script
    assert "prompt_default HMN_HOME" in script
    assert "prompt_default HMN_DB" in script
    assert "交互配置" in script
    assert "当前默认值" in script


def test_master_installer_detects_existing_version_and_policy():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "detect_existing_version" in script
    assert "VERSION_POLICY" in script
    assert "版本一致" in script
    assert "版本不同" in script
    assert "HMN_UPGRADE_POLICY" in script
    assert "backup_existing_state" in script
    assert "HMN_BACKUP_DIR" in script


def test_master_installer_runs_post_deploy_self_check():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "self_check" in script
    assert "systemctl is-active --quiet hermes-managed-network.service" in script
    assert "curl -fsS \"http://127.0.0.1:${HMN_PORT}/healthz\"" in script
    assert "curl -fsS \"http://127.0.0.1:${HMN_PORT}/api/v1/version\"" in script
    assert "journalctl -u hermes-managed-network.service" in script


def test_master_installer_can_enable_approval_gateway_service_with_telegram_compatibility():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "HMN_ENABLE_TELEGRAM" in script
    assert "HMN_APPROVAL_GATEWAY_CLIENT" in script
    assert "HMN_APPROVAL_GATEWAY_TARGET" in script
    assert "HMN_APPROVAL_GATEWAY_TOKEN" in script
    assert "approval-gateway.env" in script
    assert "hermes-managed-network-approval-gateway.service" in script
    assert "ExecStart=/usr/local/bin/hmn approval-gateway run --client" in script
    assert "systemctl enable hermes-managed-network-approval-gateway.service" in script
    assert "systemctl restart hermes-managed-network-approval-gateway.service" in script

    # Backward compatibility for existing Telegram gateway deployments/scripts.
    assert "HMN_TELEGRAM_CHAT_ID" in script
    assert "HMN_TELEGRAM_BOT_TOKEN" in script
    assert "telegram-gateway.env" in script
    assert "hermes-managed-network-telegram-gateway.service" in script
    assert "ExecStart=/usr/local/bin/hmn telegram-gateway run --interval" in script


def test_master_installer_supports_headscale_bundled_and_external_modes():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "HMN_HEADSCALE_MODE" in script
    assert "bundled" in script
    assert "external" in script
    assert "HMN_HEADSCALE_URL" in script
    assert "HMN_HEADSCALE_API_KEY" in script
    assert "HMN_HEADSCALE_NAMESPACE" in script
    assert "headscale.env" in script
    assert "/etc/hermes-managed-network/config.yaml" in script
    assert "api_key_env: HMN_HEADSCALE_API_KEY" in script
    assert "install_headscale_bundled" in script
    assert "configure_headscale_provider" in script
