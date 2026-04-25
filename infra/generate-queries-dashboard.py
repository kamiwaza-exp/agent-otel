#!/usr/bin/env python3
"""Generate claude-code-queries.azure.json — a query-browser dashboard.

The main `claude-code-dashboard.azure.json` is operational ("what's
happening now"). This dashboard is exploratory: each panel runs one of the
canned queries from `docs/example-queries.md` so users can drill into the
data without copy-pasting KQL.

Layout follows the same widths used in the operational dashboard:
  - text/log panels: full width (24)
  - chart panels:    half width  (12)
  - stat panels:     quarter width (6)
"""

import json
from pathlib import Path

DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-queries.azure.json")

AZURE_DS = {"type": "grafana-azure-monitor-datasource", "uid": "${DS_AZURE_MONITOR}"}
LA_RESOURCE_VAR = "$law_resource"


def kql_target(
    query: str, result_format: str = "table", legend: str = "", ref_id: str = "A"
) -> dict:
    """Build an Azure Monitor Logs target."""
    target = {
        "datasource": AZURE_DS,
        "queryType": "Azure Log Analytics",
        "azureLogAnalytics": {
            "query": query,
            "resource": LA_RESOURCE_VAR,
            "resultFormat": result_format,
        },
        "refId": ref_id,
    }
    if legend:
        target["legendFormat"] = legend
    return target


def stat_panel(
    pid: int, title: str, x: int, y: int, query: str, unit: str = "short"
) -> dict:
    """Single-value stat panel (table format, first numeric column)."""
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": 6, "h": 4},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "red", "value": None},
                        {"color": "green", "value": 1},
                    ],
                },
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "area",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "textMode": "auto",
        },
        "targets": [kql_target(query, "table")],
    }


def table_panel(
    pid: int, title: str, x: int, y: int, w: int, h: int, query: str
) -> dict:
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True},
        "targets": [kql_target(query, "table")],
    }


def timeseries_panel(
    pid: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    query: str,
    unit: str = "short",
    legend: str = "",
) -> dict:
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "color": {"mode": "palette-classic"},
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 2,
                    "fillOpacity": 10,
                    "showPoints": "never",
                    "spanNulls": False,
                },
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "legend": {
                "displayMode": "table",
                "placement": "bottom",
                "calcs": ["mean", "max"],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [kql_target(query, "time_series", legend=legend)],
    }


def barchart_panel(
    pid: int, title: str, x: int, y: int, w: int, h: int, query: str
) -> dict:
    return {
        "id": pid,
        "type": "barchart",
        "title": title,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"unit": "short"}, "overrides": []},
        "options": {
            "orientation": "horizontal",
            "showValue": "auto",
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": False,
            },
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [kql_target(query, "table")],
    }


def logs_panel(
    pid: int, title: str, x: int, y: int, w: int, h: int, query: str
) -> dict:
    return {
        "id": pid,
        "type": "logs",
        "title": title,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
        },
        "targets": [kql_target(query, "logs")],
    }


def row(title: str, y: int) -> dict:
    return {
        "type": "row",
        "title": title,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "collapsed": False,
    }


# ── queries (subset of docs/example-queries.md, picked for browsability) ──

Q_ACTIVE_USERS_1D = """AppTraces
| where TimeGenerated > ago(1d)
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize count_distinct = dcount(email)
"""

Q_ACTIVE_USERS_7D = """AppTraces
| where TimeGenerated > ago(7d)
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize count_distinct = dcount(email)
"""

Q_ACTIVE_HOSTS_7D = """AppTraces
| where TimeGenerated > ago(7d)
| extend host = tostring(Properties['host.name'])
| where isnotempty(host)
| summarize count_distinct = dcount(host)
"""

Q_ACTIVE_PROJECTS_7D = """AppTraces
| where TimeGenerated > ago(7d)
| extend proj = tostring(Properties['project.name'])
| where isnotempty(proj)
| summarize count_distinct = dcount(proj)
"""

Q_DAU_30D = """AppTraces
| where TimeGenerated > ago(30d)
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize DAU = dcount(email) by bin(TimeGenerated, 1d)
| order by TimeGenerated asc
"""

Q_USERS_7D_DETAIL = """AppTraces
| where TimeGenerated > ago(7d)
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize
    events = count(),
    sessions = dcount(tostring(Properties['session.id'])),
    first_seen = min(TimeGenerated),
    last_seen = max(TimeGenerated)
  by email
| order by events desc
"""

Q_BY_USERNAME = """AppTraces
| where TimeGenerated > ago(7d)
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| extend username = tostring(split(email, "@")[0])
| summarize
    accounts = make_set(email),
    events = count(),
    sessions = dcount(tostring(Properties['session.id']))
  by username
| order by events desc
"""

Q_ACTIVE_HOURS_BY_USER = """AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "user_prompt"
| extend email = tostring(Properties['user.email'])
| extend hour_bucket = bin(TimeGenerated, 1h)
| summarize prompts = count(), active_hours = count_distinct(hour_bucket) by email
| order by active_hours desc
"""

