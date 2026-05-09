#!/usr/bin/env bash
set -euo pipefail

HMN_DIR="${HMN_DIR:-/etc/hermes-managed-network}"
ENV_FILE="${HMN_ENV_FILE:-$HMN_DIR/node.env}"
: "${HMN_ENABLE_EXEC:=0}"
HMN_WORKER_PROTOCOL_VERSION="${HMN_WORKER_PROTOCOL_VERSION:-0.1}"

if [ ! -r "$ENV_FILE" ]; then
  echo "missing node env: $ENV_FILE" >&2
  exit 1
fi
. "$ENV_FILE"
: "${HERMES_MASTER_URL:?missing HERMES_MASTER_URL}"
: "${HERMES_NODE_ID:?missing HERMES_NODE_ID}"
: "${HERMES_NODE_FINGERPRINT:?missing HERMES_NODE_FINGERPRINT}"

need_command() { command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 1; }; }
need_command curl
need_command python3

json_escape() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }

heartbeat() {
  curl -fsS -X POST "${HERMES_MASTER_URL%/}/api/v1/nodes/${HERMES_NODE_ID}/heartbeat"     -H 'Content-Type: application/json'     --data "{"fingerprint":"${HERMES_NODE_FINGERPRINT}","status":"ok","facts":{"worker_protocol_version":"${HMN_WORKER_PROTOCOL_VERSION}"}}" >/dev/null
}

poll_task() {
  curl -fsS -X POST "${HERMES_MASTER_URL%/}/api/v1/nodes/${HERMES_NODE_ID}/tasks/next"     -H 'Content-Type: application/json'     --data "{"fingerprint":"${HERMES_NODE_FINGERPRINT}","worker_protocol_version":"${HMN_WORKER_PROTOCOL_VERSION}"}"
}

submit_result() {
  local task_id="$1" exit_code="$2" stdout_json stderr_json
  stdout_json="$(printf '%s' "$3" | json_escape)"
  stderr_json="$(printf '%s' "$4" | json_escape)"
  curl -fsS -X POST "${HERMES_MASTER_URL%/}/api/v1/tasks/${task_id}/result"     -H 'Content-Type: application/json'     --data "{"fingerprint":"${HERMES_NODE_FINGERPRINT}","exit_code":${exit_code},"stdout":${stdout_json},"stderr":${stderr_json}}" >/dev/null
}

run_once() {
  heartbeat
  local response task_id command stdout_file stderr_file exit_code
  response="$(poll_task)"
  task_id="$(printf '%s' "$response" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("task_id", ""))')"
  [ -z "$task_id" ] && exit 0
  command="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["command"])')"
  if [ "$HMN_ENABLE_EXEC" != "1" ]; then
    submit_result "$task_id" 126 "" "execution disabled; set HMN_ENABLE_EXEC=1"
    exit 0
  fi
  stdout_file="$(mktemp)"; stderr_file="$(mktemp)"
  set +e
  bash -lc "$command" >"$stdout_file" 2>"$stderr_file"
  exit_code=$?
  set -e
  submit_result "$task_id" "$exit_code" "$(cat "$stdout_file")" "$(cat "$stderr_file")"
  rm -f "$stdout_file" "$stderr_file"
}

run_once "$@"
