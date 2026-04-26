#!/usr/bin/env python3
"""Generate claude-code-user-profile.azure.json — per-user drilldown.

Single-select $user. All panels filter to that user. Pairs with the Team
Adoption dashboard (which is the multi-user aggregate view) and the Cost
ROI dashboard.
"""

import json
from pathlib import Path

DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-user-profile.azure.json")

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


def heatmap_table_panel(pid, title, x, y, w, h, query, description=""):
    """Hour×day heatmap rendered as a Table with per-day-column cell coloring.

    Grafana's native Heatmap can't read categorical-X data; the Table
    panel with cell-color overrides produces a discrete grid that's far
    more reliable. Expects KQL output columns
    (hour_of_day, Mon, Tue, Wed, Thu, Fri, Sat, Sun).
    """
    day_cols = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    overrides = []
    for col in day_cols:
        overrides.append(
            {
                "matcher": {"id": "byName", "options": col},
                "properties": [
                    {
                        "id": "custom.cellOptions",
                        "value": {"type": "color-background", "mode": "gradient"},
                    },
                    {"id": "color", "value": {"mode": "continuous-RdYlGr"}},
                    {"id": "min", "value": 0},
                    {"id": "custom.align", "value": "center"},
                ],
            }
        )
    overrides.append(
        {
            "matcher": {"id": "byName", "options": "hour_of_day"},
            "properties": [
                {"id": "custom.align", "value": "right"},
                {"id": "custom.width", "value": 90},
            ],
        }
    )
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "description": description,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {
            "defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
            "overrides": overrides,
        },
        "options": {"showHeader": True, "footer": {"show": False}},
        "targets": [kql_target(query, "table")],
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


# ── shared filter clauses ─────────────────────────────────────────────
# $users is multi-select. With `Include All = true` and no Custom all
# value, "All" expands to every distinct user.email from the variable's
# source query, so `in (...)` always sees a concrete list.
USER_FILTER_TRACES = (
    "| where tostring(Properties['user.email']) in (${users:doublequote})"
)
USER_FILTER_METRICS = (
    "| extend _p = parse_json(Properties)\n"
    "| where tostring(_p['user.email']) in (${users:doublequote})"
)


# ── queries ───────────────────────────────────────────────────────────

Q_IDENTITY_HEADER = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| extend email = tostring(Properties['user.email'])
| summarize
    sessions = dcount(tostring(Properties['session.id'])),
    first_seen = min(TimeGenerated),
    last_seen = max(TimeGenerated)
"""

Q_IDENTITY_SESSIONS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| summarize sessions = dcount(tostring(Properties['session.id']))
"""

Q_IDENTITY_PROMPTS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| where tostring(Properties['event.name']) == "user_prompt"
| summarize prompts = count()
"""

Q_IDENTITY_DAYS_ACTIVE = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| where tostring(Properties['event.name']) == "user_prompt"
| summarize active_days = count_distinct(bin(TimeGenerated, 1d))
"""

Q_PERSONAL_HEATMAP = f"""// Same synthetic-grid + pivot pattern as the team adoption dashboard:
// build a full 24×7 grid first so the table panel always has all seven
// day columns, even when the user's data is sparse.
let _grid = range hour_of_day from 0 to 23 step 1
  | extend _j = 1
  | join kind=fullouter (
      datatable(dow_idx:int, dow_label:string)
        [0,"Mon", 1,"Tue", 2,"Wed", 3,"Thu", 4,"Fri", 5,"Sat", 6,"Sun"]
      | extend _j = 1
    ) on _j
  | project hour_of_day, dow_idx, dow_label;
let _actual = AppTraces
  | where TimeGenerated > ago(90d)
  {USER_FILTER_TRACES}
  | where tostring(Properties['event.name']) == "user_prompt"
  // Per-user timezone shift: TimeGenerated is UTC; shift by the offset the
  // launcher captured at session start so the heatmap reads in this user's
  // local hours. Records without the attribute default to UTC.
  | extend tz_off_min = coalesce(toint(tostring(Properties['host.tz_offset_minutes'])), 0)
  | extend local_time = TimeGenerated + tz_off_min * 1m
  | extend hour_of_day = datetime_part("hour", local_time)
  | extend dow_idx = case(
      dayofweek(local_time) == 0d, 6,
      dayofweek(local_time) == 1d, 0,
      dayofweek(local_time) == 2d, 1,
      dayofweek(local_time) == 3d, 2,
      dayofweek(local_time) == 4d, 3,
      dayofweek(local_time) == 5d, 4,
      5)
  | summarize prompts = count() by hour_of_day, dow_idx;
_grid
| join kind=leftouter _actual on hour_of_day, dow_idx
| extend prompts = coalesce(prompts, long(0))
| project hour_of_day, dow_label, prompts
| evaluate pivot(dow_label, sum(prompts))
| project hour_of_day, Mon, Tue, Wed, Thu, Fri, Sat, Sun
| order by hour_of_day asc
"""

