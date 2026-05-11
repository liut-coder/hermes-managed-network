#!/usr/bin/env bash
set -euo pipefail

# Headscale bundled / external real overlay network smoke gate.
# Required env:
#   HMN_MASTER_HOST=master.example.invalid
#   HMN_WORKER_HOST=worker.example.invalid
#   HMN_SSH_KEY=/path/to/ssh_key
#   HMN_HEADSCALE_MODE=external          # bundled|external
#   HMN_HEADSCALE_URL=https://hs.example.invalid
#   HMN_HEADSCALE_API_KEY=<headscale-api-key>
# Optional env:
#   HMN_REMOTE_USER=root
#   HMN_PUBLIC_URL=http://master.example.invalid:8765
#   HMN_HEADSCALE_NAMESPACE=misk
#   HMN_HEADSCALE_TAG=tag:hmn-smoke
#   HMN_REMOTE_BRANCH=main
#   HMN_REMOTE_TMP=/tmp/hmn-headscale-network-smoke
#   HMN_SKIP_INSTALL=0
#   HMN_SKIP_TAILSCALE_UP=0

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${HMN_MASTER_HOST:?set HMN_MASTER_HOST}"
: "${HMN_WORKER_HOST:?set HMN_WORKER_HOST}"
: "${HMN_SSH_KEY:?set HMN_SSH_KEY}"
: "${HMN_HEADSCALE_MODE:?set HMN_HEADSCALE_MODE to bundled|external}"
: "${HMN_HEADSCALE_URL:?set HMN_HEADSCALE_URL}"
: "${HMN_HEADSCALE_API_KEY:?set HMN_HEADSCALE_API_KEY}"

case "$HMN_HEADSCALE_MODE" in
  bundled|external) ;;
  *) echo "HMN_HEADSCALE_MODE must be bundled|external" >&2; exit 1 ;;
esac

HMN_REMOTE_USER="${HMN_REMOTE_USER:-root}"
HMN_REMOTE_BRANCH="${HMN_REMOTE_BRANCH:-main}"
HMN_REMOTE_TMP="${HMN_REMOTE_TMP:-/tmp/hmn-headscale-network-smoke}"
HMN_PUBLIC_URL="${HMN_PUBLIC_URL:-http://${HMN_MASTER_HOST}:8765}"
HMN_HEADSCALE_NAMESPACE="${HMN_HEADSCALE_NAMESPACE:-misk}"
HMN_HEADSCALE_TAG="${HMN_HEADSCALE_TAG:-tag:hmn-smoke}"
HMN_SKIP_INSTALL="${HMN_SKIP_INSTALL:-0}"
HMN_SKIP_TAILSCALE_UP="${HMN_SKIP_TAILSCALE_UP:-0}"

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
  log "安装 / 升级 Master，并写入 Headscale $HMN_HEADSCALE_MODE 配置"
  remote_script "$MASTER_SSH" <<EOF
set -euo pipefail
rm -rf "$HMN_REMOTE_TMP"
git clone --depth 1 --branch "$HMN_REMOTE_BRANCH" https://github.com/liut-coder/hermes-managed-network.git "$HMN_REMOTE_TMP"
cd "$HMN_REMOTE_TMP"
HMN_ASSUME_YES=1 \
HMN_PUBLIC_URL="$HMN_PUBLIC_URL" \
HMN_HEADSCALE_MODE="$HMN_HEADSCALE_MODE" \
HMN_HEADSCALE_URL="$HMN_HEADSCALE_URL" \
HMN_HEADSCALE_API_KEY="$HMN_HEADSCALE_API_KEY" \
HMN_HEADSCALE_NAMESPACE="$HMN_HEADSCALE_NAMESPACE" \
bash scripts/install-master.sh
EOF
else
  log "跳过 Master 安装：HMN_SKIP_INSTALL=1"
fi

log "校验 Master 与 Headscale provider 配置"
remote "$MASTER_SSH" 'systemctl is-active --quiet hermes-managed-network.service'
remote "$MASTER_SSH" 'test -f /etc/hermes-managed-network/config.yaml'
remote "$MASTER_SSH" "grep -q 'provider: headscale' /etc/hermes-managed-network/config.yaml"
remote "$MASTER_SSH" "grep -q 'api_key_env: HMN_HEADSCALE_API_KEY' /etc/hermes-managed-network/config.yaml"
remote "$MASTER_SSH" "HMN_HEADSCALE_API_KEY='$HMN_HEADSCALE_API_KEY' hmn network status"

log "创建 Headscale preauth key"
PREAUTH_KEY="$(remote "$MASTER_SSH" "HMN_HEADSCALE_API_KEY='$HMN_HEADSCALE_API_KEY' hmn network preauth-key create --tag '$HMN_HEADSCALE_TAG' --expiration '1h' --node headscale-smoke-worker" | extract_last_field)"
if [ -z "$PREAUTH_KEY" ]; then
  echo "无法解析 Headscale preauth key" >&2
  exit 1
fi

