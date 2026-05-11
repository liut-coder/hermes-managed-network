#!/usr/bin/env bash
set -euo pipefail

HMN_USER="${HMN_USER:-hermes}"
HMN_HOME="${HMN_HOME:-/opt/hermes-managed-network}"
HMN_DB="${HMN_DB:-/var/lib/hermes-managed-network/control-plane.db}"
HMN_HOST="${HMN_HOST:-127.0.0.1}"
HMN_PORT="${HMN_PORT:-8765}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
HMN_PACKAGE="${HMN_PACKAGE:-hermes-managed-network}"
HMN_ASSUME_YES="${HMN_ASSUME_YES:-0}"
HMN_UPGRADE_POLICY="${HMN_UPGRADE_POLICY:-prompt}"
HMN_BACKUP_DIR="${HMN_BACKUP_DIR:-/var/backups/hermes-managed-network}"
HMN_LAST_BACKUP_STAMP="${HMN_LAST_BACKUP_STAMP:-}"
HMN_ROLLBACK_HINT="${HMN_ROLLBACK_HINT:-restore DB/env from HMN_BACKUP_DIR using HMN_LAST_BACKUP_STAMP, then restart services and run hmn doctor}"
HMN_PUBLIC_URL="${HMN_PUBLIC_URL:-http://127.0.0.1:${HMN_PORT}}"
HMN_ENABLE_TELEGRAM="${HMN_ENABLE_TELEGRAM:-0}"
HMN_APPROVAL_GATEWAY_CLIENT="${HMN_APPROVAL_GATEWAY_CLIENT:-telegram}"
HMN_APPROVAL_GATEWAY_TARGET="${HMN_APPROVAL_GATEWAY_TARGET:-${HMN_TELEGRAM_CHAT_ID:-}}"
HMN_APPROVAL_GATEWAY_TOKEN="${HMN_APPROVAL_GATEWAY_TOKEN:-${HMN_TELEGRAM_BOT_TOKEN:-}}"
HMN_APPROVAL_GATEWAY_INTERVAL="${HMN_APPROVAL_GATEWAY_INTERVAL:-${HMN_TELEGRAM_INTERVAL:-10}}"
HMN_TELEGRAM_CHAT_ID="${HMN_TELEGRAM_CHAT_ID:-$HMN_APPROVAL_GATEWAY_TARGET}"
HMN_TELEGRAM_BOT_TOKEN="${HMN_TELEGRAM_BOT_TOKEN:-$HMN_APPROVAL_GATEWAY_TOKEN}"
HMN_TELEGRAM_INTERVAL="${HMN_TELEGRAM_INTERVAL:-$HMN_APPROVAL_GATEWAY_INTERVAL}"
HMN_HEADSCALE_MODE="${HMN_HEADSCALE_MODE:-bundled}"
HMN_HEADSCALE_URL="${HMN_HEADSCALE_URL:-}"
HMN_HEADSCALE_API_KEY="${HMN_HEADSCALE_API_KEY:-}"
HMN_HEADSCALE_NAMESPACE="${HMN_HEADSCALE_NAMESPACE:-misk}"
HMN_HEADSCALE_LISTEN_ADDR="${HMN_HEADSCALE_LISTEN_ADDR:-127.0.0.1:8080}"
CURRENT_VERSION="unknown"
EXISTING_VERSION="not-installed"
VERSION_POLICY="install"

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

