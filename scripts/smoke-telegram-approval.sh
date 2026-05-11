#!/usr/bin/env bash
set -euo pipefail

# Repeatable real Telegram approval gateway smoke.
# Uses a fresh local HMN controller and a fresh high-risk approval card.
# Required env (values are never printed):
#   HMN_APPROVAL_GATEWAY_TOKEN=<bot-token>
#   HMN_APPROVAL_GATEWAY_TARGET=<chat-id>
# Compatibility fallbacks:
#   HMN_TELEGRAM_BOT_TOKEN
#   HMN_TELEGRAM_CHAT_ID
# Optional env:
#   HMN_SMOKE_PORT=18766
#   HMN_SMOKE_TIMEOUT=180
#   HMN_SMOKE_KEEP=1
#
# The gateway path exercised here calls Telegram getUpdates, forwards the
# callback to HMN, answers via answerCallbackQuery, then clears stale buttons
# with editMessageReplyMarkup after a successful click.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APPROVAL_TOKEN="${HMN_APPROVAL_GATEWAY_TOKEN:-${HMN_TELEGRAM_BOT_TOKEN:-}}"
APPROVAL_TARGET="${HMN_APPROVAL_GATEWAY_TARGET:-${HMN_TELEGRAM_CHAT_ID:-}}"
if [ -z "$APPROVAL_TOKEN" ]; then
  echo "缺少 HMN_APPROVAL_GATEWAY_TOKEN（兼容 HMN_TELEGRAM_BOT_TOKEN）。" >&2
  exit 1
fi
if [ -z "$APPROVAL_TARGET" ]; then
  echo "缺少 HMN_APPROVAL_GATEWAY_TARGET（兼容 HMN_TELEGRAM_CHAT_ID）。" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -e '.[dev]' >/dev/null

TMP_ROOT="$(mktemp -d /tmp/hmn-telegram-approval-smoke.XXXXXX)"
DB="$TMP_ROOT/hmn.db"
SERVER_LOG="$TMP_ROOT/hmn-server.log"
PORT="${HMN_SMOKE_PORT:-18766}"
API_URL="http://127.0.0.1:$PORT"
TIMEOUT_SECONDS="${HMN_SMOKE_TIMEOUT:-180}"
SERVER_PID=""

cleanup() {
  if [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "${HMN_SMOKE_KEEP:-0}" != "1" ]; then
    rm -rf "$TMP_ROOT"
  else
    echo "保留 smoke 目录: $TMP_ROOT"
  fi
}
trap cleanup EXIT

log() {
  printf '\n==> %s\n' "$*"
}

run_hmn() {
  HMN_DB="$DB" .venv/bin/hmn "$@"
}

wait_for_http() {
  local url="$1"
  local attempts=80
  while [ "$attempts" -gt 0 ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 0.5
  done
  echo "服务未就绪: $url" >&2
  echo "--- hmn-server log ---" >&2
  cat "$SERVER_LOG" >&2 || true
  return 1
}

log "启动本地 HMN controller"
HMN_DB="$DB" HMN_HOST=127.0.0.1 HMN_PORT="$PORT" .venv/bin/hmn-server >"$SERVER_LOG" 2>&1 &
SERVER_PID="$!"
wait_for_http "$API_URL/healthz"
curl -fsS "$API_URL/api/v1/version" >/dev/null

log "模拟节点接入"
JOIN_TOKEN="$(run_hmn token create --trust B --label worker --label telegram-smoke --ttl-minutes 15)"
curl -fsS -X POST "$API_URL/api/v1/join" \
  -H 'Content-Type: application/json' \
  --data "{\"token\":\"$JOIN_TOKEN\",\"fingerprint\":\"sha256:telegram-approval-smoke\",\"hostname\":\"telegram-smoke-node\",\"addresses\":[\"127.0.0.1\"]}" >/dev/null
unset JOIN_TOKEN
run_hmn node confirm --bundle observe
NODE_ID="$(.venv/bin/python - "$DB" <<'PY'
import sys
from hermes_managed_network.storage import SQLiteStore
nodes = SQLiteStore(sys.argv[1]).list_nodes()
assert len(nodes) == 1, nodes
print(nodes[0].node_id)
PY
)"
echo "NODE_ID=$NODE_ID"

log "创建 fresh high-risk approval card"
set +e
TASK_OUTPUT="$(run_hmn task run --node "$NODE_ID" --risk high --executor worker 'uptime' 2>&1)"
TASK_RC=$?
set -e
printf '%s\n' "$TASK_OUTPUT"
if [ "$TASK_RC" -eq 0 ]; then
  echo "high-risk task 应进入审批而不是直接创建任务。" >&2
  exit 1
fi
APPROVAL_ID="$(printf '%s\n' "$TASK_OUTPUT" | awk '/需要审批:/ {print $2}' | tail -n 1)"
if [ -z "$APPROVAL_ID" ]; then
  echo "无法解析 approval id。" >&2
  exit 1
fi
echo "APPROVAL_ID=$APPROVAL_ID"
run_hmn approval list --status pending

log "发送 Telegram 审批卡片"
POLL_OUTPUT="$(HMN_API_URL="$API_URL" HMN_APPROVAL_GATEWAY_TARGET="$APPROVAL_TARGET" HMN_APPROVAL_GATEWAY_TOKEN="$APPROVAL_TOKEN" \
  .venv/bin/hmn approval-gateway poll-once --client telegram)"
printf '%s\n' "$POLL_OUTPUT"
printf '%s\n' "$POLL_OUTPUT" | grep -q 'sent=1'

cat <<'MSG'

请点击刚刚收到的这张新 Telegram 审批卡片上的「允许/Approve」按钮。
脚本会继续轮询 callback，直到看到 callbacks=... approved=1。
MSG

log "等待 Telegram callback approve"
DEADLINE=$((SECONDS + TIMEOUT_SECONDS))
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  POLL_OUTPUT="$(HMN_API_URL="$API_URL" HMN_APPROVAL_GATEWAY_TARGET="$APPROVAL_TARGET" HMN_APPROVAL_GATEWAY_TOKEN="$APPROVAL_TOKEN" \
    .venv/bin/hmn approval-gateway poll-once --client telegram)"
  printf '%s\n' "$POLL_OUTPUT"
  if printf '%s\n' "$POLL_OUTPUT" | grep -Eq 'callbacks=[1-9][0-9]*' && printf '%s\n' "$POLL_OUTPUT" | grep -q 'approved=1'; then
    break
  fi
  sleep 3
done

if ! printf '%s\n' "$POLL_OUTPUT" | grep -Eq 'callbacks=[1-9][0-9]*'; then
  echo "未收到 callback。请检查 getUpdates 是否被其他进程消费、chat/token 是否正确。" >&2
  exit 1
fi
printf '%s\n' "$POLL_OUTPUT" | grep -q 'approved=1'

log "验证审批和任务状态"
run_hmn approval list
run_hmn task list
.venv/bin/python - "$DB" "$APPROVAL_ID" <<'PY'
import sys
from hermes_managed_network.storage import SQLiteStore
store = SQLiteStore(sys.argv[1])
approval = store.load_approval_request(sys.argv[2])
assert approval is not None, 'missing approval'
assert approval.status == 'approved', approval
tasks = store.list_tasks()
assert tasks, 'approval did not dispatch task'
assert any(task.command == 'uptime' and task.risk == 'high' for task in tasks), tasks
print('APPROVAL_STATUS', approval.status)
print('TASK_COUNT', len(tasks))
PY

log "Telegram approval gateway smoke 通过"
