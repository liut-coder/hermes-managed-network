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
json_value() { printf '%s' "$1" | json_escape; }

collect_worker_facts() {
  HMN_ENABLE_EXEC_VALUE="$HMN_ENABLE_EXEC" HMN_WORKER_PROTOCOL_VERSION_VALUE="$HMN_WORKER_PROTOCOL_VERSION" python3 - <<'PY'
import json
import os


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def load_average():
    text = read_text("/proc/loadavg")
    if text:
        parts = text.split()
        if len(parts) >= 3:
            return {"1m": parts[0], "5m": parts[1], "15m": parts[2]}
    try:
        one, five, fifteen = os.getloadavg()
        return {"1m": f"{one:.2f}", "5m": f"{five:.2f}", "15m": f"{fifteen:.2f}"}
    except (AttributeError, OSError):
        return {}


def uptime_seconds():
    text = read_text("/proc/uptime")
    if not text:
        return None
    try:
        return int(float(text.split()[0]))
    except (IndexError, ValueError):
        return None


def memory_summary():
    text = read_text("/proc/meminfo")
    values = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0])
    if not values:
        return {}
    return {
        "total_kb": values.get("MemTotal"),
        "available_kb": values.get("MemAvailable"),
        "free_kb": values.get("MemFree"),
    }


def disk_summary(path="/"):
    try:
        usage = os.statvfs(path)
    except OSError:
        return {}
    total = usage.f_blocks * usage.f_frsize
    free = usage.f_bavail * usage.f_frsize
    used = total - free
    return {"path": path, "total_bytes": total, "used_bytes": used, "free_bytes": free}

facts = {
    "worker_protocol_version": os.environ.get("HMN_WORKER_PROTOCOL_VERSION_VALUE", ""),
    "worker_version": os.environ.get("HMN_WORKER_VERSION", "unknown"),
    "exec_enabled": os.environ.get("HMN_ENABLE_EXEC") == "1",
    "uptime": {"seconds": uptime_seconds()},
    "load_average": load_average(),
    "memory": memory_summary(),
    "disk": disk_summary("/"),
}
print(json.dumps(facts, separators=(",", ":")))
PY
}

heartbeat() {
  local facts fingerprint_json
  facts="$(collect_worker_facts)"
  fingerprint_json="$(json_value "$HERMES_NODE_FINGERPRINT")"
  curl -fsS -X POST "${HERMES_MASTER_URL%/}/api/v1/nodes/${HERMES_NODE_ID}/heartbeat"     -H 'Content-Type: application/json'     --data "{\"fingerprint\":${fingerprint_json},\"status\":\"ok\",\"facts\":${facts}}" >/dev/null
}

poll_task() {
  local fingerprint_json protocol_version_json
  fingerprint_json="$(json_value "$HERMES_NODE_FINGERPRINT")"
  protocol_version_json="$(json_value "$HMN_WORKER_PROTOCOL_VERSION")"
  curl -fsS -X POST "${HERMES_MASTER_URL%/}/api/v1/nodes/${HERMES_NODE_ID}/tasks/next"     -H 'Content-Type: application/json'     --data "{\"fingerprint\":${fingerprint_json},\"worker_protocol_version\":${protocol_version_json}}"
}

submit_result() {
  local task_id="$1" exit_code="$2" fingerprint_json stdout_json stderr_json
  fingerprint_json="$(json_value "$HERMES_NODE_FINGERPRINT")"
  stdout_json="$(json_value "$3")"
  stderr_json="$(json_value "$4")"
  curl -fsS -X POST "${HERMES_MASTER_URL%/}/api/v1/tasks/${task_id}/result"     -H 'Content-Type: application/json'     --data "{\"fingerprint\":${fingerprint_json},\"exit_code\":${exit_code},\"stdout\":${stdout_json},\"stderr\":${stderr_json}}" >/dev/null
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
