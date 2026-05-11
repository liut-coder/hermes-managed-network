#!/usr/bin/env bash
set -euo pipefail

# NAS / OpenWrt / IPv6-only lite-worker real-device smoke gate.
# Required env:
#   HMN_MASTER_HOST=master.example.invalid
#   HMN_DEVICE_HOST=nas-or-openwrt.example.invalid
#   HMN_SSH_KEY=/path/to/ssh_key
#   HMN_IPV6_MASTER_URL=http://[2001:db8::10]:8765
# Optional env:
#   HMN_REMOTE_USER=root
#   HMN_DEVICE_USER=root
#   HMN_PUBLIC_URL=http://master.example.invalid:8765
#   HMN_HEADSCALE_URL=http://headscale.internal:8765
#   HMN_RELAY_URL=https://relay.example.invalid
#   HMN_DEVICE_KIND=auto          # auto|Synology|QNAP|OpenWrt|cron|procd
#   HMN_SERVICE_MANAGER=auto      # auto|cron|procd|loop
#   HMN_REMOTE_BRANCH=main
#   HMN_REMOTE_TMP=/tmp/hmn-nas-ipv6-lite-worker-smoke
#   HMN_SKIP_INSTALL=0

# Examples:
#   HMN_SERVICE_MANAGER=cron  # Synology/QNAP cron adapter: --service-manager cron
#   HMN_SERVICE_MANAGER=procd # OpenWrt procd adapter: --service-manager procd

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${HMN_MASTER_HOST:?set HMN_MASTER_HOST}"
: "${HMN_DEVICE_HOST:?set HMN_DEVICE_HOST}"
: "${HMN_SSH_KEY:?set HMN_SSH_KEY}"
: "${HMN_IPV6_MASTER_URL:?set HMN_IPV6_MASTER_URL, e.g. http://[2001:db8::10]:8765}"

HMN_REMOTE_USER="${HMN_REMOTE_USER:-root}"
HMN_DEVICE_USER="${HMN_DEVICE_USER:-$HMN_REMOTE_USER}"
HMN_REMOTE_BRANCH="${HMN_REMOTE_BRANCH:-main}"
HMN_REMOTE_TMP="${HMN_REMOTE_TMP:-/tmp/hmn-nas-ipv6-lite-worker-smoke}"
HMN_PUBLIC_URL="${HMN_PUBLIC_URL:-http://${HMN_MASTER_HOST}:8765}"
HMN_HEADSCALE_URL="${HMN_HEADSCALE_URL:-http://headscale.internal:8765}"
HMN_RELAY_URL="${HMN_RELAY_URL:-https://relay.example.invalid}"
HMN_DEVICE_KIND="${HMN_DEVICE_KIND:-auto}"
HMN_SERVICE_MANAGER="${HMN_SERVICE_MANAGER:-auto}"
HMN_SKIP_INSTALL="${HMN_SKIP_INSTALL:-0}"

SSH_OPTS=(
  -i "$HMN_SSH_KEY"
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
)
MASTER_SSH="${HMN_REMOTE_USER}@${HMN_MASTER_HOST}"
DEVICE_SSH="${HMN_DEVICE_USER}@${HMN_DEVICE_HOST}"

log() {
  printf '\n==> %s\n' "$*"
}

remote() {
  local target="$1"
  shift
  ssh "${SSH_OPTS[@]}" "$target" "$@"
}

remote_script() {
  local target="$1"
  shift
  ssh "${SSH_OPTS[@]}" "$target" 'sh -s' "$@"
}

extract_last_field() {
  awk 'NF {print $NF}' | tail -n 1
}

