#!/usr/bin/env python3
"""Generate claude-code-investigate.azure.json — the deep-dive dashboard.

Fills the niche the operational + adoption + cost dashboards leave open:
ad-hoc "is alice using the WebFetch tool?", "who's prompting about
'security'?", "what tools are used most in agent-otel?" — questions that
combine multiple filter dimensions on the fly.

Filter dimensions (template variables at the top of the dashboard):
  - $users         multi-select user.email (all by default)
  - $tools         multi-select tool name from tool_result events
  - $projects      multi-select project.name
  - $models        multi-select model name from api_request events
  - $prompt_search textbox; substring match (case-insensitive) against
                   user_prompt text. Empty = no filter.
  - $tool_search   textbox; substring match against tool_result name. An
                   alternative to $tools when you want partial matching.

All panels apply the same filter set so the dashboard reads as one
coherent slice of the data.
"""

import json
from pathlib import Path

DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-investigate.azure.json")

AZURE_DS = {"type": "grafana-azure-monitor-datasource", "uid": "${DS_AZURE_MONITOR}"}
LA_RESOURCE_VAR = "$law_resource"


# ── target / panel helpers ────────────────────────────────────────────


def kql_target(query, result_format="table", legend="", ref_id="A"):
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


def table_panel(pid, title, x, y, w, h, query, description=""):
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "description": description,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
        "options": {"showHeader": True, "footer": {"show": False}},
        "targets": [kql_target(query, "table")],
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
                "calcs": ["mean", "max"],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [kql_target(query, "time_series", legend=legend)],
    }


def logs_panel(pid, title, x, y, w, h, query, description=""):
    return {
        "id": pid,
        "type": "logs",
        "title": title,
        "description": description,
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


def row(title, y):
    return {
        "type": "row",
        "title": title,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "collapsed": False,
    }


# ── filter clauses ────────────────────────────────────────────────────
#
# Multi-select with includeAll=true and no Custom all value: Grafana
# expands "All" to the full list of options from the variable's source
# query, so `in (...)` filters always have concrete values.
#
# Textbox with :doublequote: empty input expands to `""`, which makes
# `column contains ""` match every row (KQL: every string contains the
# empty string). So no extra strlen guard needed.

USER_FILTER = "| where tostring(Properties['user.email']) in (${users:doublequote})"
PROJECT_FILTER = (
    "| where isempty(tostring(Properties['project.name']))"
    " or tostring(Properties['project.name']) in (${projects:doublequote})"
)
TOOL_FILTER_AT_TOOL_RESULT = (
    "| where tostring(Properties['tool_name']) in (${tools:doublequote})"
)
MODEL_FILTER_AT_API_REQUEST = (
    "| where tostring(Properties['model']) in (${models:doublequote})"
)
PROMPT_SEARCH = (
    "| where tostring(Properties['prompt']) contains ${prompt_search:doublequote}"
)
TOOL_NAME_SEARCH = (
    "| where tostring(Properties['tool_name']) contains ${tool_search:doublequote}"
)


# ── queries ───────────────────────────────────────────────────────────

# Scope-summary stats — counts within the active filter set.
Q_MATCHING_PROMPTS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "user_prompt"
{USER_FILTER}
{PROJECT_FILTER}
{PROMPT_SEARCH}
| summarize n = count()
"""

Q_MATCHING_TOOL_CALLS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
{USER_FILTER}
{PROJECT_FILTER}
{TOOL_FILTER_AT_TOOL_RESULT}
{TOOL_NAME_SEARCH}
| summarize n = count()
"""

Q_MATCHING_USERS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER}
{PROJECT_FILTER}
| summarize n = dcount(tostring(Properties['user.email']))
"""

Q_MATCHING_SESSIONS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER}
{PROJECT_FILTER}
| summarize n = dcount(tostring(Properties['session.id']))
"""

