#!/usr/bin/env python3
"""Generate claude-code-dashboard.azure.json from the local dashboard."""

import copy
import json
from pathlib import Path

SRC = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-dashboard.json")
DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-dashboard.azure.json")

# Single datasource for the Azure port: all panels (metrics + logs + events)
# query Log Analytics via the Azure Monitor data source. The original split
# (Prometheus + Azure Monitor) was collapsed when we unified metrics onto
# App Insights / AppMetrics.
AZURE_DS = {"type": "grafana-azure-monitor-datasource", "uid": "${DS_AZURE_MONITOR}"}
LA_RESOURCE_VAR = "$law_resource"


# KQL rewrites for what used to be Prometheus-source panels. The Azure port
# originally split metrics (Managed Prometheus via prometheusremotewrite) and
# logs (App Insights). PRW + the alpha azureauthextension didn't work from
# Container Apps, so we unified on App Insights for both signals. Metrics now
# land in `AppMetrics`, query shape differs from PromQL:
#   - Each row has `Name`, `Sum`, `ItemCount`, and `Properties` (JSON string).
#   - `Sum` is the delta for that row's interval; `sum(Sum)` over a window =
#     running total (equivalent to `increase()` in PromQL).
#   - Labels live inside Properties — parse with `parse_json(Properties)`.
#
# Each entry is a list of target specs so multi-target panels (e.g., id=12
# Development Activity with separate commits + PRs series) work uniformly.
METRICS_KQL_BY_PANEL_ID = {
    # Overview stat panels — single-value output
    1: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.session.count"
| extend _p = parse_json(Properties)
| where tostring(_p['user.email']) in (${users:doublequote})
| summarize Sessions = sum(Sum)"""
            ),
            "resultFormat": "table",
            "legendFormat": "Sessions",
        }
    ],
    2: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
| extend _p = parse_json(Properties)
| where tostring(_p['user.email']) in (${users:doublequote})
| summarize Cost = sum(Sum)"""
            ),
            "resultFormat": "table",
            "legendFormat": "Cost",
        }
    ],
    3: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.token.usage"
| extend _p = parse_json(Properties)
| where tostring(_p['user.email']) in (${users:doublequote})
| summarize Tokens = sum(Sum)"""
            ),
            "resultFormat": "table",
            "legendFormat": "Tokens",
        }
    ],
    4: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.lines_of_code.count"
| extend _p = parse_json(Properties)
| where tostring(_p['user.email']) in (${users:doublequote})
| summarize Lines = sum(Sum)"""
            ),
            "resultFormat": "table",
            "legendFormat": "Lines",
        }
    ],
    # Cost & Usage charts
    5: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| where tostring(p['user.email']) in (${users:doublequote})
| summarize Cost = sum(Sum) by model = tostring(p.model), bin(TimeGenerated, 1h)
| order by TimeGenerated asc"""
            ),
            "resultFormat": "time_series",
            "legendFormat": "{{model}}",
        }
    ],
    6: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.token.usage"
| extend p = parse_json(Properties)
| where tostring(p['user.email']) in (${users:doublequote})
| summarize Tokens = sum(Sum) by type = tostring(p.type), bin(TimeGenerated, 5m)
| order by TimeGenerated asc"""
            ),
            "resultFormat": "time_series",
            "legendFormat": "{{type}}",
        }
    ],
    # API Requests by Model — use AppTraces events, not AppMetrics (the original
    # PromQL hack of `changes(cost.usage)` is replaced with a clean event count).
    15: [
        {
            "query": (
                """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "api_request"
| summarize Requests = count() by model = tostring(Properties['model']), bin(TimeGenerated, 5m)
| order by TimeGenerated asc"""
            ),
            "resultFormat": "time_series",
            "legendFormat": "{{model}}",
        }
    ],
    11: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.lines_of_code.count"