if [ "$HMN_SKIP_TAILSCALE_UP" != "1" ]; then
  log "Worker 加入 Tailscale/Headscale overlay"
  remote_script "$WORKER_SSH" <<EOF
set -euo pipefail
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled
# tailscale up 使用临时 preauth key；不要打印 key。
TAILSCALE_HOSTNAME="hmn-smoke-worker-$(date +%s)"
tailscale up --login-server "$HMN_HEADSCALE_URL" --authkey "$PREAUTH_KEY" --hostname "$TAILSCALE_HOSTNAME"
tailscale status
EOF
else
  log "跳过 tailscale up：HMN_SKIP_TAILSCALE_UP=1"
fi
unset PREAUTH_KEY

log "接入 HMN worker 节点"
JOIN_TOKEN="$(remote "$MASTER_SSH" 'hmn token create --trust B --label worker --label headscale-smoke --ttl-minutes 15' | extract_last_field)"
if [ -z "$JOIN_TOKEN" ]; then
  echo "无法解析 HMN join token" >&2
  exit 1
fi
remote_script "$WORKER_SSH" <<EOF
set -euo pipefail
HERMES_JOIN_TOKEN="$JOIN_TOKEN" HERMES_MASTER_URL="$HMN_PUBLIC_URL" bash <(curl -fsSL "$HMN_PUBLIC_URL/scripts/join.sh")
EOF
unset JOIN_TOKEN

log "确认 HMN 节点并同步 Headscale provider node"
remote "$MASTER_SSH" 'hmn node confirm --bundle operate'
NODE_ID="$(remote "$MASTER_SSH" "hmn node list | awk '/headscale-smoke|worker/ {print \$1}' | tail -n 1")"
if [ -z "$NODE_ID" ]; then
  NODE_ID="$(remote "$MASTER_SSH" "hmn node list | awk 'NF {print \$1}' | tail -n 1")"
fi
if [ -z "$NODE_ID" ]; then
  echo "无法解析 node id" >&2
  exit 1
fi
echo "NODE_ID=$NODE_ID"
remote "$MASTER_SSH" "HMN_HEADSCALE_API_KEY='$HMN_HEADSCALE_API_KEY' hmn network sync"

log "验证 node record 保存 Headscale 网络字段"
NODE_STATUS="$(remote "$MASTER_SSH" "hmn node status \"$NODE_ID\"")"
printf '%s\n' "$NODE_STATUS"
printf '%s\n' "$NODE_STATUS" | grep -q 'network_provider: headscale'
printf '%s\n' "$NODE_STATUS" | grep -q 'network_node_id:'
printf '%s\n' "$NODE_STATUS" | grep -q 'network_ip:'
printf '%s\n' "$NODE_STATUS" | grep -q 'network_tags:'
printf '%s\n' "$NODE_STATUS" | grep -q 'network_online:'

log "验证 doctor / SSH executor / component verify 使用 network_ip"
DOCTOR_OUTPUT="$(remote "$MASTER_SSH" "hmn node doctor \"$NODE_ID\"")"
printf '%s\n' "$DOCTOR_OUTPUT"
printf '%s\n' "$DOCTOR_OUTPUT" | grep -q 'target_source: network_ip'
remote "$MASTER_SSH" "hmn task run --node \"$NODE_ID\" --risk low --executor ssh 'true'"
remote "$MASTER_SSH" 'hmn task ssh-run-next'
COMPONENT_OUTPUT="$(remote "$MASTER_SSH" "hmn component verify reverse-proxy --node \"$NODE_ID\"")"
printf '%s\n' "$COMPONENT_OUTPUT"
printf '%s\n' "$COMPONENT_OUTPUT" | grep -q 'remote_check: overlay_network'
printf '%s\n' "$COMPONENT_OUTPUT" | grep -q 'target_source: network_ip'

log "验证 network tag / ACL 写操作仍走 approval gate"
set +e
TAGS_OUTPUT="$(remote "$MASTER_SSH" "hmn network node tags set --node \"$NODE_ID\" --tag '$HMN_HEADSCALE_TAG'" 2>&1)"
TAGS_RC=$?
set -e
printf '%s\n' "$TAGS_OUTPUT"
[ "$TAGS_RC" -ne 0 ]
printf '%s\n' "$TAGS_OUTPUT" | grep -q '需要审批'

remote_script "$MASTER_SSH" <<'EOF'
set -euo pipefail
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
printf '{"acls":[]}' > "$TMP_DIR/current.hujson"
printf '{"acls":[],"groups":{"group:smoke":[]}}' > "$TMP_DIR/proposed.hujson"
set +e
ACL_OUTPUT="$(hmn network acl plan --current "$TMP_DIR/current.hujson" --proposed "$TMP_DIR/proposed.hujson" --reload-command 'true' --verify-command 'true' 2>&1)"
ACL_RC=$?
set -e
printf '%s\n' "$ACL_OUTPUT"
[ "$ACL_RC" -ne 0 ]
printf '%s\n' "$ACL_OUTPUT" | grep -q '需要审批'
EOF

log "Headscale bundled / external 真实网络 smoke 通过"