# Recent prompts matching the filter.
Q_PROMPTS_MATCHING = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "user_prompt"
{USER_FILTER}
{PROJECT_FILTER}
{PROMPT_SEARCH}
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
| take 100
"""

# Top tools used within filter.
Q_TOP_TOOLS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
{USER_FILTER}
{PROJECT_FILTER}
{TOOL_FILTER_AT_TOOL_RESULT}
{TOOL_NAME_SEARCH}
| summarize calls = count() by tool = tostring(Properties['tool_name'])
| order by calls desc
"""

# Top users in scope — events / sessions / tool calls.
Q_TOP_USERS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER}
{PROJECT_FILTER}
| extend email = tostring(Properties['user.email']),
         event_name = tostring(Properties['event.name'])
| summarize
    prompts = countif(event_name == "user_prompt"),
    tool_calls = countif(event_name == "tool_result"),
    sessions = dcount(tostring(Properties['session.id'])),
    events = count()
  by email
| order by events desc
"""

# Pivoted tool × user matrix (within filter set).
Q_TOOL_USER_MATRIX = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
{USER_FILTER}
{PROJECT_FILTER}
{TOOL_FILTER_AT_TOOL_RESULT}
{TOOL_NAME_SEARCH}
| extend email = tostring(Properties['user.email']),
         tool = tostring(Properties['tool_name'])
| summarize calls = count() by email, tool
| evaluate pivot(tool, sum(calls))
"""

# Recent matching tool calls, formatted line-per-row.
Q_TOOL_CALLS_MATCHING = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_result"
{USER_FILTER}
{PROJECT_FILTER}
{TOOL_FILTER_AT_TOOL_RESULT}
{TOOL_NAME_SEARCH}
| project
    TimeGenerated,
    Message = strcat(
        "[", tostring(Properties['user.email']), "] ",
        "[", tostring(Properties['tool_name']), "] ",
        iif(tostring(Properties['success']) == "true", "✅", "❌"), " ",
        tostring(Properties['duration_ms']), "ms",
        iif(isnotempty(tostring(Properties['error'])),
            strcat(" ERROR: ", tostring(Properties['error'])), ""))
| order by TimeGenerated desc
| take 100
"""

# Per-project breakdown.
Q_BY_PROJECT = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER}
{PROJECT_FILTER}
| extend proj = tostring(Properties['project.name']),
         event_name = tostring(Properties['event.name'])
| where isnotempty(proj)
| summarize
    prompts = countif(event_name == "user_prompt"),
    tool_calls = countif(event_name == "tool_result"),
    users = dcount(tostring(Properties['user.email'])),
    sessions = dcount(tostring(Properties['session.id']))
  by proj
| order by prompts desc
"""

# Activity over time within filter (timeseries).
Q_ACTIVITY_OVER_TIME = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER}
{PROJECT_FILTER}
| extend event_name = tostring(Properties['event.name'])
| summarize
    Prompts = countif(event_name == "user_prompt"),
    ToolCalls = countif(event_name == "tool_result"),
    APIRequests = countif(event_name == "api_request")
  by bin(TimeGenerated, 1h)
| order by TimeGenerated asc
"""

# API requests in scope by model.
Q_API_BY_MODEL = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "api_request"
{USER_FILTER}
{PROJECT_FILTER}
{MODEL_FILTER_AT_API_REQUEST}
| summarize requests = count() by model = tostring(Properties['model'])
| order by requests desc
"""

# Tool decisions in scope.
Q_TOOL_DECISIONS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "tool_decision"
{USER_FILTER}
{PROJECT_FILTER}
| extend tool = tostring(Properties['tool_name']),
         decision = tostring(Properties['decision'])
| summarize n = count() by tool, decision
| evaluate pivot(decision, sum(n))
| order by tool asc
"""

# Recent API errors in scope.
Q_API_ERRORS_MATCHING = f"""AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "api_error"
{USER_FILTER}
{PROJECT_FILTER}
{MODEL_FILTER_AT_API_REQUEST}
| project
    TimeGenerated,
    Message = strcat(
        "[", tostring(Properties['user.email']), "] ",
        "[", tostring(Properties['model']), "] HTTP ",
        tostring(Properties['status_code']), " ",
        tostring(Properties['duration_ms']), "ms — ",
        tostring(Properties['error']))