| extend p = parse_json(Properties)
| where tostring(p['user.email']) in (${users:doublequote})
| summarize Lines = sum(Sum) by type = tostring(p.type), bin(TimeGenerated, 5m)
| order by TimeGenerated asc"""
            ),
            "resultFormat": "time_series",
            "legendFormat": "{{type}}",
        }
    ],
    # Development Activity — two targets (commits + PRs) on one panel
    12: [
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.commit.count"
| extend _p = parse_json(Properties)
| where tostring(_p['user.email']) in (${users:doublequote})
| summarize Commits = sum(Sum) by bin(TimeGenerated, 1h)
| order by TimeGenerated asc"""
            ),
            "resultFormat": "time_series",
            "legendFormat": "Commits",
            "refId": "A",
        },
        {
            "query": (
                """AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.pull_request.count"
| extend _p = parse_json(Properties)
| where tostring(_p['user.email']) in (${users:doublequote})
| summarize PRs = sum(Sum) by bin(TimeGenerated, 1h)
| order by TimeGenerated asc"""
            ),
            "resultFormat": "time_series",
            "legendFormat": "Pull Requests",
            "refId": "B",
        },
    ],
}


# KQL rewrites keyed by panel id, from docs/azure-kql-panels.md.
# All Claude Code event data lands in AppTraces (workspace-based App Insights).
# OTel attrs end up in the dynamic Properties column; access by quoted key.
KQL_BY_PANEL_ID = {
    7: {
        "query": (
            """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "tool_result"
| summarize count() by tool_name = tostring(Properties['tool_name']), bin(TimeGenerated, 5m)
| order by TimeGenerated asc"""
        ),
        "resultFormat": "time_series",
    },
    14: {
        "query": (
            """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "tool_result"
| summarize count_ = count() by tool_name = tostring(Properties['tool_name'])
| order by count_ desc"""
        ),
        "resultFormat": "table",
    },
    8: {
        "query": (
            """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "tool_result"
| summarize
    total = count(),
    successes = countif(tostring(Properties['success']) == "true")
    by tool_name = tostring(Properties['tool_name']), bin(TimeGenerated, 15m)
| extend success_rate = iif(total > 0, 100.0 * successes / total, real(null))
| project TimeGenerated, tool_name, success_rate
| order by TimeGenerated asc"""
        ),
        "resultFormat": "time_series",
    },
    9: {
        "query": (
            """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "api_request"
| summarize avg_duration_ms = avg(toint(Properties['duration_ms']))
    by model = tostring(Properties['model']), bin(TimeGenerated, $__timeInterval)
| order by TimeGenerated asc"""
        ),
        "resultFormat": "time_series",
    },
    10: {
        "query": (
            """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "api_error"
| summarize errors = count() by status_code = tostring(Properties['status_code']), bin(TimeGenerated, 1m)
| extend rate_per_sec = errors / 60.0
| project TimeGenerated, status_code, rate_per_sec
| order by TimeGenerated asc"""
        ),
        "resultFormat": "time_series",
    },
    13: {
        # Synthesize a single Message column: Grafana's "logs" result format
        # only renders the timestamp + a Message body. Multiple project columns
        # collapse to just the timestamp, which is why the panel showed dates
        # only. strcat-ing a one-line summary recovers the LogQL look.
        "query": (
            """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "tool_result"
| project
    TimeGenerated,
    Message = strcat(
        "[", tostring(Properties['name']), "] ",
        iif(tostring(Properties['success']) == "true", "✅", "❌"), " ",
        tostring(Properties['duration_ms']), "ms",
        iif(isnotempty(tostring(Properties['error'])),
            strcat(" ERROR: ", tostring(Properties['error'])),
            ""))
| order by TimeGenerated desc"""
        ),
        "resultFormat": "logs",
    },
    17: {
        # Same Message-column synthesis as panel 13. api_error always carries
        # an error attribute, so no isnotempty guard needed.
        "query": (
            """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "api_error"
| project
    TimeGenerated,
    Message = strcat(
        "[", tostring(Properties['model']), "] HTTP ",
        tostring(Properties['status_code']), " ",
        tostring(Properties['duration_ms']), "ms",
        " (attempt ", tostring(Properties['attempt']), ") ",
        "ERROR: ", tostring(Properties['error']))
| order by TimeGenerated desc"""
        ),
        "resultFormat": "logs",
    },
}