prompt_default() {
  local var_name="$1"
  local label="$2"
  local default_value="${!var_name}"
  local value=""
  if [ "$HMN_ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    printf -v "$var_name" '%s' "$default_value"
    return
  fi
  read -r -p "${label} [${default_value}]: " value
  if [ -n "$value" ]; then
    printf -v "$var_name" '%s' "$value"
  fi
}

prompt_secret() {
  local var_name="$1"
  local label="$2"
  local value=""
  if [ "$HMN_ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    return
  fi
  if [ -n "${!var_name}" ]; then
    return
  fi
  read -r -s -p "${label} [留空跳过]: " value
  echo
  if [ -n "$value" ]; then
    printf -v "$var_name" '%s' "$value"
  fi
}

prompt_yes_no_default() {
  local var_name="$1"
  local label="$2"
  local default_value="${!var_name}"
  local value=""
  if [ "$HMN_ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    return
  fi
  read -r -p "${label} [${default_value}]: " value
  case "$value" in
    y|Y|yes|YES|1) printf -v "$var_name" '%s' "1" ;;
    n|N|no|NO|0) printf -v "$var_name" '%s' "0" ;;
  esac
}

interactive_config() {
  echo "交互配置 HMN 主控，可直接回车使用当前默认值。"
  echo "当前默认值：host=${HMN_HOST}, port=${HMN_PORT}, user=${HMN_USER}, home=${HMN_HOME}, db=${HMN_DB}"
  prompt_default HMN_HOST "监听地址"
  prompt_default HMN_PORT "监听端口"
  prompt_default HMN_USER "运行用户"
  prompt_default HMN_HOME "安装目录"
  prompt_default HMN_DB "数据库路径"
  prompt_default HMN_PUBLIC_URL "公网访问 URL"
  prompt_yes_no_default HMN_ENABLE_TELEGRAM "启用 Telegram Bot 审批网关？1=启用,0=跳过"
  if [ "$HMN_ENABLE_TELEGRAM" = "1" ]; then
    prompt_default HMN_TELEGRAM_CHAT_ID "Telegram Chat ID"
    prompt_secret HMN_TELEGRAM_BOT_TOKEN "Telegram Bot Token"
  fi
  prompt_default HMN_HEADSCALE_MODE "Headscale 模式 bundled/external/disabled"
  if [ "$HMN_HEADSCALE_MODE" != "disabled" ]; then
    prompt_default HMN_HEADSCALE_URL "Headscale URL"
    prompt_default HMN_HEADSCALE_NAMESPACE "Headscale namespace/user"
    if [ "$HMN_HEADSCALE_MODE" = "external" ]; then
      prompt_secret HMN_HEADSCALE_API_KEY "Headscale API Key"
    fi
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

detect_existing_version() {
  if [ -x "$HMN_HOME/.venv/bin/hmn" ]; then
    EXISTING_VERSION="$($HMN_HOME/.venv/bin/hmn version 2>/dev/null | awk '{print $NF}' || true)"
    [ -n "$EXISTING_VERSION" ] || EXISTING_VERSION="unknown"
  else
    EXISTING_VERSION="not-installed"
  fi
}

backup_existing_state() {
  if [ "$EXISTING_VERSION" = "not-installed" ]; then
    return
  fi
  install -d -m 0750 "$HMN_BACKUP_DIR"
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  HMN_LAST_BACKUP_STAMP="$stamp"
  if [ -f "$HMN_DB" ]; then
    cp -a "$HMN_DB" "$HMN_BACKUP_DIR/control-plane.${stamp}.db"
  fi
  if [ -f /etc/hermes-managed-network/master.env ]; then
    cp -a /etc/hermes-managed-network/master.env "$HMN_BACKUP_DIR/master.${stamp}.env"
  fi
  if [ -f /etc/hermes-managed-network/approval-gateway.env ]; then
    cp -a /etc/hermes-managed-network/approval-gateway.env "$HMN_BACKUP_DIR/approval-gateway.${stamp}.env"
  fi
  if [ -f /etc/hermes-managed-network/telegram-gateway.env ]; then
    cp -a /etc/hermes-managed-network/telegram-gateway.env "$HMN_BACKUP_DIR/telegram-gateway.${stamp}.env"
  fi
  if [ -f /etc/hermes-managed-network/config.yaml ]; then
    cp -a /etc/hermes-managed-network/config.yaml "$HMN_BACKUP_DIR/config.${stamp}.yaml"
  fi
  cat >"$HMN_BACKUP_DIR/metadata.${stamp}.env" <<EOF
BACKUP_STAMP=${stamp}
PREVIOUS_VERSION=${EXISTING_VERSION}
BACKUP_DB=${HMN_BACKUP_DIR}/control-plane.${stamp}.db
BACKUP_ENV=${HMN_BACKUP_DIR}/master.${stamp}.env
BACKUP_CONFIG=${HMN_BACKUP_DIR}/config.${stamp}.yaml
BACKUP_METADATA=${HMN_BACKUP_DIR}/metadata.${stamp}.env
EOF
}

version_policy() {
  detect_existing_version
  if [ "$EXISTING_VERSION" = "not-installed" ]; then
    VERSION_POLICY="install"
    echo "未发现已部署版本，将执行新安装。"
    return
  fi
  CURRENT_VERSION="$($HMN_HOME/.venv/bin/python - <<'PY' 2>/dev/null || true
from hermes_managed_network.version import package_version
print(package_version())
PY
)"
  [ -n "$CURRENT_VERSION" ] || CURRENT_VERSION="target"
  if [ "$EXISTING_VERSION" = "$CURRENT_VERSION" ]; then
    VERSION_POLICY="reinstall"
    echo "版本一致：${EXISTING_VERSION}，将执行幂等重装/修复并自检。"
    return
  fi
  VERSION_POLICY="upgrade"
  echo "版本不同：已部署=${EXISTING_VERSION}，当前安装=${CURRENT_VERSION}。"
  case "$HMN_UPGRADE_POLICY" in
    auto|yes)
      backup_existing_state
      ;;
    abort|no)
      echo "HMN_UPGRADE_POLICY=abort，停止安装。" >&2
      exit 1
      ;;
    prompt|*)
      if [ "$HMN_ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
        backup_existing_state
      else
        local answer
        read -r -p "是否备份状态并继续升级？[Y/n]: " answer
        case "$answer" in
          n|N|no|NO) exit 1 ;;
          *) backup_existing_state ;;
        esac
      fi
      ;;
  esac
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

