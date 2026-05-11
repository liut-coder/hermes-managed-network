#!/usr/bin/env bash
set -euo pipefail

# Repeatable remote HMN smoke gate for two disposable Linux/systemd hosts.
# Required env:
#   HMN_MASTER_HOST=master.example.invalid
#   HMN_WORKER_HOST=worker.example.invalid
#   HMN_SSH_KEY=/path/to/ssh_key
# Optional env:
#   HMN_REMOTE_USER=root
#   HMN_PUBLIC_URL=http://MASTER:8765
#   HMN_REMOTE_PORT=8765
#   HMN_REMOTE_BRANCH=main
#   HMN_REMOTE_TMP=/tmp/hmn-remote-e2e
#   HMN_SKIP_INSTALL=0
#   HMN_SKIP_SSH_EXECUTOR=0

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${HMN_MASTER_HOST:?set HMN_MASTER_HOST}"
: "${HMN_WORKER_HOST:?set HMN_WORKER_HOST}"
: "${HMN_SSH_KEY:?set HMN_SSH_KEY}"

HMN_REMOTE_USER="${HMN_REMOTE_USER:-root}"
HMN_REMOTE_PORT="${HMN_REMOTE_PORT:-8765}"
HMN_REMOTE_BRANCH="${HMN_REMOTE_BRANCH:-main}"
HMN_REMOTE_TMP="${HMN_REMOTE_TMP:-/tmp/hmn-remote-e2e}"
HMN_PUBLIC_URL="${HMN_PUBLIC_URL:-http://${HMN_MASTER_HOST}:${HMN_REMOTE_PORT}}"
HMN_SKIP_INSTALL="${HMN_SKIP_INSTALL:-0}"
HMN_SKIP_SSH_EXECUTOR="${HMN_SKIP_SSH_EXECUTOR:-0}"

SSH_OPTS=(
  -i "$HMN_SSH_KEY"
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
)
MASTER_SSH="${HMN_REMOTE_USER}@${HMN_MASTER_HOST}"
WORKER_SSH="${HMN_REMOTE_USER}@${HMN_WORKER_HOST}"

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
  ssh "${SSH_OPTS[@]}" "$target" 'bash -s' "$@"
}

extract_last_field() {
  awk 'NF {print $NF}' | tail -n 1
}

log "检查 SSH 连通性"
remote "$MASTER_SSH" 'true'
remote "$WORKER_SSH" 'true'

if [ "$HMN_SKIP_INSTALL" != "1" ]; then
  log "安装 / 升级 Master"
  remote_script "$MASTER_SSH" <<EOF
set -euo pipefail
rm -rf "$HMN_REMOTE_TMP"
git clone --depth 1 --branch "$HMN_REMOTE_BRANCH" https://github.com/liut-coder/hermes-managed-network.git "$HMN_REMOTE_TMP"
cd "$HMN_REMOTE_TMP"
HMN_ASSUME_YES=1 \
HMN_HOST=0.0.0.0 \
HMN_PORT="$HMN_REMOTE_PORT" \
HMN_PUBLIC_URL="$HMN_PUBLIC_URL" \
bash scripts/install-master.sh
EOF
else
  log "跳过 Master 安装：HMN_SKIP_INSTALL=1"
fi

log "验证 Master systemd 与 HTTP 健康"
remote "$MASTER_SSH" 'systemctl is-active --quiet hermes-managed-network.service'
remote "$MASTER_SSH" 'hmn version'
remote "$MASTER_SSH" "curl -fsS http://127.0.0.1:${HMN_REMOTE_PORT}/healthz >/dev/null"
remote "$MASTER_SSH" "curl -fsS http://127.0.0.1:${HMN_REMOTE_PORT}/api/v1/version >/dev/null"
remote "$MASTER_SSH" "curl -fsS ${HMN_PUBLIC_URL}/healthz >/dev/null"
remote "$MASTER_SSH" "curl -fsS ${HMN_PUBLIC_URL}/api/v1/version >/dev/null"
remote "$MASTER_SSH" 'hmn doctor'