detect_service_manager() {
  if [ "$HMN_SERVICE_MANAGER" != "auto" ]; then
    printf '%s\n' "$HMN_SERVICE_MANAGER"
    return
  fi
  case "$HMN_DEVICE_KIND" in
    OpenWrt|openwrt) printf 'procd\n'; return ;;
    Synology|synology|QNAP|qnap|cron) printf 'cron\n'; return ;;
    procd) printf 'procd\n'; return ;;
  esac
  if remote "$DEVICE_SSH" 'command -v procd >/dev/null 2>&1 || test -x /sbin/procd'; then
    printf 'procd\n'
  else
    printf 'cron\n'
  fi
}

log "检查 Master / NAS / OpenWrt SSH 连通性"
remote "$MASTER_SSH" 'true'
remote "$DEVICE_SSH" 'true'

if [ "$HMN_SKIP_INSTALL" != "1" ]; then
  log "安装 / 升级 Master"
  ssh "${SSH_OPTS[@]}" "$MASTER_SSH" 'bash -s' <<EOF
set -euo pipefail
rm -rf "$HMN_REMOTE_TMP"
git clone --depth 1 --branch "$HMN_REMOTE_BRANCH" https://github.com/liut-coder/hermes-managed-network.git "$HMN_REMOTE_TMP"
cd "$HMN_REMOTE_TMP"
HMN_ASSUME_YES=1 HMN_PUBLIC_URL="$HMN_PUBLIC_URL" bash scripts/install-master.sh
EOF
else
  log "跳过 Master 安装：HMN_SKIP_INSTALL=1"
fi

log "验证 Master 健康与 IPv6 literal URL 语法"
remote "$MASTER_SSH" 'systemctl is-active --quiet hermes-managed-network.service'
remote "$MASTER_SSH" "curl -fsS ${HMN_PUBLIC_URL}/healthz >/dev/null"
remote "$MASTER_SSH" "curl -fsS ${HMN_PUBLIC_URL}/api/v1/version >/dev/null"
JOIN_TOKEN="$(remote "$MASTER_SSH" 'hmn token create --trust B --label lite-worker --label nas-ipv6-smoke --ttl-minutes 15' | extract_last_field)"
if [ -z "$JOIN_TOKEN" ]; then
  echo "无法解析 join token" >&2
  exit 1
fi
JOIN_COMMAND="$(remote "$MASTER_SSH" "hmn token join-command \"$JOIN_TOKEN\" --master-url \"$HMN_IPV6_MASTER_URL\" --safe")"
printf '%s\n' "$JOIN_COMMAND" | grep -q -- "--master-url \"$HMN_IPV6_MASTER_URL\""

log "NAS / OpenWrt 设备能力探测（Synology/QNAP/OpenWrt 均应走 lite-worker 安全路径）"
remote_script "$DEVICE_SSH" <<'EOF'
set -eu
printf 'uname=%s\n' "$(uname -a 2>/dev/null || true)"
printf 'has_sh=%s\n' "$(command -v sh >/dev/null 2>&1 && echo yes || echo no)"
printf 'has_curl=%s\n' "$(command -v curl >/dev/null 2>&1 && echo yes || echo no)"
printf 'has_wget=%s\n' "$(command -v wget >/dev/null 2>&1 && echo yes || echo no)"
printf 'has_crond=%s\n' "$(command -v crond >/dev/null 2>&1 && echo yes || echo no)"
printf 'has_procd=%s\n' "$(command -v procd >/dev/null 2>&1 && echo yes || echo no)"
EOF

SERVICE_MANAGER="$(detect_service_manager)"
case "$SERVICE_MANAGER" in
  cron|procd|loop) ;;
  *) echo "Unsupported service manager: $SERVICE_MANAGER" >&2; exit 1 ;;
esac
echo "SERVICE_MANAGER=$SERVICE_MANAGER"

log "通过 IPv6 literal URL 接入节点"
remote_script "$DEVICE_SSH" <<EOF
set -eu
$JOIN_COMMAND
EOF
unset JOIN_TOKEN JOIN_COMMAND