Q_HOUR_OF_DAY = """AppTraces
| where TimeGenerated > ago(30d)
| where tostring(Properties['event.name']) == "user_prompt"
// Per-user timezone shift before bucketing by hour. host.tz_offset_minutes
// is set by the agent-otel launcher; records without it default to UTC.
| extend tz_off_min = coalesce(toint(tostring(Properties['host.tz_offset_minutes'])), 0)
| extend local_time = TimeGenerated + tz_off_min * 1m
| extend hour_num = datetime_part("hour", local_time)
| summarize prompts = count() by hour_num
| extend hour_of_day = strcat(iif(hour_num < 10, "0", ""), tostring(hour_num), ":00")
| project hour_of_day, prompts
| order by hour_of_day asc
"""

Q_TOP_PROJECTS = """AppTraces
| where TimeGenerated > ago(7d)
| extend proj = tostring(Properties['project.name'])
| where isnotempty(proj)
| summarize
    events = count(),
    users = dcount(tostring(Properties['user.email'])),
    sessions = dcount(tostring(Properties['session.id']))
  by proj
| order by events desc
"""

Q_COST_BY_USER = """AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend email = tostring(p['user.email'])
| summarize Cost = sum(Sum) by email
| top 10 by Cost desc
"""

Q_COST_BY_USERNAME = """AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend email = tostring(p['user.email'])
| extend username = tostring(split(email, "@")[0])
| summarize Cost = sum(Sum), Accounts = make_set(email) by username
| order by Cost desc
"""

Q_COST_BY_PROJECT = """AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend proj = tostring(p['project.name'])
| where isnotempty(proj)
| summarize Cost = sum(Sum) by proj
| order by Cost desc
"""

Q_COST_BY_MODEL = """AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend model = tostring(p['model'])
| summarize Cost = sum(Sum) by model
| order by Cost desc
"""

Q_TOKENS_BY_TYPE = """AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.token.usage"
| extend p = parse_json(Properties)
| extend type = tostring(p['type'])
| summarize Tokens = sum(Sum) by type
| order by Tokens desc
"""

Q_TOP_TOOLS = """AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "tool_result"
| summarize calls = count() by tool = tostring(Properties['name'])
| order by calls desc
"""

Q_TOOL_SUCCESS = """AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "tool_result"
| summarize
    total = count(),
    successes = countif(tostring(Properties['success']) == "true")
  by tool = tostring(Properties['name'])
| extend success_rate_pct = round(100.0 * successes / total, 1)
| order by total desc
"""

Q_TOOLS_PER_PROMPT = """let prompts = AppTraces
  | where TimeGenerated > ago(7d)
  | where tostring(Properties['event.name']) == "user_prompt"
  | summarize prompts = count() by email = tostring(Properties['user.email']);
let tools = AppTraces
  | where TimeGenerated > ago(7d)
  | where tostring(Properties['event.name']) == "tool_result"
  | summarize tools = count() by email = tostring(Properties['user.email']);
prompts
| join kind=inner tools on email
| project email, prompts, tools, tools_per_prompt = round(todouble(tools) / prompts, 1)
| order by tools_per_prompt desc
"""

Q_RECENT_PROMPTS = """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "user_prompt"
| project
    TimeGenerated,
    Message = strcat(
        "[", tostring(Properties['user.email']), "] ",
        "[", coalesce(tostring(Properties['project.name']), "(no project)"), "] ",
        "len=", tostring(Properties['prompt_length']), " ",
        iif(isnotempty(tostring(Properties['prompt'])),
            tostring(Properties['prompt']),
            "<prompt text not logged>"))
| order by TimeGenerated desc
"""

Q_RECENT_ERRORS = """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "api_error"
| project
    TimeGenerated,
    Message = strcat(
        "[", tostring(Properties['user.email']), "] ",
        "[", tostring(Properties['model']), "] HTTP ",
        tostring(Properties['status_code']), " ",
        tostring(Properties['duration_ms']), "ms ",
        "(attempt ", tostring(Properties['attempt']), ") ",
        tostring(Properties['error']))
| order by TimeGenerated desc
"""


# ── layout ──

panels: list[dict] = []
pid = 100  # offset to avoid colliding with the operational dashboard's ids


def next_id() -> int:
    global pid
    pid += 1
    return pid


# ── overview row: counts of distinct things ──
panels.append(row("📊 Footprint (24h / 7d)", y=0))
panels.append(stat_panel(next_id(), "Active users (24h)", 0, 1, Q_ACTIVE_USERS_1D))
panels.append(stat_panel(next_id(), "Active users (7d)", 6, 1, Q_ACTIVE_USERS_7D))
panels.append(stat_panel(next_id(), "Distinct hosts (7d)", 12, 1, Q_ACTIVE_HOSTS_7D))
panels.append(
    stat_panel(next_id(), "Distinct projects (7d)", 18, 1, Q_ACTIVE_PROJECTS_7D)
)

