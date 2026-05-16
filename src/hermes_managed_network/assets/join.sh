#!/usr/bin/env bash
set -euo pipefail

: "${HERMES_JOIN_TOKEN:?HERMES_JOIN_TOKEN is required}"
: "${HERMES_MASTER_URL:?HERMES_MASTER_URL is required}"

HERMES_USER="${HERMES_USER:-hermes}"
HERMES_DIR="${HERMES_DIR:-/etc/hermes-managed-network}"
HERMES_AUTO_CONFIRM="${HERMES_AUTO_CONFIRM:-1}"
HERMES_AUTO_INSTALL_WORKER="${HERMES_AUTO_INSTALL_WORKER:-1}"
HMN_ENABLE_EXEC="${HMN_ENABLE_EXEC:-1}"

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "join.sh must run as root" >&2
    exit 1
  fi
}

need_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'
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

addresses_json() {
  python3 - <<'PY'
import json
import socket
addresses = []
for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
    if family in (socket.AF_INET, socket.AF_INET6):
        address = sockaddr[0]
        if not address.startswith("127.") and address != "::1" and address not in addresses:
            addresses.append(address)
print(json.dumps(addresses))
PY
}

auto_confirm_json() {
  if [ "$HERMES_AUTO_CONFIRM" = "0" ]; then
    printf 'false'
  else
    printf 'true'
  fi
}

register_node() {
  local fp hostname_json token_json addresses auto_confirm response node_id status trust_level
  fp="$(fingerprint)"
  hostname_json="$(printf '%s' "${HERMES_NODE_NAME:-$(hostname)}" | json_escape)"
  token_json="$(printf '%s' "$HERMES_JOIN_TOKEN" | json_escape)"
  addresses="$(addresses_json)"
  auto_confirm="$(auto_confirm_json)"

  response="$(curl -fsS \
    -X POST "${HERMES_MASTER_URL%/}/api/v1/join" \
    -H 'Content-Type: application/json' \
    -d "{\"token\":${token_json},\"hostname\":${hostname_json},\"fingerprint\":\"${fp}\",\"addresses\":${addresses},\"auto_confirm\":${auto_confirm}}")"

  node_id="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])')"
  status="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  trust_level="$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["trust_level"])')"

  install -d -m 0700 "$HERMES_DIR"
  cat >"$HERMES_DIR/node.env" <<EOF
HERMES_MASTER_URL=${HERMES_MASTER_URL%/}
HERMES_NODE_ID=${node_id}
HERMES_NODE_FINGERPRINT=${fp}
HERMES_TRUST_LEVEL=${trust_level}
HERMES_STATUS=${status}
HMN_ENABLE_EXEC=${HMN_ENABLE_EXEC}
EOF
  chmod 0600 "$HERMES_DIR/node.env"
  echo "joined node_id=${node_id} status=${status} trust=${trust_level}"
}

install_worker() {
  if [ "$HERMES_AUTO_INSTALL_WORKER" = "0" ]; then
    echo "worker auto-install skipped (HERMES_AUTO_INSTALL_WORKER=0)"
    return 0
  fi
  need_command systemctl
  curl -fsSL "${HERMES_MASTER_URL%/}/scripts/worker.sh" -o /usr/local/bin/hmn-worker
  chmod 0755 /usr/local/bin/hmn-worker
  cat >/etc/systemd/system/hermes-managed-network-heartbeat.service <<'EOF'
[Unit]
Description=Hermes Managed Network worker heartbeat
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/hermes-managed-network/node.env
ExecStart=/usr/local/bin/hmn-worker
EOF
  cat >/etc/systemd/system/hermes-managed-network-heartbeat.timer <<'EOF'
[Unit]
Description=Run Hermes Managed Network worker heartbeat periodically

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=10s
Unit=hermes-managed-network-heartbeat.service

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now hermes-managed-network-heartbeat.timer
  echo "worker installed: hermes-managed-network-heartbeat.timer"
}

main() {
  need_root
  need_command curl
  need_command python3
  need_command sha256sum
  ensure_user
  register_node
  install_worker
}

main "$@"
