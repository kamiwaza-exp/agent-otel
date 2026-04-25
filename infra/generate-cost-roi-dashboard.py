#!/usr/bin/env python3
"""Generate claude-code-cost-roi.azure.json — cost / ROI view.

Theme: estimating return on Max-plan costs and surfacing cost outliers.
Cost values are computed client-side at standard API rates regardless of
the user's plan (Max, pay-as-you-go API, Bedrock, Vertex). The Anthropic
docs explicitly call out that this metric is an approximation, not a bill.
We can't distinguish API vs Max from telemetry — that audit is out-of-band.
"""

import json
from pathlib import Path

DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-cost-roi.azure.json")

AZURE_DS = {"type": "grafana-azure-monitor-datasource", "uid": "${DS_AZURE_MONITOR}"}
LA_RESOURCE_VAR = "$law_resource"


def kql_target(
    query: str, result_format: str = "table", legend: str = "", ref_id: str = "A"
) -> dict:
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


def stat_panel(pid, title, x, y, query, w=6, h=4, unit="short", description=""):
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
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
            "reduceOptions": {"values": False, "calcs": ["lastNotNull"], "fields": ""},
            "textMode": "auto",
        },
        "targets": [kql_target(query, "table")],
    }


def table_panel(pid, title, x, y, w, h, query, description="", overrides=None):
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "description": description,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto"}},
            "overrides": overrides or [],
        },
        "options": {"showHeader": True, "footer": {"show": False}},
        "targets": [kql_target(query, "table")],
    }


def timeseries_panel(
    pid, title, x, y, w, h, query, unit="short", legend="", description=""
):
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
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
                "calcs": ["sum", "max"],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [kql_target(query, "time_series", legend=legend)],
    }


