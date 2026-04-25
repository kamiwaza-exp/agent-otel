# Azure KQL Panel Reference — Claude Code Dashboard Port

Companion to `azure-deployment-design.md`. Contains:
1. Where Claude Code event data physically lands in Log Analytics.
2. KQL rewrites for each Loki/LogQL panel in `claude-code-dashboard.json`.
3. Notes on how each query maps to Grafana's Azure Monitor Logs data source.

The 7 Prometheus panels (cost, tokens, sessions, LoC, commits, PRs, API-requests-by-model) port to **Managed Prometheus without query changes** — only the datasource UID is different — so they are not in this document.

---

## 1. Where the data lives

The OTel Collector ships logs to **workspace-based Application Insights** (created in `main.bicep`) using the `azuremonitor` exporter. Workspace-based AI is just an ingestion door — data physically lands in the linked Log Analytics workspace under the auto-provisioned `App*` tables:

| LA table | What lands here |
|---|---|
| `AppTraces` | OTel log records (Claude Code's `claude_code.*` events all arrive here) |
| `AppEvents` | Custom events (not used by Claude Code today) |
| `AppMetrics` | OTel metrics if routed via the same exporter (we route metrics separately to Managed Prometheus, so this stays empty) |

For Claude Code, **everything we care about is in `AppTraces`**.

### AppTraces shape — relevant columns

| Column | Type | Source |
|---|---|---|
| `TimeGenerated` | `datetime` | OTel log record timestamp |
| `Message` | `string` | OTel log body (mostly empty for Claude Code — events use attrs, not body) |
| `SeverityLevel` | `int` | OTel severity number |
| `Properties` | `dynamic` | OTel attributes **and** resource attributes merged into a single bag — this is where every Claude Code event field lives |
| `OperationName` | `string` | Populated by AI; usually empty for OTel-shipped logs |
| `AppRoleName` | `string` | OTel resource `service.name` (set to `claude-code`) |

### Where each Claude Code attr ends up

Claude Code's standard attrs (`session.id`, `user.account_uuid`, `app.version`, etc.) and per-event attrs (`name`, `success`, `duration_ms`, `model`, `status_code`, etc.) all land inside the dynamic `Properties` column. Access by quoted key:

```kql
| where tostring(Properties['event.name']) == "tool_result"
| extend tool = tostring(Properties['name'])
| extend duration_ms = toint(Properties['duration_ms'])
```

Dotted keys like `event.name` need bracket-quoted access; bare attrs like `name` work either way.

---

## 2. Panel Rewrites

Each section shows the current LogQL query, the KQL equivalent, the Grafana data source result format to use, and any behavioral notes.

---

### 2.1 Tool Usage Rate Over Time

**LogQL (today):**
```logql
sum by (tool_name) (count_over_time({service_name="claude-code"} |= "claude_code.tool_result" [5m]))
```

**KQL:**
```kusto
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
| summarize count() by tool_name = tostring(Properties['name']), bin(TimeGenerated, 5m)
| order by TimeGenerated asc
```

- **Grafana format:** Time series
- **Series column:** `tool_name`
- **Value column:** `count_`

---

### 2.2 Cumulative Tool Usage

**LogQL (today):**
```logql
sum by (tool_name) (count_over_time({service_name="claude-code"} |= "claude_code.tool_result" [$__range]))
```

**KQL:**
```kusto
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
| summarize count_ = count() by tool_name = tostring(Properties['name'])
| order by count_ desc
```

- **Grafana format:** Table or Bar chart
- **Notes:** No time dimension; just totals over the dashboard range. Renders nicely as a horizontal bar chart.

---

### 2.3 Tool Success Rate

**LogQL (today):**
```logql
100 * (
  sum by (tool_name) (count_over_time({service_name="claude-code"} |= "claude_code.tool_result" | json | success="true" [15m]))
) / (
  sum by (tool_name) (count_over_time({service_name="claude-code"} |= "claude_code.tool_result" [15m]))
)
```

**KQL:**
```kusto
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
| summarize
    total = count(),
    successes = countif(tostring(Properties['success']) == "true")
    by tool_name = tostring(Properties['name']), bin(TimeGenerated, 15m)
| extend success_rate = iif(total > 0, 100.0 * successes / total, real(null))
| project TimeGenerated, tool_name, success_rate
| order by TimeGenerated asc
```

- **Grafana format:** Time series
- **Series column:** `tool_name`
- **Value column:** `success_rate` (unit: percent)
- **Notes:** The `iif(total > 0, ..., real(null))` guard prevents divide-by-zero in empty buckets.

---

### 2.4 API Request Duration by Model

**LogQL (today):**
```logql
avg by (model) (avg_over_time({service_name="claude-code"} |= "claude_code.api_request" | unwrap duration_ms [$__interval]))
```

**KQL:**
```kusto
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "api_request"
| summarize avg_duration_ms = avg(toint(Properties['duration_ms']))
    by model = tostring(Properties['model']), bin(TimeGenerated, $__timeInterval)
| order by TimeGenerated asc
```

- **Grafana format:** Time series
- **Series column:** `model`
- **Value column:** `avg_duration_ms` (unit: ms)
- **Notes:** `$__timeInterval` is Grafana's auto-bucket macro for the Azure Monitor data source. KQL's `toint(Properties['duration_ms'])` replaces LogQL's `unwrap duration_ms`.

---

### 2.5 API Error Rate

**LogQL (today):**
```logql
sum by (status_code) (rate({service_name="claude-code"} |= "claude_code.api_error" | json | __error__ = "" [$__interval]))
```

**KQL:**
```kusto
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "api_error"
| summarize errors = count() by status_code = tostring(Properties['status_code']), bin(TimeGenerated, 1m)
| extend rate_per_sec = errors / 60.0
| project TimeGenerated, status_code, rate_per_sec
| order by TimeGenerated asc
```

- **Grafana format:** Time series
- **Series column:** `status_code`
- **Value column:** `rate_per_sec` (unit: err/s)
- **Notes:** `Properties` is already parsed JSON, so the `| json | __error__=""` guard from LogQL has no equivalent — there's no parse step that could fail.

---

### 2.6 Tool Execution Events (log viewer)

**LogQL (today):**
```logql
{service_name="claude-code"} |= "claude_code.tool_result"
| line_format "{{.event_timestamp}} [{{.tool_name}}] {{if eq .success \"true\"}}✅{{else}}❌{{end}} {{.duration_ms}}ms {{if .error}}ERROR: {{.error}}{{end}}"
```

**KQL:**
```kusto
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
| project
    TimeGenerated,
    Tool = tostring(Properties['name']),
    Status = iif(tostring(Properties['success']) == "true", "✅", "❌"),
    DurationMs = toint(Properties['duration_ms']),
    Error = tostring(Properties['error'])
| order by TimeGenerated desc
```

- **Grafana format:** Logs (the Azure Monitor Logs data source has a "Logs" result format that renders rows similarly to Loki's Logs panel)
- **Notes:** This is the panel with cosmetic loss vs. LogQL. Tabular layout by default. To restore a single inline-formatted line, replace the `| project` with:
  ```kusto
  | extend DisplayLine = strcat(
        format_datetime(TimeGenerated, "HH:mm:ss"), " [",
        tostring(Properties['name']), "] ",
        iif(tostring(Properties['success']) == "true", "✅", "❌"), " ",
        toint(Properties['duration_ms']), "ms",
        iif(isnotempty(tostring(Properties['error'])), strcat(" ERROR: ", tostring(Properties['error'])), ""))
  | project TimeGenerated, DisplayLine
  ```

---

### 2.7 API Error Events (log viewer)

**LogQL (today):**
```logql
{service_name="claude-code"} |= "claude_code.api_error"
| line_format "{{.event_timestamp}} [{{.model}}] ❌ HTTP {{.status_code}} {{.duration_ms}}ms ERROR: {{.error}}"
```

**KQL:**
```kusto
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "api_error"
| project
    TimeGenerated,
    Model = tostring(Properties['model']),
    StatusCode = tostring(Properties['status_code']),
    DurationMs = toint(Properties['duration_ms']),
    Error = tostring(Properties['error']),
    Attempt = toint(Properties['attempt'])
| order by TimeGenerated desc
```

- **Grafana format:** Logs or Table

---

## 3. Mapping Summary

| Panel | Capability | KQL equivalent status |
|---|---|---|
| Tool Usage Rate Over Time | time-binned counts by label | ✅ Clean port |
| Cumulative Tool Usage | single-aggregate counts by label | ✅ Clean port |
| Tool Success Rate | ratio of filtered count to total, time-binned | ✅ Clean port + divide-by-zero guard |
| API Request Duration by Model | avg of numeric log attr, time-binned | ✅ Clean port |
| API Error Rate | per-second rate by label | ✅ Clean port |
| Tool Execution Events | templated log viewer rows | ⚠️ Cosmetic: tabular by default; `strcat` trick matches old look |
| API Error Events | templated log viewer rows | ⚠️ Same cosmetic note as above |

No *semantic* capability is lost. Cosmetic loss is limited to the log-viewer panels' default tabular layout, recoverable with a `DisplayLine` synthesis.

---

## 4. Querying via Grafana

In Grafana's Azure Monitor Logs panel target:

| Field | Value |
|---|---|
| **Service / Type** | Logs |
| **Resource** | `$law_resource` (the dashboard variable, set on import to the LA workspace resource ID from M1) |
| **Query** | the KQL above |
| **Format As** | Time series / Table / Logs (per panel) |

The dashboard variable is already declared in `claude-code-dashboard.azure.json` — set it once after import.

---

## 5. Resolved & Open Follow-Ups

**Resolved:**

- **`event.name` shape.** Verified via the collector's debug exporter against live ingest (revision v7). The `azuremonitor` exporter preserves dots — the key is `Properties['event.name']` (not normalized to `event_name`). **However**, the *value* is unprefixed: the LogRecord *Body* carries `claude_code.tool_result`, but the `event.name` *attribute* carries just `tool_result`. The Loki path filtered with `|= "claude_code.tool_result"` (a substring match against the whole line, which includes the body), so it matched on the prefixed string. KQL needs an exact match against the attribute value, so all KQL filters use the unprefixed name (`"tool_result"`, `"api_request"`, `"api_error"`).

**Open:**

- **Dashboard import workflow.** `claude-code-dashboard.azure.json` is generated from the local dashboard via `infra/generate-azure-dashboard.py`. Rerun that script after any local-dashboard edit.