log "确认节点并渲染 lite-worker fallback installer"
remote "$MASTER_SSH" 'hmn node confirm --bundle observe'
NODE_ID="$(remote "$MASTER_SSH" "hmn node list | awk '/nas-ipv6-smoke|lite-worker/ {print \$1}' | tail -n 1")"
if [ -z "$NODE_ID" ]; then
  NODE_ID="$(remote "$MASTER_SSH" "hmn node list | awk 'NF {print \$1}' | tail -n 1")"
fi
if [ -z "$NODE_ID" ]; then
  echo "无法解析 node id" >&2
  exit 1
fi
echo "NODE_ID=$NODE_ID"
INSTALL_CMD="$(remote "$MASTER_SSH" "hmn node install-heartbeat \"$NODE_ID\" --master-url \"$HMN_IPV6_MASTER_URL\" --runtime lite-worker --service-manager \"$SERVICE_MANAGER\" --endpoint \"$HMN_IPV6_MASTER_URL\" --endpoint \"$HMN_HEADSCALE_URL\" --endpoint \"$HMN_RELAY_URL\"")"
printf '%s\n' "$INSTALL_CMD"
printf '%s\n' "$INSTALL_CMD" | grep -q 'runtime=lite-worker'
printf '%s\n' "$INSTALL_CMD" | grep -q "service_manager=$SERVICE_MANAGER"
printf '%s\n' "$INSTALL_CMD" | grep -q 'HMN_ENABLE_EXEC=0'
printf '%s\n' "$INSTALL_CMD" | grep -q 'HMN_MASTER_URLS'
printf '%s\n' "$INSTALL_CMD" | grep -q "$HMN_IPV6_MASTER_URL,$HMN_HEADSCALE_URL,$HMN_RELAY_URL"
printf '%s\n' "$INSTALL_CMD" | grep -q 'scripts/worker-lite.sh'

log "安装 lite-worker，并验证 POSIX sh 兼容"
remote_script "$DEVICE_SSH" <<EOF
set -eu
$INSTALL_CMD
sh -n /usr/local/bin/hmn-worker
HMN_ENV_FILE=/etc/hermes-managed-network/node.env HMN_ENABLE_EXEC=0 /bin/sh /usr/local/bin/hmn-worker || true
EOF

log "验证 worker-status / fallback URL / disabled-exec 安全拒绝"
sleep 2
remote "$MASTER_SSH" "hmn node worker-status \"$NODE_ID\""
remote "$MASTER_SSH" "hmn task run --node \"$NODE_ID\" --risk low --executor worker 'echo nas-ipv6-lite-worker-smoke'"
remote_script "$DEVICE_SSH" <<'EOF'
set -eu
grep -q 'HMN_MASTER_URLS=' /etc/hermes-managed-network/node.env
grep -q 'HMN_ENABLE_EXEC=0' /etc/hermes-managed-network/node.env
HMN_ENV_FILE=/etc/hermes-managed-network/node.env HMN_ENABLE_EXEC=0 /bin/sh /usr/local/bin/hmn-worker || true
EOF
ssh "${SSH_OPTS[@]}" "$MASTER_SSH" 'bash -s' <<'EOF'
set -euo pipefail
HMN_PYTHON="$(command -v python3)"
if [ -x /opt/hermes-managed-network/.venv/bin/python ]; then
  HMN_PYTHON=/opt/hermes-managed-network/.venv/bin/python
fi
"$HMN_PYTHON" - <<'PY'
from hermes_managed_network.storage import SQLiteStore
from hermes_managed_network.cli import _default_db
s = SQLiteStore(_default_db())
task = s.list_tasks()[0]
assert task.status == 'failed', task
assert task.exit_code == 126, task
assert 'execution disabled' in (task.stderr or ''), task.stderr
print('TASK_STATUS', task.status)
print('TASK_EXIT', task.exit_code)
print('TASK_STDERR', task.stderr)
PY
EOF

log "生成资产文档"
remote "$MASTER_SSH" 'hmn docs generate'

log "NAS / OpenWrt / IPv6-only lite-worker fallback 真实设备 smoke 通过"