# Azure-port layout overrides. Sized per:
#   - log/text panels: full width (w=24)
#   - chart panels (timeseries, barchart): half width (w=12)
#   - stat panels (numbers/dials): quarter width (w=6)
# y values keep the original section grouping but compact within each section.
PANEL_GRID = {
    # Overview — stat panels, quarter width
    1: {"x": 0, "y": 1, "w": 6, "h": 4},
    2: {"x": 6, "y": 1, "w": 6, "h": 4},
    3: {"x": 12, "y": 1, "w": 6, "h": 4},
    4: {"x": 18, "y": 1, "w": 6, "h": 4},
    # Cost & Usage — charts, half width
    5: {"x": 0, "y": 6, "w": 12, "h": 8},
    6: {"x": 12, "y": 6, "w": 12, "h": 8},
    15: {"x": 0, "y": 14, "w": 12, "h": 8},  # was full width in local
    # Tool Usage & Performance — charts; id=8 moved off the y=23 collision
    7: {"x": 0, "y": 23, "w": 12, "h": 8},
    14: {"x": 12, "y": 23, "w": 12, "h": 8},
    8: {"x": 0, "y": 31, "w": 12, "h": 8},
    # Performance & Errors — charts
    9: {"x": 0, "y": 40, "w": 12, "h": 8},
    10: {"x": 12, "y": 40, "w": 12, "h": 8},
    # User Activity & Productivity — charts
    11: {"x": 0, "y": 49, "w": 12, "h": 8},
    12: {"x": 12, "y": 49, "w": 12, "h": 8},
    # Event Logs — log panels, full width, stacked vertically
    20: {"x": 0, "y": 58, "w": 24, "h": 8},  # User Prompts (Azure-only)
    13: {"x": 0, "y": 66, "w": 24, "h": 8},  # Tool Execution Events
    17: {"x": 0, "y": 74, "w": 24, "h": 8},  # API Error Events
}

# Row positions follow the panel y-positions above. Rows are matched by title
# (no stable id) so any title rename in the local dashboard breaks the match —
# update both sides together.
ROW_GRID = {
    "📊 Overview": {"x": 0, "y": 0, "w": 24, "h": 1},
    "💰 Cost & Usage Analysis": {"x": 0, "y": 5, "w": 24, "h": 1},
    "🔧 Tool Usage & Performance": {"x": 0, "y": 22, "w": 24, "h": 1},
    "⚡ Performance & Errors": {"x": 0, "y": 39, "w": 24, "h": 1},
    "📝 User Activity & Productivity": {"x": 0, "y": 48, "w": 24, "h": 1},
    "🔍 Event Logs": {"x": 0, "y": 57, "w": 24, "h": 1},
}


def apply_layout(panels: list[dict]) -> None:
    """Override gridPos per Azure layout rules. Mutates panels in place."""
    for p in panels:
        if p.get("type") == "row":
            title = p.get("title")
            if isinstance(title, str):
                grid = ROW_GRID.get(title)
                if grid:
                    p["gridPos"] = grid
        else:
            pid = p.get("id")
            if isinstance(pid, int):
                grid = PANEL_GRID.get(pid)
                if grid:
                    p["gridPos"] = grid


def make_user_prompts_panel() -> dict:
    """Construct the User Prompts log panel (Azure-only — no local equivalent).

    Renders one row per user_prompt event using a synthesized Message column,
    same shape as the other log panels. If `Properties['prompt']` is empty
    (Claude Code defaults to NOT logging prompt text — set
    OTEL_LOG_USER_PROMPTS=1 to opt in), the panel shows a hint instead so
    the empty state is self-explanatory.
    """
    return {
        "id": 20,
        "type": "logs",
        "title": "User Prompts",
        "datasource": AZURE_DS,
        "gridPos": {"x": 0, "y": 58, "w": 24, "h": 8},
        "options": {
            "dedupStrategy": "none",
            "enableLogDetails": True,
            "prettifyLogMessage": False,
            "showCommonLabels": False,
            "showLabels": False,
            "showTime": True,
            "sortOrder": "Descending",
            "wrapLogMessage": True,
        },
        "targets": [
            {
                "datasource": AZURE_DS,
                "queryType": "Azure Log Analytics",
                "azureLogAnalytics": {
                    "query": (
                        """AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['user.email']) in (${users:doublequote})
| where tostring(Properties['event.name']) == "user_prompt"
| project
    TimeGenerated,
    Message = strcat(
        "[", substring(tostring(Properties['session.id']), 0, 8), "] ",
        "len=", tostring(Properties['prompt_length']), " ",
        iif(isnotempty(tostring(Properties['prompt'])),
            tostring(Properties['prompt']),
            "<prompt text not logged: set OTEL_LOG_USER_PROMPTS=1 on the client>"))
| order by TimeGenerated desc"""
                    ),
                    "resource": LA_RESOURCE_VAR,
                    "resultFormat": "logs",
                },
                "refId": "A",
            }
        ],
    }


