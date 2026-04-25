# Azure Infrastructure — Deploy Walkthrough

Bicep templates implementing the Azure deployment described in
[`../docs/azure-deployment-design.md`](../docs/azure-deployment-design.md).

| File | Milestone | What it creates |
|---|---|---|
| `main.bicep` | M1 | Managed Identity, Azure Monitor Workspace, Log Analytics + custom table, DCE, DCRs, Container Apps Environment, Managed Grafana |
| `collector-app.bicep` | M3 | OTel Collector Container App with IP-restricted ingress |
| `generate-azure-dashboard.py` | M4 | Produces `../claude-code-dashboard.azure.json` from the local dashboard — rerun after any local-dashboard edit to keep Azure in sync |

Switching from the local Docker Compose stack to this Azure deployment is telemetry-destination only — Claude Code clients keep emitting OTLP, they just point at a different endpoint.

---

## Prerequisites

- **Azure CLI** with Bicep: `az version` should show `"azure-cli": "2.50+"` and `"Bicep CLI version": "0.20+"`.
- **A subscription** where you can create resource groups and role assignments (Contributor + User Access Administrator, or Owner).
- **Your external IP** — used for the collector ingress allowlist:
  ```bash
  curl -s ifconfig.me
  ```

---

## Step 1 — Resource Group

```bash
export RG=claude-obs-rg
export LOCATION=eastus2
export BASE=claude-obs

az group create --name "$RG" --location "$LOCATION"
```

---

## Step 2 — Deploy M1 (backends)

```bash
az deployment group create \
  --resource-group "$RG" \
  --template-file main.bicep \
  --parameters \
      baseName="$BASE" \
      location="$LOCATION" \
      logRetentionDays=30 \
      logDailyCapGb=1
```

Expect ~5–8 minutes (Managed Grafana is the slow one).

### Grab the outputs

```bash
az deployment group show \
  --resource-group "$RG" \
  --name main \
  --query properties.outputs \
  --output json > m1-outputs.json

cat m1-outputs.json | jq -r '.metricsRemoteWriteUrl.value'
cat m1-outputs.json | jq -r '.logsOtlpUrl.value'
cat m1-outputs.json | jq -r '.grafanaEndpoint.value'
```

---

## Step 3 — Deploy M3 (collector)

Compose the parameters from your external IP and the M1 outputs:

```bash
export MY_IP=$(curl -s ifconfig.me)
export METRICS_URL=$(jq -r '.metricsRemoteWriteUrl.value' m1-outputs.json)
export LOGS_URL=$(jq -r '.logsOtlpUrl.value' m1-outputs.json)

az deployment group create \
  --resource-group "$RG" \
  --template-file collector-app.bicep \
  --parameters \
      baseName="$BASE" \
      location="$LOCATION" \
      allowedClientCidrs="['${MY_IP}/32']" \
      metricsRemoteWriteUrl="$METRICS_URL" \
      logsOtlpUrl="$LOGS_URL"
```

The Container App is deployed as a single replica with `minReplicas=1` / `maxReplicas=1` — always warm, no cold-start packet loss.

### Grab the collector endpoint

```bash
az deployment group show \
  --resource-group "$RG" \
  --name collector-app \
  --query properties.outputs.otlpHttpEndpoint.value \
  --output tsv
```

---

## Step 3b — Import the dashboard (M4)

1. Open the Managed Grafana URL (from `m1-outputs.json` → `grafanaEndpoint`).
2. Sign in with your Entra ID credentials.
3. **Dashboards** → **New** → **Import**.
4. Upload `../claude-code-dashboard.azure.json`.
5. On the import form, supply:
   - **DS_PROMETHEUS**: the auto-provisioned Managed Prometheus data source (picked from dropdown).
   - **DS_AZURE_MONITOR**: the auto-provisioned Azure Monitor data source.
6. After import, click the dashboard's **Settings → Variables** and set `law_resource` to the Log Analytics workspace resource ID from `m1-outputs.json` → `logAnalyticsWorkspaceId`.

Regenerating the dashboard after editing the local version:

```bash
python3 infra/generate-azure-dashboard.py
```

---

## Step 4 — Point Claude Code at it

Set these env vars (or persist in `~/.claude/settings.json`):

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=https://<collector-fqdn-from-step-3>
```

Note: we send HTTP/protobuf (not gRPC) because Container Apps ingress speaks HTTP/2 out of the box; gRPC requires TCP ingress with extra setup.

Run `claude` for a session, then hit Managed Grafana at the URL from the M1 output to verify data is flowing.

---

## Step 5 — Refresh your IP in the allowlist

If your external IP changes, the collector will silently reject your traffic. Update the allowlist without a full redeploy:

```bash
export MY_IP=$(curl -s ifconfig.me)
az deployment group create \
  --resource-group "$RG" \
  --template-file collector-app.bicep \
  --parameters \
      baseName="$BASE" \
      location="$LOCATION" \
      allowedClientCidrs="['${MY_IP}/32']" \
      metricsRemoteWriteUrl="$METRICS_URL" \
      logsOtlpUrl="$LOGS_URL"
```

Only the `ipSecurityRestrictions` list changes; the Container App revision rolls over in seconds.

For multiple CIDRs (home + office, shared VPN):

```bash
--parameters allowedClientCidrs="['203.0.113.42/32','198.51.100.7/32']"
```

---

## Troubleshooting

### No data in Grafana

1. **Check the collector is up**: portal → Container Apps → `claude-obs-collector` → Log stream. Look for "Everything is ready. Begin running and processing data."
2. **Check ingress rejection**: from a client, `curl -v https://<fqdn>/v1/metrics` — a 403 means your IP is blocked (refresh the allowlist, step 5); a 405 means allowlist passed but the method was wrong (expected — the endpoint accepts POST only).
3. **Check DCE auth**: collector logs will show `401 Unauthorized` against the DCE if the Managed Identity role assignment hasn't propagated. Wait 5 min and retry.

### High ingestion cost

Log Analytics has a daily cap (`logDailyCapGb` param, default 1 GB). When hit, ingestion pauses and Claude Code events are dropped until midnight UTC. Raise with:

```bash
az monitor log-analytics workspace update \
  --resource-group "$RG" \
  --workspace-name "${BASE}-law" \
  --quota 5
```

### Teardown

```bash
az group delete --name "$RG" --yes
```

This removes every resource including logs and metrics history. There is no undo.

---

## What's not covered here

- **Dashboard import** (M4): export `claude-code-dashboard.json`, swap datasource UIDs to the Managed Grafana ones, rewrite the Loki panels per [`../docs/azure-kql-panels.md`](../docs/azure-kql-panels.md), import.
- **Private endpoint for Grafana** (future hardening): currently Grafana is gated by Entra auth, accessible from any IP. Adding a private endpoint requires a VNet and is outside v1 scope.
- **Multi-region / HA**: single-region by design.