# ── activity ──
panels.append(row("👥 Activity", y=5))
panels.append(
    timeseries_panel(
        next_id(), "Daily active users (30d)", 0, 6, 24, 8, Q_DAU_30D, legend="DAU"
    )
)
panels.append(
    table_panel(next_id(), "Users (last 7d)", 0, 14, 12, 9, Q_USERS_7D_DETAIL)
)
panels.append(
    table_panel(
        next_id(),
        "Users by username (multi-account aggregation)",
        12,
        14,
        12,
        9,
        Q_BY_USERNAME,
    )
)
panels.append(
    table_panel(
        next_id(),
        "Active hours per user (7d, prompt-active 1h buckets)",
        0,
        23,
        12,
        9,
        Q_ACTIVE_HOURS_BY_USER,
    )
)
panels.append(
    barchart_panel(
        next_id(), "Prompts by hour of day (30d)", 12, 23, 12, 9, Q_HOUR_OF_DAY
    )
)

# ── projects ──
panels.append(row("🗂  Projects", y=32))
panels.append(table_panel(next_id(), "Top projects (7d)", 0, 33, 24, 8, Q_TOP_PROJECTS))

# ── cost ──
panels.append(row("💰 Cost (approximation — see CLAUDE_OBSERVABILITY.md)", y=41))
panels.append(
    table_panel(next_id(), "Cost by user (7d, top 10)", 0, 42, 12, 9, Q_COST_BY_USER)
)
panels.append(
    table_panel(
        next_id(),
        "Cost by username (multi-account aggregation)",
        12,
        42,
        12,
        9,
        Q_COST_BY_USERNAME,
    )
)
panels.append(
    barchart_panel(next_id(), "Cost by project", 0, 51, 12, 8, Q_COST_BY_PROJECT)
)
panels.append(
    barchart_panel(next_id(), "Cost by model", 12, 51, 12, 8, Q_COST_BY_MODEL)
)
panels.append(
    barchart_panel(
        next_id(),
        "Tokens by type (input / output / cache)",
        0,
        59,
        24,
        8,
        Q_TOKENS_BY_TYPE,
    )
)

# ── tools ──
panels.append(row("🔧 Tools", y=67))
panels.append(table_panel(next_id(), "Top tools (7d)", 0, 68, 12, 9, Q_TOP_TOOLS))
panels.append(
    table_panel(next_id(), "Tool success rate by tool", 12, 68, 12, 9, Q_TOOL_SUCCESS)
)
panels.append(
    table_panel(
        next_id(), "Tool calls per prompt by user", 0, 77, 24, 8, Q_TOOLS_PER_PROMPT
    )
)

# ── log streams ──
panels.append(row("📝 Recent activity (uses dashboard time picker)", y=85))
panels.append(logs_panel(next_id(), "Recent prompts", 0, 86, 24, 10, Q_RECENT_PROMPTS))
panels.append(logs_panel(next_id(), "Recent API errors", 0, 96, 24, 8, Q_RECENT_ERRORS))


# ── dashboard envelope ──

dashboard = {
    "annotations": {"list": []},
    "description": "Canned drill-down queries for the agent-otel observability stack. Mirrors docs/example-queries.md so users can browse without copy-pasting KQL.",
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "id": None,
    "links": [],
    "liveNow": False,
    "panels": panels,
    "refresh": "1m",
    "schemaVersion": 27,
    "style": "dark",
    "tags": ["claude-code", "observability", "queries"],
    "templating": {
        "list": [
            {
                "name": "law_resource",
                "type": "textbox",
                "label": "Log Analytics Workspace Resource ID",
                "description": "Paste the full resource ID, e.g. /subscriptions/xxx/resourceGroups/agent-otel-rg/providers/Microsoft.OperationalInsights/workspaces/agent-otel-law",
                "query": "",
                "current": {"selected": False, "text": "", "value": ""},
                "hide": 2,
                "skipUrlSync": False,
            }
        ]
    },
    "time": {"from": "now-7d", "to": "now"},
    "timepicker": {},
    "timezone": "",
    "title": "Agent Observability — Drill-Downs",
    "uid": "agent-otel-azure-queries",
    "version": 1,
    "__inputs": [
        {
            "name": "DS_AZURE_MONITOR",
            "label": "Azure Monitor",
            "description": "Azure Monitor data source with access to the Log Analytics workspace containing AppMetrics + AppTraces.",
            "type": "datasource",
            "pluginId": "grafana-azure-monitor-datasource",
            "pluginName": "Azure Monitor",
        }
    ],
    "__requires": [
        {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "10.0.0"},
        {
            "type": "datasource",
            "id": "grafana-azure-monitor-datasource",
            "name": "Azure Monitor",
            "version": "1.0.0",
        },
    ],
}


def main() -> None:
    DST.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(f"Wrote {DST} ({DST.stat().st_size} bytes, {len(panels)} panels)")


if __name__ == "__main__":
    main()
