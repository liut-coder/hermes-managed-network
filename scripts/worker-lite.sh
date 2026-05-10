#!/bin/sh
# Hermes Managed Network POSIX lite worker.
# Runs with /bin/sh and intentionally avoids bash, python, and jq.
# Security policy: heartbeat-only. This lite worker does not poll tasks and
# never executes controller-provided shell commands. Install the full worker.sh
# only on nodes that need signed task handling.
set -eu

HMN_DIR=${HMN_DIR:-/etc/hermes-managed-network}
ENV_FILE=${HMN_ENV_FILE:-$HMN_DIR/node.env}
HMN_WORKER_PROTOCOL_VERSION=${HMN_WORKER_PROTOCOL_VERSION:-0.1}
HMN_WORKER_VERSION=${HMN_WORKER_VERSION:-posix-lite}
HMN_ONCE=${HMN_ONCE:-1}
HMN_HEARTBEAT_INTERVAL=${HMN_HEARTBEAT_INTERVAL:-60}

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

# Keep JSON generation dependency-free by accepting the safe token format used
# by HMN URLs, node ids, fingerprints, and versions. Values outside this set are
# rejected instead of being interpolated unsafely.
json_safe_string() {
  case $1 in
    *[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:._/@%+=,-]*|'')
      fail "unsafe JSON string value"
      ;;
  esac
  printf '"%s"' "$1"
}

load_env() {
  [ -r "$ENV_FILE" ] || fail "missing node env: $ENV_FILE"
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  : "${HERMES_MASTER_URL:?missing HERMES_MASTER_URL}"
  : "${HERMES_NODE_ID:?missing HERMES_NODE_ID}"
  : "${HERMES_NODE_FINGERPRINT:?missing HERMES_NODE_FINGERPRINT}"
}

collect_lite_facts() {
  printf '{'
  printf '"worker_protocol_version":%s,' "$(json_safe_string "$HMN_WORKER_PROTOCOL_VERSION")"
  printf '"worker_version":%s,' "$(json_safe_string "$HMN_WORKER_VERSION")"
  printf '"worker_variant":"posix-lite",'
  printf '"exec_enabled":false,'
  printf '"task_policy":"heartbeat-only",'
  printf '"capabilities":{"has_sh":true,"requires_bash":false,"requires_python":false,"requires_jq":false}'
  printf '}'
}

heartbeat() {
  fingerprint_json=$(json_safe_string "$HERMES_NODE_FINGERPRINT")
  facts=$(collect_lite_facts)
  curl -fsS -X POST "${HERMES_MASTER_URL%/}/api/v1/nodes/${HERMES_NODE_ID}/heartbeat" \
    -H 'Content-Type: application/json' \
    --data "{\"fingerprint\":${fingerprint_json},\"status\":\"ok\",\"facts\":${facts}}" >/dev/null
}

main() {
  need_command curl
  load_env
  while :; do
    heartbeat
    [ "$HMN_ONCE" = "0" ] || break
    sleep "$HMN_HEARTBEAT_INTERVAL"
  done
}

main "$@"
