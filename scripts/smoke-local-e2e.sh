#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -x .venv/bin/python ]; then
  "$PYTHON_BIN" -m venv .venv
fi
.venv/bin/python -m pip install -e '.[dev]' >/dev/null

TMP_ROOT="$(mktemp -d /tmp/hmn-local-e2e.XXXXXX)"
DB="$TMP_ROOT/hmn.db"
DOCS_ROOT="$TMP_ROOT/docs"
SERVICE_ROOT="$TMP_ROOT/service"
RUNBOOK_ROOT="$TMP_ROOT/runbooks"
ENV_FILE="$TMP_ROOT/node.env"
SERVER_LOG="$TMP_ROOT/hmn-server.log"
PORT="${HMN_SMOKE_PORT:-18765}"
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

mkdir -p "$DOCS_ROOT" "$SERVICE_ROOT" "$RUNBOOK_ROOT"
printf '# Local Smoke Restore\n' >"$RUNBOOK_ROOT/restore-local-smoke.md"

log() {
  printf '\n==> %s\n' "$*"
}

run_hmn() {
  HMN_DB="$DB" .venv/bin/hmn "$@"
}

wait_for_http() {
  url="$1"
  attempts=60
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
wait_for_http "http://127.0.0.1:$PORT/healthz"
curl -fsS "http://127.0.0.1:$PORT/api/v1/version" >/dev/null

log "创建 join token 并模拟节点接入"
TOKEN="$(run_hmn token create --trust B --label worker --label smoke --ttl-minutes 15)"
JOIN_RESPONSE="$(curl -fsS -X POST "http://127.0.0.1:$PORT/api/v1/join" \
  -H 'Content-Type: application/json' \
  --data "{\"token\":\"$TOKEN\",\"fingerprint\":\"sha256:smoke-local-e2e\",\"hostname\":\"smoke-node\",\"addresses\":[\"127.0.0.1\"]}")"
echo "$JOIN_RESPONSE"

log "确认节点"
run_hmn node confirm
NODE_ID="$(.venv/bin/python - "$DB" <<'PY'
import sys
from hermes_managed_network.storage import SQLiteStore
nodes = SQLiteStore(sys.argv[1]).list_nodes()
assert len(nodes) == 1, nodes
print(nodes[0].node_id)
PY
)"
echo "NODE_ID=$NODE_ID"

log "创建 worker disabled-exec 任务"
run_hmn task run 'echo smoke-disabled'

cat >"$ENV_FILE" <<EOF
HERMES_MASTER_URL=http://127.0.0.1:$PORT
HERMES_NODE_ID=$NODE_ID
HERMES_NODE_FINGERPRINT=sha256:smoke-local-e2e
HMN_ENABLE_EXEC=0
EOF
chmod 0600 "$ENV_FILE"

log "运行 worker：heartbeat + poll + disabled result"
HMN_ENV_FILE="$ENV_FILE" HMN_ENABLE_EXEC=0 bash scripts/worker.sh

log "验证 worker-status 和任务结果"
run_hmn node worker-status
run_hmn task list
.venv/bin/python - "$DB" <<'PY'
import sys
from hermes_managed_network.storage import SQLiteStore
s = SQLiteStore(sys.argv[1])
tasks = s.list_tasks()
assert len(tasks) == 1, tasks
task = tasks[0]
assert task.status == 'failed', task
assert task.exit_code == 126, task
assert 'execution disabled' in (task.stderr or ''), task.stderr
events = s.list_audit_events()
assert any(event.action == 'heartbeat' and event.outcome == 'ok' for event in events)
print('TASK_STATUS', task.status)
print('TASK_EXIT', task.exit_code)
print('TASK_STDERR', task.stderr)
PY

log "生成资产文档"
run_hmn docs service smoke-service \
  --service-root "$SERVICE_ROOT" \
  --title "Smoke Service" \
  --node smoke-node \
  --url "https://smoke.example.invalid" \
  --summary "Local E2E smoke fixture"
run_hmn docs generate \
  --output-root "$DOCS_ROOT" \
  --service-root "$SERVICE_ROOT" \
  --runbook-root "$RUNBOOK_ROOT"

test -f "$DOCS_ROOT/server/smoke-node/README.md"
test -f "$DOCS_ROOT/server/README.md"
test -f "$SERVICE_ROOT/README.md"
test -f "$SERVICE_ROOT/domains.md"
test -f "$SERVICE_ROOT/runbooks.md"

log "本地 E2E smoke 通过"
