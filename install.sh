#!/usr/bin/env bash
set -euo pipefail

export HMN_PACKAGE="${HMN_PACKAGE:-git+https://github.com/liut-coder/hermes-managed-network.git@main}"
export HMN_HOST="${HMN_HOST:-0.0.0.0}"
export HMN_PORT="${HMN_PORT:-8765}"
export HMN_DB="${HMN_DB:-/var/lib/hermes-managed-network/control-plane.db}"

SCRIPT_URL="${HMN_INSTALL_MASTER_URL:-https://raw.githubusercontent.com/liut-coder/hermes-managed-network/main/scripts/install-master.sh}"

curl -fsSL "$SCRIPT_URL" | bash
