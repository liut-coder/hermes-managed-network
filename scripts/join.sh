#!/usr/bin/env bash
set -euo pipefail

: "${HERMES_JOIN_TOKEN:?HERMES_JOIN_TOKEN is required}"
: "${HERMES_MASTER_URL:?HERMES_MASTER_URL is required}"

HERMES_USER="${HERMES_USER:-hermes}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "join.sh must run as root" >&2
    exit 1
  fi
}

ensure_user() {
  if ! id "$HERMES_USER" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$HERMES_USER"
  fi
}

fingerprint() {
  local machine_id="unknown"
  if [ -r /etc/machine-id ]; then
    machine_id="$(cat /etc/machine-id)"
  fi
  printf '%s:%s' "$(hostname)" "$machine_id" | sha256sum | awk '{print "sha256:"$1}'
}

register_node() {
  local fp
  fp="$(fingerprint)"
  curl -fsS \
    -X POST "${HERMES_MASTER_URL%/}/api/v1/join" \
    -H 'Content-Type: application/json' \
    -d "{\"join_token\":\"$HERMES_JOIN_TOKEN\",\"hostname\":\"$(hostname)\",\"fingerprint\":\"$fp\"}"
}

main() {
  need_root
  ensure_user
  register_node
}

main "$@"