def barchart_panel(pid, title, x, y, w, h, query, description=""):
    return {
        "id": pid,
        "type": "barchart",
        "title": title,
        "description": description,
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


def text_panel(pid, title, x, y, w, h, content):
    return {
        "id": pid,
        "type": "text",
        "title": title,
        "datasource": None,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {"mode": "markdown", "content": content},
    }


def row(title, y):
    return {
        "type": "row",
        "title": title,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "collapsed": False,
    }


# ── shared filter clauses ─────────────────────────────────────────────
USER_FILTER_METRICS = (
    "| extend _p = parse_json(Properties)\n"
    "| where tostring(_p['user.email']) in (${users:doublequote})"
)


# ── queries ───────────────────────────────────────────────────────────

Q_TOTAL_SHADOW_COST = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
{USER_FILTER_METRICS}
| summarize ShadowCost = sum(Sum)
"""

Q_AVG_PER_USER_PER_DAY = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
{USER_FILTER_METRICS}
| extend email = tostring(_p['user.email']), day = bin(TimeGenerated, 1d)
| summarize daily_total = sum(Sum) by email, day
| summarize AvgPerUserPerDay = round(avg(daily_total), 2)
"""

# Break-even = total shadow cost / max plan monthly fee.
# Equivalent to "this many people would have hit the Max plan break-even."
Q_BREAK_EVEN_USERS = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
{USER_FILTER_METRICS}
| summarize total = sum(Sum)
| extend BreakEvenUsers = round(total / todouble(${{max_plan_monthly_usd}}), 2)
| project BreakEvenUsers
"""

# Distinct paying users (so the per-user calc means something).
Q_DISTINCT_USERS = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
{USER_FILTER_METRICS}
| summarize Users = dcount(tostring(_p['user.email']))
"""

Q_SHADOW_COST_TIMESERIES = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
{USER_FILTER_METRICS}
| summarize ShadowCost = sum(Sum) by bin(TimeGenerated, 1d)
| order by TimeGenerated asc
"""

# Per-user ROI table — shadow cost vs Max plan fee. Delta < 0 = Max overpaid
# for this user (they didn't generate enough activity to break even).
# Delta > 0 = Max saved them money.
Q_PER_USER_ROI = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
{USER_FILTER_METRICS}
| extend email = tostring(_p['user.email'])
| summarize ShadowCost = round(sum(Sum), 2) by email
| extend MaxPlanFee = todouble(${{max_plan_monthly_usd}})
| extend Delta = round(ShadowCost - MaxPlanFee, 2)
| extend ROI_Status = case(
    Delta > 0,  "Max saved money",
    Delta < 0,  "Max overpaid",
    "break even")
| project email, ShadowCost, MaxPlanFee, Delta, ROI_Status
| order by Delta desc
"""

Q_COST_BY_MODEL = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.cost.usage"
{USER_FILTER_METRICS}
| extend model = tostring(_p['model'])
| summarize Cost = sum(Sum) by model
| order by Cost desc
"""

# Token mix per user — input / output / cacheRead / cacheCreation
Q_TOKEN_MIX_PER_USER = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.token.usage"
{USER_FILTER_METRICS}
| extend email = tostring(_p['user.email']), type = tostring(_p['type'])
| summarize Tokens = sum(Sum) by email, type
| order by email asc, type asc
"""

# Cost per prompt by user — joins per-user cost (AppMetrics) and per-user
# prompt count (AppTraces).
Q_COST_PER_PROMPT_BY_USER = """let _cost = AppMetrics
  | where $__timeFilter(TimeGenerated)
  | where Name == "claude_code.cost.usage"
  | extend p = parse_json(Properties)
  | extend email = tostring(p['user.email'])
  | where tostring(p['user.email']) in (${users:doublequote})
  | summarize ShadowCost = sum(Sum) by email;
let _prompts = AppTraces
  | where $__timeFilter(TimeGenerated)
  | where tostring(Properties['event.name']) == "user_prompt"
  | extend email = tostring(Properties['user.email'])
  | where tostring(Properties['user.email']) in (${users:doublequote})
  | summarize Prompts = count() by email;
_cost
| join kind=inner _prompts on email
| extend CostPerPrompt = round(ShadowCost / todouble(Prompts), 4)
| project email, Prompts, ShadowCost = round(ShadowCost, 2), CostPerPrompt
| order by CostPerPrompt desc
"""


# ── layout ────────────────────────────────────────────────────────────

panels: list[dict] = []
pid = 400


def next_id() -> int:
    global pid
    pid += 1
    return pid


# Org-level shadow cost
panels.append(row("💰 Org-level shadow cost", y=0))
panels.append(
    stat_panel(
        next_id(),
        "Total shadow cost (window)",
        0,
        1,
        Q_TOTAL_SHADOW_COST,
        w=6,
        h=4,
        unit="currencyUSD",
        description="Sum of claude_code.cost.usage across selected users. NOT a bill — see Notes section.",
    )
)
panels.append(
    stat_panel(next_id(), "Distinct users (window)", 6, 1, Q_DISTINCT_USERS, w=6, h=4)
)
panels.append(
    stat_panel(
        next_id(),
        "Avg shadow cost per user/day",
        12,
        1,
        Q_AVG_PER_USER_PER_DAY,
        w=6,
        h=4,
        unit="currencyUSD",
        description="Average daily shadow cost per active user. Rough sense of an individual's pace.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Break-even users (vs $max_plan_monthly_usd)",
        18,
        1,
        Q_BREAK_EVEN_USERS,
        w=6,
        h=4,
        description="Total shadow cost ÷ $max_plan_monthly_usd. 'This many people would have broken even on the Max plan if they were paying API rates instead.'",
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "Shadow cost over time",
        0,
        5,
        24,
        8,
        Q_SHADOW_COST_TIMESERIES,
        unit="currencyUSD",
        legend="cost",
        description="Daily total shadow cost (selected users). Note: this is a delta-temporality metric — daily sum is meaningful, cumulative trend tells you usage growth.",
    )
)

# Per-user ROI
panels.append(row("📉 Per-user ROI", y=13))
roi_overrides = [
    {
        "matcher": {"id": "byName", "options": "Delta"},
        "properties": [
            {
                "id": "custom.cellOptions",
                "value": {"type": "color-background", "mode": "gradient"},
            },
            {"id": "color", "value": {"mode": "continuous-RdYlGr"}},
            {"id": "unit", "value": "currencyUSD"},
        ],
    },
    {
        "matcher": {"id": "byName", "options": "ShadowCost"},
        "properties": [{"id": "unit", "value": "currencyUSD"}],
    },
    {
        "matcher": {"id": "byName", "options": "MaxPlanFee"},
        "properties": [{"id": "unit", "value": "currencyUSD"}],
    },
]
panels.append(
    table_panel(
        next_id(),
        "Per-user ROI (shadow cost vs Max plan fee)",
        0,
        14,
        24,
        10,
        Q_PER_USER_ROI,
        description=(
            "Per-user shadow cost compared to the configured Max plan monthly fee ($max_plan_monthly_usd). "
            "Delta = ShadowCost − MaxPlanFee. Positive (green) means the user's API-rate equivalent activity exceeded the Max fee — Max saved money. "
            "Negative (red) means the user didn't hit the break-even — Max overpaid for them. "
            "$max_plan_monthly_usd is editable at the top of the dashboard."
        ),
        overrides=roi_overrides,
    )
)

# Cost shape
panels.append(row("🤖 Cost shape", y=24))
panels.append(
    barchart_panel(
        next_id(),
        "Shadow cost by model",
        0,
        25,
        12,
        9,
        Q_COST_BY_MODEL,
        description="Where the shadow cost concentrates by model. Heavy Opus where Sonnet would do = optimization opportunity.",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Token mix per user (input / output / cache)",
        12,
        25,
        12,
        9,
        Q_TOKEN_MIX_PER_USER,
        description="Token usage broken down by type. cacheRead-heavy = healthy reuse of context. Low cacheRead with high input = re-prompting from scratch repeatedly (paying for context every call).",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Cost per prompt by user",
        0,
        34,
        24,
        9,
        Q_COST_PER_PROMPT_BY_USER,
        description="Shadow cost ÷ prompt count, per user. Outliers signal misuse, experimentation, or unusually expensive interaction patterns. Useful when paired with the User Profile dashboard for that user.",
    )
)

# Notes
panels.append(row("📌 Notes", y=43))
panels.append(
    text_panel(
        next_id(),
        "About this dashboard",
        0,
        44,
        24,
        8,
        content=(
            "**Cost is an approximation, not a bill.**\n\n"
            "`claude_code.cost.usage` is computed client-side by Claude Code at standard "
            "Anthropic API rates regardless of the user's actual plan (Max, pay-as-you-go API, "
            "AWS Bedrock, GCP Vertex). For Max-plan users this is a *shadow cost* — what the "
            "same activity would cost at API rates. Don't reconcile against an invoice.\n\n"
            "**API-vs-Max detection is not available from telemetry.**\n\n"
            "Claude Code does not emit any auth-mode, plan-tier, or subscription attribute. "
            "There's no signal in the data that distinguishes an API-key user from a Max-plan "
            "user. The recommended audit pattern is out-of-band: maintain a roster of who's on "
            "Max, periodically diff against the distinct `user.email` values in `AppTraces`, "
            "flag anyone in telemetry not on the roster as 'probably API'.\n\n"
            "**ROI math.**\n\n"
            "The break-even calculation assumes `$max_plan_monthly_usd` is the monthly Max plan "
            "fee and that the dashboard window aligns with a monthly billing cycle. For windows "
            "longer than 30 days, the per-user ROI table compares the *full window* shadow cost "
            "to a single month's Max fee — adjust `$max_plan_monthly_usd` if you want a different "
            "comparison (e.g., set to 300 to compare against a quarter on Max).\n\n"
            "See `CLAUDE_OBSERVABILITY.md` for Anthropic's own caveat on the cost metric."
        ),
    )
)


# ── envelope ──────────────────────────────────────────────────────────

dashboard = {
    "annotations": {"list": []},
    "description": (
        "Cost / ROI view for the Claude Code observability stack. Estimates "
        "return on Max-plan investment via shadow-cost vs plan-fee comparison. "
        "Cost values are approximations — see the 📌 Notes section."
    ),
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "id": None,
    "links": [],
    "liveNow": False,
    "panels": panels,
    "refresh": "5m",
    "schemaVersion": 27,
    "style": "dark",
    "tags": ["claude-code", "observability", "cost", "roi"],
    "templating": {
        "list": [
            {
                "name": "law_resource",
                "type": "textbox",
                "label": "Log Analytics Workspace Resource ID",
                "description": "Paste the full resource ID of the Log Analytics workspace.",
                "query": "",
                "current": {"selected": False, "text": "", "value": ""},
                "hide": 2,
                "skipUrlSync": False,
            },
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
            },
            {
                "name": "max_plan_monthly_usd",
                "type": "textbox",
                "label": "Max plan monthly fee (USD)",
                "description": "Used for break-even and per-user ROI calculations. Edit to match your actual subscription cost.",
                "query": "200",
                "current": {"selected": False, "text": "200", "value": "200"},
                "hide": 0,
                "skipUrlSync": False,
            },
        ]
    },
    "time": {"from": "now-30d", "to": "now"},
    "timepicker": {},
    "timezone": "",
    "title": "Agent Observability — Cost / ROI",
    "uid": "agent-otel-cost-roi",
    "version": 1,
    "__inputs": [
        {
            "name": "DS_AZURE_MONITOR",
            "label": "Azure Monitor",
            "description": "Azure Monitor data source with access to the Log Analytics workspace.",
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