Q_PROMPTS_PER_DAY = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| where tostring(Properties['event.name']) == "user_prompt"
| summarize prompts = count() by bin(TimeGenerated, 1d)
| order by TimeGenerated asc
"""

Q_SESSIONS_PER_DAY = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| summarize sessions = dcount(tostring(Properties['session.id'])) by bin(TimeGenerated, 1d)
| order by TimeGenerated asc
"""

# Avg prompts/session by day = total prompts / distinct sessions in that day.
Q_PROMPTS_PER_SESSION_OVER_TIME = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| where tostring(Properties['event.name']) == "user_prompt"
| extend day = bin(TimeGenerated, 1d), session = tostring(Properties['session.id'])
| summarize prompts = count(), sessions = dcount(session) by day
| extend prompts_per_session = round(todouble(prompts) / sessions, 2)
| project TimeGenerated = day, prompts_per_session
| order by TimeGenerated asc
"""


# Effectiveness — per session, computed daily.
# claude_code.commit.count / claude_code.pull_request.count / .lines_of_code.count
# all carry session.id in Properties. Sum the metric across the user's events
# in a day, divide by distinct sessions that day.
def _per_session_query(metric_name: str, type_filter: str = "") -> str:
    # type_filter optionally narrows lines_of_code by type=added|removed.
    extra_filter = ""
    if type_filter:
        extra_filter = f"\n| where tostring(_p['type']) == \"{type_filter}\""
    return f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "{metric_name}"
| extend _p = parse_json(Properties)
| where tostring(_p['user.email']) in (${{users:doublequote}}){extra_filter}
| extend day = bin(TimeGenerated, 1d), session = tostring(_p['session.id'])
| summarize value = sum(Sum), sessions = dcount(session) by day
| extend per_session = round(todouble(value) / sessions, 3)
| project TimeGenerated = day, per_session
| order by TimeGenerated asc
"""


Q_COMMITS_PER_SESSION = _per_session_query("claude_code.commit.count")
Q_PRS_PER_SESSION = _per_session_query("claude_code.pull_request.count")

# +/- LoC per session — two series on the same panel
Q_LOC_ADDED_PER_SESSION = _per_session_query("claude_code.lines_of_code.count", "added")
Q_LOC_REMOVED_PER_SESSION = _per_session_query(
    "claude_code.lines_of_code.count", "removed"
)

# Stats for current per-session averages (window total, not daily binned)
Q_AVG_COMMITS_PER_SESSION = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.commit.count"
{USER_FILTER_METRICS}
| summarize total = sum(Sum), sessions = dcount(tostring(_p['session.id']))
| extend avg = iif(sessions > 0, round(todouble(total) / sessions, 2), 0.0)
| project avg
"""

Q_AVG_PRS_PER_SESSION = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.pull_request.count"
{USER_FILTER_METRICS}
| summarize total = sum(Sum), sessions = dcount(tostring(_p['session.id']))
| extend avg = iif(sessions > 0, round(todouble(total) / sessions, 2), 0.0)
| project avg
"""

Q_AVG_NET_LOC_PER_SESSION = f"""AppMetrics
| where $__timeFilter(TimeGenerated)
| where Name == "claude_code.lines_of_code.count"
{USER_FILTER_METRICS}
| extend t = tostring(_p['type'])
| summarize
    added   = sumif(Sum, t == "added"),
    removed = sumif(Sum, t == "removed"),
    sessions = dcount(tostring(_p['session.id']))
| extend net = added - removed
| extend avg_net_per_session = iif(sessions > 0, round(todouble(net) / sessions, 1), 0.0)
| project avg_net_per_session
"""