write_upgrade_manifest() {
  install -d -m 0750 -o "$HMN_USER" -g "$HMN_USER" /etc/hermes-managed-network
  cat >/etc/hermes-managed-network/upgrade-manifest.env <<EOF
PREVIOUS_VERSION=${EXISTING_VERSION}
TARGET_VERSION=${CURRENT_VERSION}
VERSION_POLICY=${VERSION_POLICY}
HMN_BACKUP_DIR=${HMN_BACKUP_DIR}
HMN_LAST_BACKUP_STAMP=${HMN_LAST_BACKUP_STAMP}
BACKUP_DB=${HMN_BACKUP_DIR}/control-plane.${HMN_LAST_BACKUP_STAMP}.db
BACKUP_ENV=${HMN_BACKUP_DIR}/master.${HMN_LAST_BACKUP_STAMP}.env
BACKUP_CONFIG=${HMN_BACKUP_DIR}/config.${HMN_LAST_BACKUP_STAMP}.yaml
BACKUP_METADATA=${HMN_BACKUP_DIR}/metadata.${HMN_LAST_BACKUP_STAMP}.env
HMN_ROLLBACK_HINT=${HMN_ROLLBACK_HINT}
ROLLBACK_COMMAND=${HMN_ROLLBACK_HINT}
EOF
  chmod 0640 /etc/hermes-managed-network/upgrade-manifest.env
  chown "$HMN_USER:$HMN_USER" /etc/hermes-managed-network/upgrade-manifest.env
}

write_env() {
  install -d -m 0750 -o "$HMN_USER" -g "$HMN_USER" /etc/hermes-managed-network
  cat >/etc/hermes-managed-network/master.env <<EOF
HMN_DB=${HMN_DB}
HMN_HOST=${HMN_HOST}
HMN_PORT=${HMN_PORT}
HMN_PUBLIC_URL=${HMN_PUBLIC_URL}
HMN_HEADSCALE_MODE=${HMN_HEADSCALE_MODE}
HMN_HEADSCALE_URL=${HMN_HEADSCALE_URL}
HMN_HEADSCALE_API_KEY=${HMN_HEADSCALE_API_KEY}
HMN_HEADSCALE_NAMESPACE=${HMN_HEADSCALE_NAMESPACE}
EOF
  chmod 0640 /etc/hermes-managed-network/master.env
  chown "$HMN_USER:$HMN_USER" /etc/hermes-managed-network/master.env
}