| order by TimeGenerated desc
| take 50
"""


# ── layout ────────────────────────────────────────────────────────────

panels = []
pid = 500


def next_id():
    global pid
    pid += 1
    return pid


# Section: Filter scope summary
panels.append(row("🔍 Scope (matches the active filter set)", y=0))
panels.append(
    stat_panel(
        next_id(),
        "Matching prompts",
        0,
        1,
        Q_MATCHING_PROMPTS,
        description="user_prompt events that match all active filters (users, projects, prompt_search).",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Matching tool calls",
        6,
        1,
        Q_MATCHING_TOOL_CALLS,
        description="tool_result events that match all active filters (users, projects, tools, tool_search).",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Distinct users",
        12,
        1,
        Q_MATCHING_USERS,
        description="Distinct user.email values among events matching the user + project filters.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Distinct sessions",
        18,
        1,
        Q_MATCHING_SESSIONS,
        description="Distinct session.id values among events matching the user + project filters.",
    )
)

# Section: Prompts
panels.append(row("📝 Prompts (filtered by $prompt_search)", y=5))
panels.append(
    logs_panel(
        next_id(),
        "Recent matching prompts (last 100)",
        0,
        6,
        24,
        12,
        Q_PROMPTS_MATCHING,
        description="user_prompt events matching $users, $projects, and $prompt_search. Use $prompt_search to filter by a substring of the prompt text — e.g., 'security', '/devloop', 'agent-otel'. Case-insensitive.",
    )
)

# Section: Tools
panels.append(row("🔧 Tools (filtered by $tools / $tool_search)", y=18))
panels.append(
    barchart_panel(
        next_id(),
        "Top tools in scope",
        0,
        19,
        12,
        9,
        Q_TOP_TOOLS,
        description="tool_result events grouped by tool name, within the filter set.",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Tool calls by user × tool",
        12,
        19,
        12,
        9,
        Q_TOOL_USER_MATRIX,
        description="Pivot table — rows are users, columns are tools. Useful for 'is alice using the WebFetch tool' answers at a glance.",
    )
)
panels.append(
    logs_panel(
        next_id(),
        "Recent matching tool calls (last 100)",
        0,
        28,
        24,
        12,
        Q_TOOL_CALLS_MATCHING,
        description="tool_result events with one-line summaries: user, tool, success/fail, duration, error if any.",
    )
)

# Section: Per dimension
panels.append(row("📊 Breakdowns (within filter set)", y=40))
panels.append(
    table_panel(
        next_id(),
        "Top users in scope",
        0,
        41,
        12,
        9,
        Q_TOP_USERS,
        description="Per-user counts of prompts, tool calls, and sessions matching the active filters.",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Per-project breakdown in scope",
        12,
        41,
        12,
        9,
        Q_BY_PROJECT,
        description="Per-project counts. Requires the agent-otel launcher to be installed for project attribution.",
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "Activity over time (filtered)",
        0,
        50,
        24,
        8,
        Q_ACTIVITY_OVER_TIME,
        description="Hourly counts of prompts / tool calls / API requests within the filter set.",
    )
)

# Section: API requests + errors
panels.append(row("🤖 API requests + errors (filtered by $models)", y=58))
panels.append(
    barchart_panel(
        next_id(),
        "API requests by model",
        0,
        59,
        12,
        8,
        Q_API_BY_MODEL,
        description="api_request events grouped by model name.",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Tool decisions (accept/deny patterns)",
        12,
        59,
        12,
        8,
        Q_TOOL_DECISIONS,
        description="tool_decision events pivoted by decision per tool. Shows where users explicitly accept or block tool requests.",
    )
)
panels.append(
    logs_panel(
        next_id(),
        "Recent matching API errors (last 50)",
        0,
        67,
        24,
        10,
        Q_API_ERRORS_MATCHING,
        description="api_error events matching the filters.",
    )
)


# ── envelope ──────────────────────────────────────────────────────────


def _multi_select_var(name, label, source_query, description):
    """A standard multi-select query-driven variable."""
    return {
        "name": name,
        "type": "query",
        "label": label,
        "description": description,
        "datasource": AZURE_DS,
        "query": {
            "queryType": "Azure Log Analytics",
            "azureLogAnalytics": {
                "query": source_query,
                "resource": LA_RESOURCE_VAR,
                "resultFormat": "table",
            },
            "refId": name,
        },
        "refresh": 2,
        "multi": True,
        "includeAll": True,
        "allValue": None,
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "hide": 0,
        "skipUrlSync": False,
    }


def _textbox_var(name, label, description):
    return {
        "name": name,
        "type": "textbox",
        "label": label,
        "description": description,
        "query": "",
        "current": {"selected": False, "text": "", "value": ""},
        "hide": 0,
        "skipUrlSync": False,
    }


dashboard = {
    "annotations": {"list": []},
    "description": (
        "Ad-hoc deep-dive dashboard. All panels respect the same filter set "
        "($users, $tools, $projects, $models, $prompt_search, $tool_search) "
        "so the whole page reads as one slice of the data. Use it to answer "
        "questions like 'who's using the WebFetch tool?', 'is alice prompting "
        "about security topics?', 'what tools dominate in the agent-otel "
        "project?'."
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
    "tags": ["claude-code", "observability", "investigate"],
    "templating": {
        "list": [
            {
                "name": "law_resource",
                "type": "textbox",
                "label": "Log Analytics Workspace Resource ID",
                "description": "Pre-populated by the upload script.",
                "query": "",
                "current": {"selected": False, "text": "", "value": ""},
                "hide": 2,
                "skipUrlSync": False,
            },
            _multi_select_var(
                "users",
                "Users",
                source_query=(
                    "AppTraces\n"
                    "| where $__timeFilter(TimeGenerated)\n"
                    "| extend email = tostring(Properties['user.email'])\n"
                    "| where isnotempty(email)\n"
                    "| distinct email\n"
                    "| order by email asc"
                ),
                description="Multi-select. 'All' expands to every distinct user.email seen in the dashboard window.",
            ),
            _multi_select_var(
                "tools",
                "Tools",
                source_query=(
                    "AppTraces\n"
                    "| where $__timeFilter(TimeGenerated)\n"
                    "| where tostring(Properties['event.name']) == \"tool_result\"\n"
                    "| extend tool = tostring(Properties['tool_name'])\n"
                    "| where isnotempty(tool)\n"
                    "| distinct tool\n"
                    "| order by tool asc"
                ),
                description="Multi-select tool names from tool_result events.",
            ),
            _multi_select_var(
                "projects",
                "Projects",
                source_query=(
                    "AppTraces\n"
                    "| where $__timeFilter(TimeGenerated)\n"
                    "| extend proj = tostring(Properties['project.name'])\n"
                    "| where isnotempty(proj)\n"
                    "| distinct proj\n"
                    "| order by proj asc"
                ),
                description="Multi-select project.name (from the agent-otel launcher resource attributes).",
            ),
            _multi_select_var(
                "models",
                "Models",
                source_query=(
                    "AppTraces\n"
                    "| where $__timeFilter(TimeGenerated)\n"
                    "| where tostring(Properties['event.name']) == \"api_request\"\n"
                    "| extend model = tostring(Properties['model'])\n"
                    "| where isnotempty(model)\n"
                    "| distinct model\n"
                    "| order by model asc"
                ),
                description="Multi-select Anthropic model name from api_request events.",
            ),
            _textbox_var(
                "prompt_search",
                "Prompt search",
                description="Substring filter (case-insensitive) against user_prompt text. Empty = no filter. Examples: 'security', '/devloop', 'agent-otel'.",
            ),
            _textbox_var(
                "tool_search",
                "Tool name search",
                description="Substring filter against tool_result tool name. Empty = no filter. Useful for partial matching alongside the multi-select $tools.",
            ),
        ]
    },
    "time": {"from": "now-7d", "to": "now"},
    "timepicker": {},
    "timezone": "",
    "title": "Agent Observability — Investigate",
    "uid": "agent-otel-investigate",
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


def main():
    DST.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(f"Wrote {DST} ({DST.stat().st_size} bytes, {len(panels)} panels)")


if __name__ == "__main__":
    main()