Q_TOP_TOOLS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| where tostring(Properties['event.name']) == "tool_result"
| summarize calls = count() by tool = tostring(Properties['tool_name'])
| order by calls desc
"""

Q_TOOL_SUCCESS_RATE = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| where tostring(Properties['event.name']) == "tool_result"
| summarize
    total = count(),
    successes = countif(tostring(Properties['success']) == "true")
  by tool = tostring(Properties['tool_name'])
| extend success_rate_pct = round(100.0 * successes / total, 1)
| order by total desc
"""

Q_TOOLS_PER_PROMPT_OVER_TIME = f"""let prompts = AppTraces
  | where $__timeFilter(TimeGenerated)
  {USER_FILTER_TRACES}
  | where tostring(Properties['event.name']) == "user_prompt"
  | summarize prompts = count() by bin(TimeGenerated, 1d);
let tools = AppTraces
  | where $__timeFilter(TimeGenerated)
  {USER_FILTER_TRACES}
  | where tostring(Properties['event.name']) == "tool_result"
  | summarize tools = count() by bin(TimeGenerated, 1d);
prompts
| join kind=leftouter tools on TimeGenerated
| extend tools_per_prompt = round(todouble(coalesce(tools, 0)) / prompts, 2)
| project TimeGenerated, tools_per_prompt
| order by TimeGenerated asc
"""

Q_PER_PROJECT = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| extend proj = tostring(Properties['project.name'])
| where isnotempty(proj)
| summarize
    sessions = dcount(tostring(Properties['session.id'])),
    prompts = countif(tostring(Properties['event.name']) == "user_prompt"),
    tool_calls = countif(tostring(Properties['event.name']) == "tool_result"),
    first_seen = min(TimeGenerated),
    last_seen = max(TimeGenerated)
  by proj
| order by sessions desc
"""

Q_RECENT_PROMPTS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER_TRACES}
| where tostring(Properties['event.name']) == "user_prompt"
| project
    TimeGenerated,
    Message = strcat(
        "[", coalesce(tostring(Properties['project.name']), "(no project)"), "] ",
        "len=", tostring(Properties['prompt_length']), " ",
        iif(isnotempty(tostring(Properties['prompt'])),
            tostring(Properties['prompt']),
            "<prompt text not logged>"))
| order by TimeGenerated desc
| take 50
"""


# ── layout ────────────────────────────────────────────────────────────

panels: list[dict] = []
pid = 300


def next_id() -> int:
    global pid
    pid += 1
    return pid


# Identity header — 4 small stats
panels.append(row("👤 Identity", y=0))
panels.append(
    stat_panel(next_id(), "Sessions (window)", 0, 1, Q_IDENTITY_SESSIONS, w=6, h=4)
)
panels.append(
    stat_panel(next_id(), "Prompts (window)", 6, 1, Q_IDENTITY_PROMPTS, w=6, h=4)
)
panels.append(
    stat_panel(
        next_id(), "Active days (window)", 12, 1, Q_IDENTITY_DAYS_ACTIVE, w=6, h=4
    )
)
panels.append(
    stat_panel(
        next_id(), "Avg commits / session", 18, 1, Q_AVG_COMMITS_PER_SESSION, w=6, h=4
    )
)

# Personal rhythm
panels.append(row("⏱  Personal rhythm", y=5))
panels.append(
    heatmap_table_panel(
        next_id(),
        "Hour-of-day × day-of-week (last 90d)",
        0,
        6,
        24,
        10,
        Q_PERSONAL_HEATMAP,
        description="When this user submits prompts, last 90 days. Cell = count of user_prompt events at that hour on that weekday. Pivoted into a 24-row × 7-col table with cell-color shading; Grafana's native heatmap panel can't render categorical-X data.",
    )
)

# Engagement intensity
panels.append(row("📈 Engagement intensity", y=16))
panels.append(
    timeseries_panel(
        next_id(), "Prompts per day", 0, 17, 24, 8, Q_PROMPTS_PER_DAY, legend="prompts"
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "Sessions per day",
        0,
        25,
        12,
        8,
        Q_SESSIONS_PER_DAY,
        legend="sessions",
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "Avg prompts per session over time",
        12,
        25,
        12,
        8,
        Q_PROMPTS_PER_SESSION_OVER_TIME,
        legend="prompts/session",
    )
)

