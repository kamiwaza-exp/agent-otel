#!/usr/bin/env bash
# Launcher for the agent-otel redaction proxy on macOS / Linux.
#
# Invoked by the platform service manager (launchd / systemd --user).
# Reads $INSTALL_DIR/config.env (written by install.sh) for runtime
# settings, activates the bundled venv, and execs the proxy. Service
# manager is responsible for restart-on-failure; this script just sets
# up the environment and execs.
#
# Layout assumed:
#   $INSTALL_DIR/config.env           — port, categories, upstream
#   $INSTALL_DIR/venv/bin/python3     — venv with proxy deps
#   $INSTALL_DIR/redaction-proxy.py   — proxy script (copied at install)

set -euo pipefail

# Resolve our install dir from the script location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SCRIPT_DIR}"

# Source operator-edited config (port, categories, upstream URL).
# config.env is sourced as bash, so values can use shell syntax if needed.
if [[ -f "${INSTALL_DIR}/config.env" ]]; then
  # shellcheck disable=SC1091
  source "${INSTALL_DIR}/config.env"
fi

LISTEN="${REDACTION_PROXY_LISTEN:-127.0.0.1:4319}"
UPSTREAM="${REDACTION_PROXY_UPSTREAM:-https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io}"
PF_FLAG="${REDACTION_PROXY_PRIVACY_FILTER:-0}"
PF_QUANT="${REDACTION_PROXY_PF_QUANT:-q4f16}"
PF_CATEGORIES="${REDACTION_PROXY_PF_CATEGORIES:-secret,account_number,private_address,private_phone,private_email}"

ARGS=(
  --listen "${LISTEN}"
  --upstream "${UPSTREAM}"
)
if [[ "${PF_FLAG}" == "1" ]]; then
  ARGS+=(--privacy-filter --quant "${PF_QUANT}" --pf-categories "${PF_CATEGORIES}")
fi

exec "${INSTALL_DIR}/venv/bin/python3" "${INSTALL_DIR}/redaction-proxy.py" "${ARGS[@]}"
