# Azure Deployment Design — Claude Code Observability Stack

**Status:** Draft · **Date:** 2026-04-24 · **Author:** John Stanford

> Lightweight design doc. If any section needs the full treatment (traceable UCs, detailed sequences, implementation plan with tasks), we can expand it.

---

## 1. Context

Today the observability stack runs as four Docker Compose services (OTel Collector, Prometheus, Loki, Grafana) per the repo's `docker-compose.yml`. We want to move it to Azure **without running a VM** and without operating stateful backends ourselves.

## 2. Goals / Non-Goals

**Goals**
- Zero VM ops. Managed services for metrics storage, logs storage, and dashboards.
- Preserve the current Claude Code telemetry data flow (clients still emit OTLP; endpoint is just moved).
- Keep monthly cost predictable at personal / small-team volume.
- Preserve dashboard semantics where practical (metric panels should port; log panels will be rewritten).

**Non-Goals**
- High availability across regions.
- Multi-tenancy or per-team isolation.
- Long retention beyond Azure Monitor defaults.
- Ingesting traces (Claude Code doesn't emit them today).

## 3. Proposed Architecture

```
  Claude Code clients (laptops, CI runners)
          │  OTLP/gRPC or OTLP/HTTP  (with auth header)
          ▼
  ┌─────────────────────────────────────────────────────┐
  │  Azure Container App: otel-collector                │
  │    receivers:  otlp (http on 4318)                  │
  │    processors: memory_limiter, resource, batch      │
  │    exporters:                                       │
  │      - prometheusremotewrite → DCE (metrics, MI auth)│
  │      - azuremonitor          → AI (connstring auth) │
  └───────────────┬─────────────────────────────────────┘
                  │
      ┌───────────┴────────────┐
      ▼                        ▼
  Azure Monitor          Application Insights
  Workspace              (workspace-based,
  (Managed Prom)          SamplingPercentage=100)
      │                        │
      │                        ▼
      │              Log Analytics Workspace
      │              (auto-provisioned AppTraces
      │               table — physical store
      └──────────┬──── for AI ingestion)
                 ▼
       Azure Managed Grafana (Essential SKU)
       (Managed Prom + Azure Monitor Logs data sources)
```

## 4. Components

| Component | Azure service | Purpose |
|---|---|---|
| **Collector** | Container Apps (single app, one replica, consumption plan) | OTLP ingest, routing, auth enforcement |
| **Metrics backend** | Azure Monitor Workspace + Data Collection Endpoint (DCE) | Managed Prometheus — PromQL-compatible storage |
| **Logs/events backend** | Application Insights (workspace-based, `SamplingPercentage=100`) → Log Analytics Workspace (`AppTraces` table, auto-provisioned) | Ingest Claude Code event logs via the `azuremonitor` exporter; queryable via KQL. AI is the ingestion door, LA is the physical store |
| **Dashboards** | Azure Managed Grafana (Essential SKU) | Replaces self-hosted Grafana; auto-connects to Managed Prometheus and Azure Monitor Logs. Alerting not needed for v1 |
| **Identity** | User-assigned Managed Identity on the Container App | Auth to metrics DCE (role: `Monitoring Metrics Publisher`). Logs path uses the AI connection string instead of MI |
| **Ingress auth** | Container App ingress `ipSecurityRestrictions` (per OQ2) | Allowlist client external IP(s); rejects everything else at the platform edge |
| **IaC** | Bicep in a new `infra/` directory | Reproducible, reviewable deployments |

## 5. Data Flow Changes From Current Stack

| Concern | Today | Azure |
|---|---|---|
| OTLP endpoint clients hit | `http://localhost:4317` | `https://<app>.<env>.azurecontainerapps.io` (4318 HTTP) or TCP ingress for gRPC |
| Metrics path | Collector → `prometheus` exporter → scraped by Prometheus | Collector → `prometheusremotewrite` → DCE → Managed Prometheus |
| Logs path | Collector → `otlphttp` → Loki | Collector → `azuremonitor` exporter → Application Insights (workspace-based, no sampling) → `AppTraces` table in Log Analytics |
| Dashboard storage | `claude-code-dashboard.json` file-provisioned | Imported into Managed Grafana (same JSON, adjusted datasource UIDs + rewritten log panels) |
| Auth on OTLP | None (localhost) | Managed-identity to backends; API-key or OAuth on ingress |

## 6. Decisions & Open Questions

### Resolved

| # | Question | Decision | Rationale |
|---|---|---|---|
| OQ1 | **Logs target: Log Analytics vs. App Insights?** | ✅ **Log Analytics, ingested via workspace-based Application Insights with `SamplingPercentage=100`**. Data lands in the auto-provisioned `AppTraces` table in our LA workspace. | Original plan was a custom LA table fed via DCE/DCR with an OTLP-shaped stream. Discovered during M1 deploy that no built-in OTLP-logs stream exists — the Log Ingestion API requires a custom JSON shape that doesn't match what the OTel `otlphttp` exporter sends. Pivoted to the `azuremonitor` exporter → workspace-based App Insights, which has a documented OTLP path and physically writes through to the same LA workspace. Sampling concern (the original reason to avoid App Insights) is addressed by setting `SamplingPercentage=100` on the AI resource, which disables the AI backend's adaptive sampling. Net effect: same physical store, same KQL queries (against `AppTraces` instead of `ClaudeCodeEvents_CL`), no schema control loss that matters. |
| OQ2 | **Ingress auth mechanism?** | ✅ **IP allowlist on Container App ingress** (`ipSecurityRestrictions`). User's external IP in /32 form. No API key, no header validation in the collector. | Layered defense: (1) ingress rejects non-allowlisted IPs at the platform edge, before reaching the collector process; (2) DCE/DCRs already require Entra-auth'd bearer tokens from the collector's Managed Identity, so even an ingress bypass couldn't write to backends; (3) Grafana is gated by Entra ID auth. Simplest model with no secrets to rotate. Revisit if we go multi-user and need per-user auth. |
| OQ7 | **Managed Grafana SKU?** | ✅ **Standard** (forced; Essential is no longer offered) | First deploy attempt revealed Essential is no longer accepted — `'Essential' is not a supported value. Supported sku values are: Standard`. The choice is now Standard or nothing. Standard adds ~$8/active-user/mo and unlocks alerting (which we still don't use for v1). |
| OQ8 | **Grafana network exposure?** | ✅ **Entra ID auth only** (no additional IP restriction) | Managed Grafana doesn't expose a straightforward IP-allowlist feature — the production-grade option is `publicNetworkAccess: Disabled` + private endpoint, which requires VNet infrastructure. Entra auth forces sign-in regardless of source IP; sufficient for v1 single-user. Revisit with private endpoint if team grows. |

### Open
| OQ3 | **Which dashboard panels need full rewrite?** | Audited (see `docs/azure-kql-panels.md`): 7 Loki panels need KQL rewrites; the 7 Prometheus panels port unchanged. | KQL drafts already exist in `azure-kql-panels.md`; ready to integrate during M4. |
| OQ4 | **How do end users get the endpoint + auth token?** | Env vars need to be distributed. Options: documented in README, managed via `~/.claude/settings.json`, or Entra-authenticated. | Document in README + support a team `settings.json` snippet. |
| OQ5 | **Retention and cost caps?** | Azure Monitor Workspace and Log Analytics both bill on ingestion. Need daily cap + retention policy to avoid surprises. | 30-day retention, daily cap sized from current local volume × 2. Measure first week before raising. |
| OQ6 | **Container Apps: always-on or scale-to-zero?** | Scale-to-zero saves money but cold start loses telemetry during startup window. | Min replicas = 1. Cost is minimal; reliability of ingest matters more. |

## 7. Implementation Sketch

Small milestones, each independently demoable:

**M1 — Managed backends provisioned** *(IaC only, no data flowing yet)*
- Bicep resources (`infra/main.bicep`):
  - Azure Monitor Workspace + DCE + metrics DCR (`kind: Direct`)
  - Log Analytics Workspace
  - Application Insights (workspace-based, linked to LA, `SamplingPercentage=100`)
  - Azure Managed Grafana (Essential SKU, system-assigned MI for read access)
  - Container Apps Environment
  - User-assigned Managed Identity with `Monitoring Metrics Publisher` on the metrics DCR. (Logs path uses the AI connection string instead.)
  - Grafana → AMW `Monitoring Reader`, Grafana → LA `Log Analytics Reader`
- Outputs: `metricsRemoteWriteUrl`, `appInsightsConnectionString`, identity client ID, LA workspace ID, Grafana endpoint.

**M2 — Collector config for Azure** (`collector-config.azure.yaml`)
- `prometheusremotewrite` exporter → metrics DCE (Entra auth via the `azureauth` extension + Managed Identity)
- `azuremonitor` exporter → App Insights (auth via connection string, mounted as ACA secret)
- `memory_limiter`, `resource`, `batch` processors
- Validate locally with `otelcol-contrib --config-validate`. Keep `collector-config.yaml` untouched so local dev still works.

**M3 — Container App deployed, ingesting from a local client**
- Deploy collector via `infra/collector-app.bicep`:
  - Image: `otel/opentelemetry-collector-contrib` (stock, pinned tag)
  - Config mounted as an ACA secret volume (`loadTextContent('../collector-config.azure.yaml')`)
  - HTTP ingress on port 4318 (OTLP/HTTP) with `ipSecurityRestrictions` allowing only the CIDR(s) in `allowedClientCidrs` param
  - User-assigned MI from M1 attached; env vars wired from M1 outputs
- Point a local `claude` at the ingress FQDN, verify metrics show up in Managed Prometheus via the Azure portal.

**M4 — Grafana dashboards ported**
- Import `claude-code-dashboard.json` into Managed Grafana.
- Rewrite datasource UIDs (Managed Prometheus + Azure Monitor Logs).
- Replace the 7 LogQL queries with the KQL drafts in `docs/azure-kql-panels.md`.
- Sanity check against known panels (cost, token usage, tool decisions, tool success rate, API duration).

**M5 — Hardening + cost caps**
- Verify `ipSecurityRestrictions` actually rejects non-allowlisted IPs (test with a VPN from another IP).
- Set daily ingestion caps on Log Analytics (already in M1 Bicep as `logDailyCapGb`; tune after 1 week of real traffic).
- Document client-side env var setup in README (`OTEL_EXPORTER_OTLP_ENDPOINT=https://<collector-fqdn>`).
- Confirm the IP-restriction doesn't break: Grafana → Prometheus queries, Grafana → Log Analytics queries, Collector → DCE writes (all use the Azure backbone / Entra auth, so should be unaffected).

## 8. Costs (rough, at single-developer volume)

| Service | Estimate | Notes |
|---|---|---|
| Azure Monitor Managed Prometheus | < $5/mo | Billed per sample ingested + per query. Claude Code emits modest cardinality. |
| Log Analytics (workspace-based AI ingestion) | $5–$15/mo | Billed per GB ingested at Pay-As-You-Go rates (~$2.76/GB). Claude Code events are small; cap enforced via `logDailyCapGb`. |
| Azure Managed Grafana (Standard) | ~$8/active-user/mo | Essential SKU was deprecated; Standard is the only option. Single-user → ~$8/mo. |
| Container Apps (1 always-on replica, 0.25 vCPU / 0.5 GiB) | ~$10–$15/mo | Consumption plan; scales to zero if we relax OQ6. |
| **Total** | **~$28–$43/mo** | Up from earlier estimate due to Grafana SKU change. Still cheaper than a B2s VM (~$30/mo) once you account for the ops time it saves. |

## 9. Risks

- **AppTraces schema is fixed by App Insights** — we don't control the table schema; new event-attr fields from Claude Code automatically land inside the dynamic `Properties` column without any infra change. Loss vs. the original custom-table plan: we can't promote attrs to typed columns (e.g., a hot `ToolName` index would need a manual KQL summary table or materialized view). Acceptable for v1.
- **`Properties` key shape** — the `azuremonitor` exporter usually preserves OTel attribute keys verbatim (so `event.name` → `Properties['event.name']`), but some versions normalize dots to underscores. After first deploy, run `AppTraces | take 10 | project Properties` to confirm before relying on the dashboard queries.
- **AI connection string is a credential** — embeds the instrumentation key. Stored as an ACA secret on the Container App; never appears in plaintext in the template or env. Rotation requires regenerating the AI key (`az monitor app-insights component update`) and redeploying M3.
- **Histograms / exponential metrics** — `prometheusremotewrite` exporter has some quirks with delta-temporality metrics; verify early with a known histogram from Claude Code. Claude Code's current metrics are all counters, so this is hypothetical until they add a histogram.
- **gRPC ingress** on Container Apps needs TCP ingress or HTTP/2 — HTTP/4318 is the safer default unless we have a reason to prefer gRPC.
- **Dynamic external IP** — if `allowedClientCidrs` points at a user's ISP-assigned IP that rotates, telemetry starts silently dropping. Mitigations: use a broader ISP CIDR, front with a static egress (Tailscale exit node, VPN), or periodically update the allowlist. M5 should include a sanity check procedure.
- **Collector image tag pinning** — stock `otel/opentelemetry-collector-contrib` changes component availability across versions; pin to a specific tag rather than `:latest` to avoid surprise breakages of `azureauth` or `prometheusremotewrite` behavior.

## 10. Decision Needed To Proceed

All resolved. Remaining open questions (OQ3–OQ6) are implementation details that can be settled during the milestones — none block M1.
