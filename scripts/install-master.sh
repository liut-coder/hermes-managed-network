#!/usr/bin/env bash
set -euo pipefail

HMN_USER="${HMN_USER:-hermes}"
HMN_HOME="${HMN_HOME:-/opt/hermes-managed-network}"
HMN_DB="${HMN_DB:-/var/lib/hermes-managed-network/control-plane.db}"
HMN_HOST="${HMN_HOST:-127.0.0.1}"
HMN_PORT="${HMN_PORT:-8765}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "install-master.sh must run as root" >&2
    exit 1
  fi
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

ensure_user() {
  if ! id "$HMN_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$HMN_HOME" --create-home --shell /usr/sbin/nologin "$HMN_USER"
  fi
}

install_package() {
  install -d -m 0755 "$HMN_HOME"
  chown "$HMN_USER:$HMN_USER" "$HMN_HOME"
  if [ ! -d "$HMN_HOME/.venv" ]; then
    "$PYTHON_BIN" -m venv "$HMN_HOME/.venv"
  fi
  "$HMN_HOME/.venv/bin/python" -m pip install --upgrade pip
  "$HMN_HOME/.venv/bin/python" -m pip install "${HMN_PACKAGE:-hermes-managed-network}"
}

write_service() {
  install -d -m 0750 -o "$HMN_USER" -g "$HMN_USER" "$(dirname "$HMN_DB")"
  cat >/etc/systemd/system/hermes-managed-network.service <<EOF
[Unit]
Description=Hermes Managed Network control plane
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${HMN_USER}
Group=${HMN_USER}
Environment=HMN_DB=${HMN_DB}
Environment=HMN_HOST=${HMN_HOST}
Environment=HMN_PORT=${HMN_PORT}
ExecStart=${HMN_HOME}/.venv/bin/python -m hermes_managed_network.server
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$(dirname "$HMN_DB")

[Install]
WantedBy=multi-user.target
EOF
}

main() {
  need_root
  need_command "$PYTHON_BIN"
  ensure_user
  install_package
  write_service
  systemctl daemon-reload
  systemctl enable --now hermes-managed-network.service
  systemctl --no-pager --full status hermes-managed-network.service || true
}

main "$@"
