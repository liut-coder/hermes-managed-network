#!/usr/bin/env bash
set -euo pipefail

HMN_USER="${HMN_USER:-hermes}"
HMN_HOME="${HMN_HOME:-/opt/hermes-managed-network}"
HMN_DB="${HMN_DB:-/var/lib/hermes-managed-network/control-plane.db}"
HMN_HOST="${HMN_HOST:-127.0.0.1}"
HMN_PORT="${HMN_PORT:-8765}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HMN_PACKAGE="${HMN_PACKAGE:-hermes-managed-network}"

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

install_dependencies() {
  local missing=()
  for cmd in "$PYTHON_BIN" curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if command -v "$PYTHON_BIN" >/dev/null 2>&1 && ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    missing+=("python3-venv")
  fi
  if [ "${#missing[@]}" -eq 0 ]; then
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3 python3-venv python3-pip curl
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip curl
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip curl
  else
    echo "missing dependencies: ${missing[*]}" >&2
    echo "please install python3, venv/pip and curl manually" >&2
    exit 1
  fi
}

check_platform() {
  if ! command -v systemctl >/dev/null 2>&1 || [ ! -d /run/systemd/system ]; then
    echo "systemd is required by this installer" >&2
    echo "OpenRC/procd/launchd templates will be added separately" >&2
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
  "$HMN_HOME/.venv/bin/python" -m pip install --upgrade --force-reinstall --no-cache-dir "$HMN_PACKAGE"
}

verify_install() {
  "$HMN_HOME/.venv/bin/hmn" version >/dev/null
  "$HMN_HOME/.venv/bin/python" - <<'PY'
from hermes_managed_network.api import create_app
app = create_app('/tmp/hmn-install-smoke.db')
assert app.title == 'Hermes Managed Network'
PY
}

write_env() {
  install -d -m 0750 -o "$HMN_USER" -g "$HMN_USER" /etc/hermes-managed-network
  cat >/etc/hermes-managed-network/master.env <<EOF
HMN_DB=${HMN_DB}
HMN_HOST=${HMN_HOST}
HMN_PORT=${HMN_PORT}
EOF
  chmod 0640 /etc/hermes-managed-network/master.env
  chown "$HMN_USER:$HMN_USER" /etc/hermes-managed-network/master.env
}

install_cli_links() {
  ln -sf "$HMN_HOME/.venv/bin/hmn" /usr/local/bin/hmn
  ln -sf "$HMN_HOME/.venv/bin/hmn-server" /usr/local/bin/hmn-server
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
EnvironmentFile=/etc/hermes-managed-network/master.env
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
  install_dependencies
  need_command "$PYTHON_BIN"
  check_platform
  ensure_user
  install_package
  verify_install
  write_env
  install_cli_links
  write_service
  systemctl daemon-reload
  systemctl enable --now hermes-managed-network.service
  systemctl --no-pager --full status hermes-managed-network.service || true
}

main "$@"