# Effectiveness — the four metrics the user asked for
panels.append(row("🎯 Effectiveness", y=33))
panels.append(
    stat_panel(
        next_id(),
        "Avg commits / session (window)",
        0,
        34,
        Q_AVG_COMMITS_PER_SESSION,
        w=8,
        h=4,
    )
)
panels.append(
    stat_panel(
        next_id(), "Avg PRs / session (window)", 8, 34, Q_AVG_PRS_PER_SESSION, w=8, h=4
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Avg net LoC / session (window)",
        16,
        34,
        Q_AVG_NET_LOC_PER_SESSION,
        w=8,
        h=4,
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "Commits per session over time",
        0,
        38,
        12,
        8,
        Q_COMMITS_PER_SESSION,
        legend="commits/session",
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "PRs per session over time",
        12,
        38,
        12,
        8,
        Q_PRS_PER_SESSION,
        legend="PRs/session",
    )
)
# +/- LoC per session — two targets on one panel for paired view
loc_panel = timeseries_panel(
    next_id(),
    "LoC per session over time (added vs removed)",
    0,
    46,
    24,
    8,
    Q_LOC_ADDED_PER_SESSION,
    legend="added",
    description="Two series: lines added per session, lines removed per session. Daily binned.",
)
loc_panel["targets"].append(
    kql_target(Q_LOC_REMOVED_PER_SESSION, "time_series", legend="removed", ref_id="B")
)
panels.append(loc_panel)

# Tool usage
panels.append(row("🔧 Tool usage", y=54))
panels.append(table_panel(next_id(), "Top tools (window)", 0, 55, 12, 9, Q_TOP_TOOLS))
panels.append(
    table_panel(
        next_id(), "Tool success rate by tool", 12, 55, 12, 9, Q_TOOL_SUCCESS_RATE
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "Tools per prompt over time",
        0,
        64,
        24,
        8,
        Q_TOOLS_PER_PROMPT_OVER_TIME,
        legend="tools/prompt",
        description="Daily ratio of tool_result events to user_prompt events. Higher = each ask is doing more work.",
    )
)

# Where time goes
panels.append(row("🗂  Where time goes", y=72))
panels.append(
    table_panel(
        next_id(),
        "Per-project breakdown (window)",
        0,
        73,
        24,
        10,
        Q_PER_PROJECT,
        description="Sessions, prompts, and tool calls by project.name. Requires the agent-otel launcher to be installed for project attribution.",
    )
)

# Recent activity
panels.append(row("📝 Recent activity", y=83))
panels.append(
    logs_panel(
        next_id(),
        "Recent prompts (last 50, window)",
        0,
        84,
        24,
        12,
        Q_RECENT_PROMPTS,
        description="Last 50 user_prompt events for this user. Shows project context, prompt length, and prompt text (server-side redacted for known secret patterns).",
    )
)


# ── envelope ──────────────────────────────────────────────────────────

dashboard = {
    "annotations": {"list": []},
    "description": (
        "User profile drilldown. Pick one or more users via $users. "
        "Shows per-user rhythm, engagement intensity, effectiveness "
        "(commits/PRs/LoC per session, prompts/day), tool usage, "
        "project breakdown, and recent prompts. Aggregates across "
        "the selected users; pick a single user for a focused view "
        "or several to compare. Pairs with the Team Adoption "
        "dashboard (team-wide aggregate)."
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
    "tags": ["claude-code", "observability", "user-profile"],
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
                "description": "Multi-select. 'All' expands to every distinct user.email seen in the dashboard window. Pick one for a single-user profile, or several to compare.",
                "datasource": AZURE_DS,
                "query": {
                    "queryType": "Azure Log Analytics",
                    "azureLogAnalytics": {
                        "query": (
                            "AppTraces\n"
                            "| where TimeGenerated > ago(90d)\n"
                            "| extend email = tostring(Properties['user.email'])\n"
                            "| where isnotempty(email)\n"
                            "| summarize last_seen = max(TimeGenerated) by email\n"
                            "| order by last_seen desc\n"
                            "| project email"
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
        ]
    },
    "time": {"from": "now-90d", "to": "now"},
    "timepicker": {},
    "timezone": "",
    "title": "Agent Observability — User Profile",
    "uid": "agent-otel-user-profile",
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