def make_azure_target(panel_id: int, ref_id: str = "A", legend: str = "") -> dict:
    """Build an Azure Monitor Logs query target."""
    spec = KQL_BY_PANEL_ID[panel_id]
    target = {
        "datasource": AZURE_DS,
        "queryType": "Azure Log Analytics",
        "azureLogAnalytics": {
            "query": spec["query"],
            "resource": LA_RESOURCE_VAR,
            "resultFormat": spec["resultFormat"],
        },
        "refId": ref_id,
    }
    if legend:
        target["legendFormat"] = legend
    return target


def rewrite_panel(panel: dict) -> dict:
    """Return an Azure-port version of a panel."""
    p = copy.deepcopy(panel)
    ds = p.get("datasource")
    if not isinstance(ds, dict):
        return p  # row panels, no datasource

    ds_type = ds.get("type")

    if ds_type == "prometheus":
        # Convert Prometheus-source panels to Azure Monitor Logs against
        # AppMetrics (or AppTraces for event-count panels). The Azure port
        # no longer uses Managed Prometheus because prometheusremotewrite
        # + azureauth was unreliable from Container Apps.
        pid = p.get("id")
        if not isinstance(pid, int) or pid not in METRICS_KQL_BY_PANEL_ID:
            raise RuntimeError(
                f"Prometheus panel {pid} ({p.get('title')}) missing AppMetrics KQL mapping"
            )
        p["datasource"] = AZURE_DS
        p["targets"] = [
            {
                "datasource": AZURE_DS,
                "queryType": "Azure Log Analytics",
                "azureLogAnalytics": {
                    "query": spec["query"],
                    "resource": LA_RESOURCE_VAR,
                    "resultFormat": spec["resultFormat"],
                },
                "refId": spec.get("refId", chr(ord("A") + i)),
                **(
                    {"legendFormat": spec["legendFormat"]}
                    if spec.get("legendFormat")
                    else {}
                ),
            }
            for i, spec in enumerate(METRICS_KQL_BY_PANEL_ID[pid])
        ]
        return p

    if ds_type == "loki":
        pid = p.get("id")
        if pid not in KQL_BY_PANEL_ID:
            raise RuntimeError(
                f"Loki panel {pid} ({p.get('title')}) missing KQL mapping"
            )

        p["datasource"] = AZURE_DS

        # Preserve original legendFormat when present so series naming stays the same.
        orig_legend = ""
        if p.get("targets"):
            orig_legend = p["targets"][0].get("legendFormat", "")
        # Rewrite legendFormat placeholders: {{tool_name}} → {{tool_name}} still works
        # in Grafana's Azure Monitor data source as a column-substitution.
        azure_legend = orig_legend

        # Panel 14 was a "timeseries" but semantically a bar chart.
        # Switch to "barchart" for Azure port so the KQL table renders sensibly.
        if pid == 14:
            p["type"] = "barchart"
            p["options"] = {
                "legend": {
                    "displayMode": "list",
                    "placement": "bottom",
                    "showLegend": True,
                },
                "orientation": "horizontal",
                "xTickLabelRotation": 0,
                "xTickLabelSpacing": 0,
                "showValue": "auto",
                "stacking": "none",
                "groupWidth": 0.7,
                "barWidth": 0.97,
                "barRadius": 0,
                "fullHighlight": False,
                "tooltip": {"mode": "single", "sort": "none"},
                "text": {},
            }

        p["targets"] = [make_azure_target(pid, ref_id="A", legend=azure_legend)]
        return p

    raise RuntimeError(f"Unknown datasource type {ds_type} in panel {p.get('id')}")


