# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Nature

This is a **configuration-only repository** — there is no application source code. It provisions a full observability stack (OpenTelemetry Collector + Prometheus + Loki + Grafana) that ingests telemetry emitted by Claude Code itself. Changes here are almost always to YAML configs, the dashboard JSON, or `docker-compose.yml`.

## Common Commands

All lifecycle operations go through the `Makefile` (wrapping `docker compose`):

```bash
make up               # Start the stack (docker compose up -d)
make down             # Stop the stack
make restart          # Restart all services
make clean            # docker compose down -v + docker system prune -f (DESTROYS volumes)
make status           # Show running containers + service URLs
make logs             # Tail logs from all services
make logs-collector   # Tail otel-collector only (most useful for debugging ingest)
make logs-prometheus  # Tail Prometheus only
make logs-grafana     # Tail Grafana only
make validate-config  # Validate docker-compose.yml; also validates collector-config.yaml if otelcol-contrib is installed
make setup-claude     # Print the env vars a user needs to export to send telemetry here
```

Service endpoints after `make up`:
- Grafana: http://localhost:3000 (admin/admin)
- Prometheus: http://localhost:9090
- Loki: http://localhost:3100
- OTLP ingest: `localhost:4317` (gRPC) / `localhost:4318` (HTTP)
- Collector's Prometheus scrape endpoint: `localhost:8889` (only reached from inside the Docker network)

## Architecture

### Data Flow

```
Claude Code (OTLP exporter)
        │
        ▼
otel-collector   (receives OTLP on 4317/4318)
  ├─ metrics pipeline  → prometheus exporter on :8889 ──► Prometheus (scrapes every 15s) ──► Grafana
  └─ logs pipeline     → otlphttp exporter http://loki:3100/otlp ──► Loki ─────────────────► Grafana
```

Claude Code emits **metrics** (e.g., `claude_code.cost.usage`, `claude_code.token.usage`, `claude_code.session.count`) and **events/logs** (e.g., `claude_code.api_request`, `claude_code.tool_result`, `claude_code.user_prompt`). The collector splits them into two pipelines — this separation is the single most important architectural fact in this repo. Metrics go to Prometheus; events go to Loki.

### Config File Relationships

Modifying any one of these often requires a matching change in another:

| File | Role | Coupled With |
|------|------|--------------|
| `docker-compose.yml` | Defines services, ports, volume mounts | All config files below are mounted into containers here |
| `collector-config.yaml` | OTel Collector receivers/processors/exporters and pipelines | `prometheus.yml` (must scrape the collector's `prometheus` exporter); `docker-compose.yml` (service name `loki` is referenced in `otlphttp.endpoint`) |
| `prometheus.yml` | Prometheus scrape config | `collector-config.yaml` — must match the `prometheus` exporter endpoint (`otel-collector:8889`) |
| `grafana-datasources.yml` | Provisions Prometheus + Loki datasources | Datasource URLs use Docker service names (`http://prometheus:9090`, `http://loki:3100`) — only valid inside `otel-network` |
| `grafana-dashboards.yml` | Tells Grafana where to find dashboard JSON files (`/var/lib/grafana/dashboards`) | `docker-compose.yml` mounts `claude-code-dashboard.json` into that directory |
| `claude-code-dashboard.json` | The main dashboard — panel queries reference Prometheus metric names and Loki log fields | Must track metric names emitted by Claude Code (see `CLAUDE_OBSERVABILITY.md`) |

Service names (`otel-collector`, `prometheus`, `loki`, `grafana`) are the DNS names inside `otel-network` — never use `localhost` in config files that run inside containers.

### Two Compose Files

- `docker-compose.yml` — the **primary** stack: four separate containers. Use this for normal work.
- `docker-compose-lgtm.yml` — an **alternative** all-in-one using `grafana/otel-lgtm` (Grafana + Loki + Tempo + Mimir + OTel collector in a single image). Not wired into the Makefile; run it directly with `docker compose -f docker-compose-lgtm.yml up -d` when you want a faster-starting dev environment. Note: it binds the same host ports (3000/4317/4318) as the primary stack, so only run one at a time.

## Working With This Repo

### Changing collector behavior
Edit `collector-config.yaml`, then restart just the collector (`docker compose restart otel-collector`) and watch `make logs-collector` for parse errors. The pipelines block is the contract — if you add a new receiver/exporter, it must be wired into a pipeline or it has no effect.

### Adding or modifying dashboard panels
1. Grafana is provisioned with `allowUiUpdates: true` (see `grafana-dashboards.yml`), so you can edit panels in the UI at `http://localhost:3000` and export the JSON.
2. Save the exported JSON back to `claude-code-dashboard.json` so changes are committed.
3. Provisioning re-reads dashboards every 10s (`updateIntervalSeconds: 10`), so a save-to-file propagates without restart.
4. Panel queries use PromQL against Prometheus and LogQL against Loki — keep metric names aligned with `CLAUDE_OBSERVABILITY.md`.

### Verifying metrics are flowing
```bash
# Is the collector receiving data and exporting to its Prometheus endpoint?
curl -s http://localhost:8889/metrics | grep claude_code

# Is Prometheus scraping successfully?
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[].health'
```
If `claude_code.*` metrics don't appear, the most common causes are: (1) Claude Code not started with `CLAUDE_CODE_ENABLE_TELEMETRY=1`, (2) `OTEL_EXPORTER_OTLP_ENDPOINT` pointing elsewhere, (3) collector pipeline misconfiguration — check `make logs-collector`.

### Reference documentation
`CLAUDE_OBSERVABILITY.md` (16KB, in the repo root) is the authoritative reference for every environment variable Claude Code understands and every metric/event it emits. Consult it before adding new dashboard panels or changing cardinality-related env vars.

## Conventions Noted From Existing Files

- Commit message style (from `CONTRIBUTING.md`): Conventional-commit-ish prefixes — `feat:`, `fix:`, `docs:`, `refactor:` — e.g., `feat: add API request count panel to cost analysis`.
- `.gitignore` excludes `prometheus_data/`, `grafana_data/`, `loki_data/` — do not commit persisted volume contents if you add named volumes.
- No language-specific tooling (no `package.json`, `pyproject.toml`, etc.) — `make validate-config` is the only "test" command.
