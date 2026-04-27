#!/usr/bin/env bash
# Install the agent-otel redaction proxy as a user service.
# Supports macOS (launchd) and Linux (systemd --user). Windows users
# run install.ps1 instead.
#
# What this does:
#   1. Creates ~/.kz-eng-mp/agent-otel/redaction-proxy/{venv,logs}
#   2. Copies redaction-proxy.py + generate-collector-config.py + launcher.sh
#   3. Creates a Python venv and installs proxy deps
#   4. (Optional, prompted) Downloads openai/privacy-filter ONNX weights
#   5. Renders the platform service file from the .tmpl
#   6. Loads + starts the service
#   7. Patches ~/.claude/settings.json["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
#      to point at the proxy
#
# Idempotent: re-running upgrades the proxy code in place. Run uninstall.sh
# to remove the service entirely.
#
# Defaults:
#   - Privacy-filter (PII model) is OFF unless the operator says yes at
#     the prompt or passes --pii-redaction.
#   - Proxy fails closed: if it crashes, Claude Code OTLP fails. This is
#     intentional — the whole point is "secrets never leave the laptop."
#     Service-manager restart will bring it back within ~10s.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROXY_SRC="${REPO_ROOT}/tools/redaction-proxy.py"
PATTERNS_SRC="${REPO_ROOT}/infra/generate-collector-config.py"
LAUNCHER_SRC="${REPO_ROOT}/tools/install/launcher.sh"
SVC_TPL_DIR="${REPO_ROOT}/tools/install/services"

INSTALL_ROOT="${HOME}/.kz-eng-mp/agent-otel"
INSTALL_DIR="${INSTALL_ROOT}/redaction-proxy"
LOG_DIR="${INSTALL_DIR}/logs"

PII=0
NONINTERACTIVE=0
for arg in "$@"; do
  case "$arg" in
    --pii-redaction) PII=1 ;;
    --no-pii)        PII=0; NONINTERACTIVE=1 ;;
    --yes|-y)        NONINTERACTIVE=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
  esac
done

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# ── platform detect ────────────────────────────────────────────────
case "$(uname -s)" in
  Darwin) PLATFORM=macos ;;
  Linux)  PLATFORM=linux ;;
  *)      err "unsupported OS: $(uname -s). Windows users run install.ps1." ;;
esac

# ── prompt for PII redaction ───────────────────────────────────────
if [[ $NONINTERACTIVE -eq 0 && $PII -eq 0 ]]; then
  echo
  echo "Optional: enable PII redaction via openai/privacy-filter ML model."
  echo "  - Adds ~250 ms latency per OTLP record."
  echo "  - Adds ~2.4 GB RSS to the proxy."
  echo "  - Downloads ~400 MB of ONNX weights to ~/.cache/huggingface."
  echo "  - Default categories: secret, account_number, private_address,"
  echo "                        private_phone, private_email"
  echo "    (private_person, private_date, private_url disabled to reduce FPs)."
  echo
  read -r -p "Enable PII redaction? [y/N] " yn
  case "$yn" in [yY]*) PII=1 ;; esac
fi

info "platform: $PLATFORM   pii: $([[ $PII -eq 1 ]] && echo enabled || echo disabled)"
info "install dir: $INSTALL_DIR"

# ── stage files ────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR" "$LOG_DIR"
cp "$PROXY_SRC"    "$INSTALL_DIR/redaction-proxy.py"
cp "$PATTERNS_SRC" "$INSTALL_DIR/generate-collector-config.py"
cp "$LAUNCHER_SRC" "$INSTALL_DIR/launcher.sh"
chmod +x "$INSTALL_DIR/launcher.sh"

# ── venv + deps ────────────────────────────────────────────────────
if [[ ! -x "$INSTALL_DIR/venv/bin/python3" ]]; then
  info "creating venv"
  python3 -m venv "$INSTALL_DIR/venv"
fi
info "installing proxy deps into venv (silent on success)"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet \
  fastapi 'uvicorn[standard]' httpx opentelemetry-proto google-re2

