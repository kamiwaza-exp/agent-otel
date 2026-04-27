#!/usr/bin/env bash
# Remove the agent-otel redaction proxy service. macOS / Linux. The
# install dir, venv, and logs are removed too unless --keep-data is passed.
#
# Restores ~/.claude/settings.json["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"]
# to the cloud collector URL — direct OTLP, no local redaction.

set -euo pipefail

KEEP=0
for arg in "$@"; do
  case "$arg" in
    --keep-data) KEEP=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
  esac
done

INSTALL_DIR="$HOME/.kz-eng-mp/agent-otel/redaction-proxy"
info() { echo "==> $*"; }

case "$(uname -s)" in
Darwin)
  PLIST="$HOME/Library/LaunchAgents/com.kamiwaza.agent-otel.redaction-proxy.plist"
  launchctl bootout "gui/$(id -u)/com.kamiwaza.agent-otel.redaction-proxy" 2>/dev/null || true
  rm -f "$PLIST"
  info "removed launchd agent"
  ;;
Linux)
  systemctl --user disable --now agent-otel-redaction-proxy.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/agent-otel-redaction-proxy.service"
  systemctl --user daemon-reload || true
  info "removed systemd user unit"
  ;;
*) echo "unsupported OS: $(uname -s)"; exit 1 ;;
esac

if [[ $KEEP -eq 0 ]]; then
  rm -rf "$INSTALL_DIR"
  info "removed $INSTALL_DIR"
else
  info "kept install dir at $INSTALL_DIR (--keep-data)"
fi

SETTINGS="$HOME/.claude/settings.json"
if [[ -f "$SETTINGS" ]] && command -v jq >/dev/null; then
  cp "$SETTINGS" "$SETTINGS.bak-$(date +%s)"
  tmp=$(mktemp)
  jq '.env.OTEL_EXPORTER_OTLP_ENDPOINT = "https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io"' \
     "$SETTINGS" > "$tmp"
  mv "$tmp" "$SETTINGS"
  info "restored cloud endpoint in $SETTINGS"
fi

info "Done. To re-install: tools/install/install.sh"
