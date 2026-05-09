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
    assert "systemctl enable --now hermes-managed-network.service" in script


def test_master_installer_has_linux_dependency_detection():
    script = (ROOT / "scripts/install-master.sh").read_text()

    assert "apt-get install -y python3 python3-venv python3-pip curl" in script
    assert "dnf install -y python3 python3-pip curl" in script
    assert "yum install -y python3 python3-pip curl" in script
    assert "apk add --no-cache python3 py3-pip curl" in script