log "创建 join token 并接入 Worker"
JOIN_TOKEN="$(remote "$MASTER_SSH" 'hmn token create --trust B --label worker --label remote-smoke --ttl-minutes 15' | extract_last_field)"
if [ -z "$JOIN_TOKEN" ]; then
  echo "无法解析 join token" >&2
  exit 1
fi
remote_script "$WORKER_SSH" <<EOF
set -euo pipefail
HERMES_JOIN_TOKEN="$JOIN_TOKEN" HERMES_MASTER_URL="$HMN_PUBLIC_URL" bash <(curl -fsSL "$HMN_PUBLIC_URL/scripts/join.sh")
EOF
unset JOIN_TOKEN

log "确认 Worker 节点"
remote "$MASTER_SSH" 'hmn node confirm --bundle observe'
NODE_ID="$(remote "$MASTER_SSH" "hmn node list | awk '/remote-smoke|worker/ {print \$1}' | tail -n 1")"
if [ -z "$NODE_ID" ]; then
  NODE_ID="$(remote "$MASTER_SSH" "hmn node list | awk 'NF {print \$1}' | tail -n 1")"
fi
if [ -z "$NODE_ID" ]; then
  echo "无法解析 node id" >&2
  exit 1
fi
echo "NODE_ID=$NODE_ID"

log "安装安全模式 worker heartbeat timer"
INSTALL_CMD="$(remote "$MASTER_SSH" "hmn node install-heartbeat \"$NODE_ID\" --master-url \"$HMN_PUBLIC_URL\" --service-manager systemd")"
printf '%s\n' "$INSTALL_CMD" | grep -q 'HMN_ENABLE_EXEC=0'
remote_script "$WORKER_SSH" <<EOF
set -euo pipefail
$INSTALL_CMD
EOF
remote "$WORKER_SSH" 'systemctl is-active --quiet hermes-managed-network-heartbeat.timer'
remote "$WORKER_SSH" 'systemctl start hermes-managed-network-heartbeat.service || true'
sleep 2

log "验证 worker-status"
remote "$MASTER_SSH" "hmn node worker-status \"$NODE_ID\""

log "验证 worker disabled-exec 安全拒绝"
remote "$MASTER_SSH" "hmn task run --node \"$NODE_ID\" --risk low --executor worker 'echo remote-smoke-disabled'"
remote "$WORKER_SSH" 'HMN_ENV_FILE=/etc/hermes-managed-network/node.env HMN_ENABLE_EXEC=0 /usr/local/bin/hmn-worker || true'
remote_script "$MASTER_SSH" <<'EOF'
set -euo pipefail
HMN_PYTHON="$(command -v python3)"
if [ -x /opt/hermes-managed-network/.venv/bin/python ]; then
  HMN_PYTHON=/opt/hermes-managed-network/.venv/bin/python
fi
"$HMN_PYTHON" - <<'PY'
from hermes_managed_network.storage import SQLiteStore
from hermes_managed_network.cli import _default_db
s = SQLiteStore(_default_db())
tasks = s.list_tasks()
assert tasks, 'missing task'
task = tasks[0]
assert task.status == 'failed', task
assert task.exit_code == 126, task
assert 'execution disabled' in (task.stderr or ''), task.stderr
print('TASK_STATUS', task.status)
print('TASK_EXIT', task.exit_code)
print('TASK_STDERR', task.stderr)
PY
EOF

if [ "$HMN_SKIP_SSH_EXECUTOR" != "1" ]; then
  log "验证 SSH executor 路由（如目标密钥未配置，可用 HMN_SKIP_SSH_EXECUTOR=1 跳过）"
  remote "$MASTER_SSH" "hmn task run --node \"$NODE_ID\" --risk low --executor ssh 'true'"
  remote "$MASTER_SSH" 'hmn task ssh-run-next'
fi

log "生成资产文档"
remote "$MASTER_SSH" 'hmn docs generate'

log "远程 E2E smoke 通过"