def main():
    src = json.loads(SRC.read_text())
    dst = copy.deepcopy(src)

    # Identity + title — fixed for the Azure port; not derived from the
    # local dashboard so renames there don't leak into Azure.
    dst["title"] = "Agent Observability"
    dst["uid"] = "agent-otel-azure"
    # Strip the Grafana-assigned auto-increment id so import treats it as new
    dst["id"] = None
    dst["version"] = 1

    # Declare plugin + datasource requirements so import prompts correctly.
    # Single Azure Monitor data source now covers both AppMetrics queries
    # (metrics panels) and AppTraces queries (log/event panels).
    dst["__inputs"] = [
        {
            "name": "DS_AZURE_MONITOR",
            "label": "Azure Monitor",
            "description": "Azure Monitor data source with access to the Log Analytics workspace containing AppMetrics + AppTraces.",
            "type": "datasource",
            "pluginId": "grafana-azure-monitor-datasource",
            "pluginName": "Azure Monitor",
        },
    ]
    dst["__requires"] = [
        {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "10.0.0"},
        {
            "type": "datasource",
            "id": "grafana-azure-monitor-datasource",
            "name": "Azure Monitor",
            "version": "1.0.0",
        },
    ]

    # Dashboard variables: $law_resource (hidden, populated by upload script)
    # and $users (multi-select, all-by-default). Both KQL types apply user
    # filtering — for AppTraces directly, for AppMetrics via parse_json.
    templating = dst.setdefault("templating", {"list": []})
    tvars = templating.setdefault("list", [])
    # Strip any pre-existing copies so re-runs are idempotent.
    tvars = [v for v in tvars if v.get("name") not in ("law_resource", "users")]
    tvars.insert(
        0,
        {
            "name": "law_resource",
            "type": "textbox",
            "label": "Log Analytics Workspace Resource ID",
            "description": "Paste the full resource ID, e.g. /subscriptions/xxx/resourceGroups/agent-otel-rg/providers/Microsoft.OperationalInsights/workspaces/agent-otel-law",
            "query": "",
            "current": {"selected": False, "text": "", "value": ""},
            "hide": 2,
            "skipUrlSync": False,
        },
    )
    tvars.append(
        {
            "name": "users",
            "type": "query",
            "label": "Users",
            "description": "Multi-select. 'All' expands to every distinct user.email seen in the dashboard window.",
            "datasource": AZURE_DS,
            "query": {
                "queryType": "Azure Log Analytics",
                "azureLogAnalytics": {
                    "query": (
                        # NOTE: this is the variable's OWN source query —
                        # it must NOT reference $users (would be self-
                        # referential and Grafana sends the literal placeholder
                        # to Azure Monitor, which 400s). Earlier versions of
                        # this file accidentally had ${users:doublequote}
                        # injected here by the bulk query-patcher.
                        "AppTraces\n"
                        "| where $__timeFilter(TimeGenerated)\n"
                        "| extend email = tostring(Properties['user.email'])\n"
                        "| where isnotempty(email)\n"
                        "| distinct email\n"
                        "| order by email asc"
                    ),
                    "resource": LA_RESOURCE_VAR,
                    "resultFormat": "table",
                },
                "refId": "users",
            },
            "refresh": 2,
            "multi": True,
            "includeAll": True,
            "allValue": None,
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
            "hide": 0,
            "skipUrlSync": False,
        }
    )
    templating["list"] = tvars

    # Rewrite panels, append the Azure-only User Prompts panel, then apply
    # Azure-specific layout overrides so widths/positions match the new rules.
    dst["panels"] = [rewrite_panel(p) for p in src.get("panels", [])]
    dst["panels"].append(make_user_prompts_panel())
    apply_layout(dst["panels"])

    DST.write_text(json.dumps(dst, indent=2) + "\n")
    print(f"Wrote {DST} ({DST.stat().st_size} bytes, {len(dst['panels'])} panels)")


if __name__ == "__main__":
    main()
