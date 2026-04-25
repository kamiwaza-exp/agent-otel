#!/usr/bin/env bash
# Upload one or more dashboard JSON files to an Azure Managed Grafana
# instance. Pre-processes each file to:
#   - drop __inputs / __requires (those are for the manual import wizard)
#   - replace every "uid": "${DS_AZURE_MONITOR}" with the actual Azure
#     Monitor data source UID resolved from Grafana
#   - bake the LA workspace resource ID into the law_resource textbox
#     default so users don't have to paste it after upload
#   - clear top-level "id" so Grafana keys by uid (overwrite by uid)
#
# Usage:
#   tools/upload-dashboards.sh                                # upload all *.azure.json from repo root
#   tools/upload-dashboards.sh claude-code-team-adoption.azure.json
#   tools/upload-dashboards.sh path1.json path2.json
#
# Override defaults via env vars:
#   GRAFANA_NAME    Azure Managed Grafana resource name (default: agent-otel-grafana)
#   RESOURCE_GROUP  RG that contains both Grafana and the LA workspace (default: agent-otel-rg)
#   LA_WORKSPACE    LA workspace name to bake into law_resource (default: agent-otel-law)
#   FOLDER_UID      Grafana folder uid to upload into (default: empty = root)
#   OVERWRITE       "true" (default) | "false"
set -euo pipefail

GRAFANA_NAME="${GRAFANA_NAME:-agent-otel-grafana}"
RESOURCE_GROUP="${RESOURCE_GROUP:-agent-otel-rg}"
LA_WORKSPACE="${LA_WORKSPACE:-agent-otel-law}"
FOLDER_UID="${FOLDER_UID:-}"
OVERWRITE="${OVERWRITE:-true}"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "$*"; }

# ── deps ─────────────────────────────────────────────────────────────
command -v az >/dev/null || err "az CLI not found. Install from https://aka.ms/installazcli"
command -v jq >/dev/null || err "jq not found. brew install jq (or apt-get install jq)"

# The `az grafana ...` command group is provided by the `amg` extension
# (Azure Managed Grafana). Probe the command directly rather than asking
# about a specific extension name — works whether amg is installed or
# bundled in a future az release.
if ! az grafana --help >/dev/null 2>&1; then
  info "az grafana command group not available; installing the 'amg' extension..."
  az extension add -n amg --only-show-errors
fi

# ── resolve Grafana datasource UID + LA workspace resource ID ────────
info "Resolving Azure Monitor data source UID in Grafana '$GRAFANA_NAME'..."
DS_UID=$(az grafana data-source list \
  -n "$GRAFANA_NAME" -g "$RESOURCE_GROUP" \
  --query "[?type=='grafana-azure-monitor-datasource'].uid | [0]" -o tsv 2>/dev/null || true)
[[ -n $DS_UID ]] || err "No Azure Monitor data source found in Grafana '$GRAFANA_NAME'. Add one in Grafana Connections → Data sources before re-running."

info "Resolving LA workspace resource ID for '$LA_WORKSPACE'..."
LA_RESOURCE_ID=$(az monitor log-analytics workspace show \
  -g "$RESOURCE_GROUP" -n "$LA_WORKSPACE" --query id -o tsv 2>/dev/null || true)
[[ -n $LA_RESOURCE_ID ]] || err "LA workspace '$LA_WORKSPACE' not found in '$RESOURCE_GROUP'."

info ""
info "Grafana:        $GRAFANA_NAME (rg=$RESOURCE_GROUP)"
info "Datasource UID: $DS_UID"
info "LA resource:    $LA_RESOURCE_ID"
[[ -n $FOLDER_UID ]] && info "Folder UID:     $FOLDER_UID"
info ""

# ── enumerate files ──────────────────────────────────────────────────
declare -a files
if [[ $# -gt 0 ]]; then
  files=("$@")
else
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")
  # bash 3.2 (macOS default) lacks mapfile; glob expansion gives
  # alphabetical order on POSIX globs by default, which is what we want.
  shopt -s nullglob
  files=( "$repo_root"/*.azure.json )
  shopt -u nullglob
  [[ ${#files[@]} -gt 0 ]] || err "No *.azure.json in $repo_root and no files passed on the command line."
fi

# ── upload one ───────────────────────────────────────────────────────
upload_one() {
  local src="$1"
  [[ -f $src ]] || { info "  ✗ $src — not a file"; return 1; }

  local processed
  processed=$(mktemp -t agent-otel-dash.XXXXXX.json)
  # Cleanup the temp file when this function returns regardless of outcome.
  trap 'rm -f "$processed"' RETURN

  jq \
    --arg ds "$DS_UID" \
    --arg la "$LA_RESOURCE_ID" \
    '
      del(.__inputs, .__requires)
      | .id = null
      | walk(
          if type == "object" and (.uid // "") == "${DS_AZURE_MONITOR}"
          then .uid = $ds
          else .
          end)
      | (.templating.list // []) |= map(
          if .name == "law_resource"
          then . + {
                 query: $la,
                 current: { selected: false, text: $la, value: $la }
               }
          else .
          end)
    ' "$src" > "$processed"

  local title uid
  title=$(jq -r '.title' "$processed")
  uid=$(jq -r '.uid' "$processed")

  info "Uploading: $title (uid=$uid)"

  local args=(--name "$GRAFANA_NAME" --resource-group "$RESOURCE_GROUP" --definition "@$processed")
  [[ $OVERWRITE == "true" ]] && args+=(--overwrite true)
  [[ -n $FOLDER_UID ]]       && args+=(--folder "$FOLDER_UID")

  if az grafana dashboard create "${args[@]}" -o none 2>&1 | tail -5; then
    info "  ✓ uploaded"
  else
    local rc=$?
    info "  ✗ failed (rc=$rc)"
    return 1
  fi
}

failures=0
for f in "${files[@]}"; do
  upload_one "$f" || failures=$((failures + 1))
done

if [[ $failures -gt 0 ]]; then
  err "$failures dashboard(s) failed to upload."
fi
info ""
info "Done. ${#files[@]} dashboard(s) processed."