write_approval_gateway_env() {
  if [ "$HMN_ENABLE_TELEGRAM" != "1" ]; then
    return
  fi
  if [ "$HMN_APPROVAL_GATEWAY_CLIENT" != "telegram" ]; then
    echo "暂不支持 HMN_APPROVAL_GATEWAY_CLIENT=${HMN_APPROVAL_GATEWAY_CLIENT}，当前可用：telegram" >&2
    exit 1
  fi
  if [ -z "$HMN_APPROVAL_GATEWAY_TARGET" ] || [ -z "$HMN_APPROVAL_GATEWAY_TOKEN" ]; then
    echo "HMN_ENABLE_TELEGRAM=1 但缺少 HMN_APPROVAL_GATEWAY_TARGET/HMN_TELEGRAM_CHAT_ID 或 HMN_APPROVAL_GATEWAY_TOKEN/HMN_TELEGRAM_BOT_TOKEN" >&2
    exit 1
  fi
  install -d -m 0750 -o "$HMN_USER" -g "$HMN_USER" /etc/hermes-managed-network
  cat >/etc/hermes-managed-network/approval-gateway.env <<EOF
HMN_API_URL=http://127.0.0.1:${HMN_PORT}
HMN_APPROVAL_GATEWAY_CLIENT=${HMN_APPROVAL_GATEWAY_CLIENT}
HMN_APPROVAL_GATEWAY_TARGET=${HMN_APPROVAL_GATEWAY_TARGET}
HMN_APPROVAL_GATEWAY_TOKEN=${HMN_APPROVAL_GATEWAY_TOKEN}
HMN_TELEGRAM_CHAT_ID=${HMN_APPROVAL_GATEWAY_TARGET}
HMN_TELEGRAM_BOT_TOKEN=${HMN_APPROVAL_GATEWAY_TOKEN}
EOF
  chmod 0640 /etc/hermes-managed-network/approval-gateway.env
  chown "$HMN_USER:$HMN_USER" /etc/hermes-managed-network/approval-gateway.env

  # Backward-compatible env file for operators/scripts that still reference the old Telegram gateway name.
  cp -a /etc/hermes-managed-network/approval-gateway.env /etc/hermes-managed-network/telegram-gateway.env
  chmod 0640 /etc/hermes-managed-network/telegram-gateway.env
  chown "$HMN_USER:$HMN_USER" /etc/hermes-managed-network/telegram-gateway.env
}

write_telegram_gateway_env() {
  write_approval_gateway_env
}

write_approval_gateway_service() {
  if [ "$HMN_ENABLE_TELEGRAM" != "1" ]; then
    return
  fi
  cat >/etc/systemd/system/hermes-managed-network-approval-gateway.service <<EOF
[Unit]
Description=Hermes Managed Network approval gateway
After=network-online.target hermes-managed-network.service
Wants=network-online.target

[Service]
Type=simple
User=${HMN_USER}
Group=${HMN_USER}
EnvironmentFile=/etc/hermes-managed-network/approval-gateway.env
ExecStart=/usr/local/bin/hmn approval-gateway run --client ${HMN_APPROVAL_GATEWAY_CLIENT} --target ${HMN_APPROVAL_GATEWAY_TARGET} --interval ${HMN_APPROVAL_GATEWAY_INTERVAL}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF

  # Compatibility service: keep the legacy unit name available but route it through the generic gateway.
  cat >/etc/systemd/system/hermes-managed-network-telegram-gateway.service <<EOF
[Unit]
Description=Hermes Managed Network Telegram approval gateway (legacy alias)
After=network-online.target hermes-managed-network.service
Wants=network-online.target

[Service]
Type=simple
User=${HMN_USER}
Group=${HMN_USER}
EnvironmentFile=/etc/hermes-managed-network/telegram-gateway.env
ExecStart=/usr/local/bin/hmn telegram-gateway run --interval ${HMN_TELEGRAM_INTERVAL}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
}