if [[ $PII -eq 1 ]]; then
  info "installing privacy-filter deps (onnxruntime + tokenizers)"
  "$INSTALL_DIR/venv/bin/pip" install --quiet onnxruntime tokenizers numpy huggingface_hub
  info "downloading openai/privacy-filter (q4f16 only, ~400 MB)"
  "$INSTALL_DIR/venv/bin/python3" -c "
from huggingface_hub import snapshot_download
snapshot_download(
    'openai/privacy-filter',
    allow_patterns=['onnx/model_q4f16.onnx', 'tokenizer.json', 'config.json'],
)
print('done')
"
fi

# ── config.env ─────────────────────────────────────────────────────
cat > "$INSTALL_DIR/config.env" <<EOF
# agent-otel redaction proxy runtime config.
# Edit this file then restart the service to apply:
#   macOS:  launchctl kickstart -k gui/\$(id -u)/com.kamiwaza.agent-otel.redaction-proxy
#   linux:  systemctl --user restart agent-otel-redaction-proxy

REDACTION_PROXY_LISTEN=127.0.0.1:4319
REDACTION_PROXY_UPSTREAM=https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io
REDACTION_PROXY_PRIVACY_FILTER=$PII
REDACTION_PROXY_PF_QUANT=q4f16
REDACTION_PROXY_PF_CATEGORIES=secret,account_number,private_address,private_phone,private_email
EOF

# ── render + install service file ──────────────────────────────────
render() {
  sed \
    -e "s|{{INSTALL_DIR}}|$INSTALL_DIR|g" \
    -e "s|{{LAUNCHER}}|$INSTALL_DIR/launcher.sh|g" \
    -e "s|{{LOG_DIR}}|$LOG_DIR|g" \
    "$1"
}

case $PLATFORM in
macos)
  PLIST="$HOME/Library/LaunchAgents/com.kamiwaza.agent-otel.redaction-proxy.plist"
  mkdir -p "$(dirname "$PLIST")"
  render "$SVC_TPL_DIR/com.kamiwaza.agent-otel.redaction-proxy.plist.tmpl" > "$PLIST"
  # bootout first to handle re-install (idempotent — ignore failure if not loaded)
  launchctl bootout "gui/$(id -u)/com.kamiwaza.agent-otel.redaction-proxy" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  info "loaded launchd agent: $PLIST"
  ;;
linux)
  UNIT_DIR="$HOME/.config/systemd/user"
  UNIT="$UNIT_DIR/agent-otel-redaction-proxy.service"
  mkdir -p "$UNIT_DIR"
  render "$SVC_TPL_DIR/agent-otel-redaction-proxy.service.tmpl" > "$UNIT"
  systemctl --user daemon-reload
  systemctl --user enable --now agent-otel-redaction-proxy.service
  info "enabled+started systemd unit: $UNIT"
  info "to keep running after logout: sudo loginctl enable-linger $USER"
  ;;
esac

# ── settings.json wiring ───────────────────────────────────────────
SETTINGS="$HOME/.claude/settings.json"
if [[ -f "$SETTINGS" ]]; then
  if command -v jq >/dev/null; then
    cp "$SETTINGS" "$SETTINGS.bak-$(date +%s)"
    tmp=$(mktemp)
    jq '.env.OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4319"' "$SETTINGS" > "$tmp"
    mv "$tmp" "$SETTINGS"
    info "patched $SETTINGS to use proxy endpoint (backup taken)"
  else
    info "WARN: jq not installed — manually set OTEL_EXPORTER_OTLP_ENDPOINT to http://127.0.0.1:4319 in $SETTINGS"
  fi
fi

echo
info "Installed. Health check:"
echo "  curl -s http://127.0.0.1:4319/healthz | jq"
echo
info "Tail logs:"
echo "  tail -f $LOG_DIR/redaction-proxy.{out,err}.log"
echo
info "Uninstall:  $REPO_ROOT/tools/install/uninstall.sh"