write_telegram_gateway_service() {
  write_approval_gateway_service
}

configure_headscale_provider() {
  if [ "$HMN_HEADSCALE_MODE" = "disabled" ]; then
    return
  fi
  install -d -m 0750 -o "$HMN_USER" -g "$HMN_USER" /etc/hermes-managed-network
  cat >/etc/hermes-managed-network/headscale.env <<EOF
HMN_HEADSCALE_MODE=${HMN_HEADSCALE_MODE}
HMN_HEADSCALE_URL=${HMN_HEADSCALE_URL}
HMN_HEADSCALE_API_KEY=${HMN_HEADSCALE_API_KEY}
HMN_HEADSCALE_NAMESPACE=${HMN_HEADSCALE_NAMESPACE}
EOF
  cat >/etc/hermes-managed-network/config.yaml <<EOF
network:
  provider: headscale
  headscale:
    url: ${HMN_HEADSCALE_URL}
    api_key_env: HMN_HEADSCALE_API_KEY
    user: ${HMN_HEADSCALE_NAMESPACE}
EOF
  chmod 0640 /etc/hermes-managed-network/headscale.env /etc/hermes-managed-network/config.yaml
  chown "$HMN_USER:$HMN_USER" /etc/hermes-managed-network/headscale.env /etc/hermes-managed-network/config.yaml
}

install_headscale_bundled() {
  if [ "$HMN_HEADSCALE_MODE" != "bundled" ]; then
    return
  fi
  echo "准备内置 Headscale 配置骨架。真实 headscale-server 组件会负责安装/配置服务。"
  if [ -z "$HMN_HEADSCALE_URL" ]; then
    HMN_HEADSCALE_URL="${HMN_PUBLIC_URL%/}/headscale"
  fi
  configure_headscale_provider
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

self_check() {
  echo "执行部署后自检..."
  systemctl is-active --quiet hermes-managed-network.service || return 1
  local ready=0
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${HMN_PORT}/healthz" >/dev/null; then
      ready=1
      break
    fi
    sleep 1
  done
  if [ "$ready" != "1" ]; then
    echo "healthz 未就绪" >&2
    return 1
  fi
  curl -fsS "http://127.0.0.1:${HMN_PORT}/api/v1/version" >/dev/null || return 1
  "$HMN_HOME/.venv/bin/hmn" version || return 1
  "$HMN_HOME/.venv/bin/hmn" doctor --skip-systemd || true
  echo "自检通过。"
}

print_failure_hint() {
  echo "部署自检失败，最近日志如下：" >&2
  journalctl -u hermes-managed-network.service -n 80 --no-pager >&2 || true
}

main() {
  need_root
  interactive_config
  install_dependencies
  need_command "$PYTHON_BIN"
  check_platform
  ensure_user
  version_policy
  install_package
  verify_install
  write_upgrade_manifest
  write_env
  write_approval_gateway_env
  install_headscale_bundled
  if [ "$HMN_HEADSCALE_MODE" = "external" ]; then
    configure_headscale_provider
  fi
  install_cli_links
  write_service
  write_approval_gateway_service
  systemctl daemon-reload
  systemctl enable hermes-managed-network.service
  systemctl restart hermes-managed-network.service
  if [ "$HMN_ENABLE_TELEGRAM" = "1" ]; then
    systemctl enable hermes-managed-network-approval-gateway.service
    systemctl restart hermes-managed-network-approval-gateway.service
  fi
  if ! self_check; then
    print_failure_hint
    exit 1
  fi
  systemctl --no-pager --full status hermes-managed-network.service || true
  if [ "$HMN_ENABLE_TELEGRAM" = "1" ]; then
    systemctl --no-pager --full status hermes-managed-network-approval-gateway.service || true
  fi
}

main "$@"
