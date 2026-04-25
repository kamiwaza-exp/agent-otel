#!/usr/bin/env python3
"""Generate claude-code-team-adoption.azure.json — team adoption dashboard.

Theme: how effectively / consistently / vigorously the team uses AI in
development. Cost is intentionally not a focus — see the cost-roi
dashboard. Operational "what's happening now" is the main dashboard;
this one is the analytical "how is the team trending."

Layout follows the project widths convention:
  - text/log panels: full width (24)
  - chart panels:    half width  (12)
  - stat panels:     quarter (6) or smaller
"""

import json
from pathlib import Path

DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-team-adoption.azure.json")

AZURE_DS = {"type": "grafana-azure-monitor-datasource", "uid": "${DS_AZURE_MONITOR}"}
LA_RESOURCE_VAR = "$law_resource"


# ── target / panel helpers ────────────────────────────────────────────


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


def stat_panel(
    pid: int,
    title: str,
    x: int,
    y: int,
    query: str,
    w: int = 6,
    h: int = 4,
    unit: str = "short",
    description: str = "",
) -> dict:
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
                    "steps": [{"color": "green", "value": None}],
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
    pid: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    query: str,
    description: str = "",
) -> dict:
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
    pid: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    query: str,
    unit: str = "short",
    legend: str = "",
    description: str = "",
) -> dict:
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


def barchart_panel(
    pid: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    query: str,
    description: str = "",
) -> dict:
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


def heatmap_table_panel(
    pid: int,
    title: str,
    x: int,
    y: int,
    w: int,
    h: int,
    query: str,
    description: str = "",
) -> dict:
    """Hour×day heatmap rendered as a Table with per-day-column cell coloring.

    Grafana's native Heatmap panel can't read categorical-X data (dow as
    string + numeric Y), so we lean on the Table panel's cell-color override
    instead — produces a discrete, GitHub-commit-style grid that's far more
    reliable. Expects KQL output with columns
    (hour_of_day, Mon, Tue, Wed, Thu, Fri, Sat, Sun) — see Q_HEATMAP_HOUR_DOW
    for the synthetic-grid + pivot pattern that guarantees all 7 day columns
    exist even when data is sparse.
    """
    day_cols = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    overrides: list[dict] = []
    for col in day_cols:
        overrides.append(
            {
                "matcher": {"id": "byName", "options": col},
                "properties": [
                    {
                        "id": "custom.cellOptions",
                        "value": {"type": "color-background", "mode": "gradient"},
                    },
                    {"id": "color", "value": {"mode": "continuous-BlPu"}},
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


def row(title: str, y: int) -> dict:
    return {
        "type": "row",
        "title": title,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "collapsed": False,
    }


# ── shared filter clauses (KQL) ──────────────────────────────────────
#
# $users multi-select: source query returns distinct user.email; "All"
# expands to all values (no Custom all value set), so we always
# get a concrete list. ${users:doublequote} produces "a","b" suitable for
# `in (...)` matching.

USER_FILTER = "| where tostring(Properties['user.email']) in (${users:doublequote})"

# AppMetrics has Properties as a JSON string — parse first, then access.
USER_FILTER_METRICS = (
    "| extend _p = parse_json(Properties)\n"
    "| where tostring(_p['user.email']) in (${users:doublequote})"
)


# ── queries ──────────────────────────────────────────────────────────


def Q_ACTIVE_USERS(window: str) -> str:
    return f"""AppTraces
| where TimeGenerated > ago({window})
{USER_FILTER}
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize count_distinct = dcount(email)
"""


Q_DAU_WAU_MAU = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER}
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize
    DAU = dcountif(email, TimeGenerated > ago(1d)),
    WAU = dcountif(email, TimeGenerated > ago(7d)),
    MAU = dcountif(email, TimeGenerated > ago(30d))
  by bin(TimeGenerated, 1d)
| order by TimeGenerated asc
"""

Q_HEATMAP_HOUR_DOW = f"""// Synthesize a full 24×7 grid first so the pivot output always has all
// seven day columns even when actual data is sparse (otherwise pivot
// drops missing days and the table panel renders fewer columns).
let _grid = range hour_of_day from 0 to 23 step 1
  | extend _j = 1
  | join kind=fullouter (
      datatable(dow_idx:int, dow_label:string)
        [0,"Mon", 1,"Tue", 2,"Wed", 3,"Thu", 4,"Fri", 5,"Sat", 6,"Sun"]
      | extend _j = 1
    ) on _j
  | project hour_of_day, dow_idx, dow_label;
let _actual = AppTraces
  | where TimeGenerated > ago(30d)
  {USER_FILTER}
  | where tostring(Properties['event.name']) == "user_prompt"
  // Per-user timezone shift: TimeGenerated is UTC, but we want the heatmap
  // to read in each user's local hours/days. host.tz_offset_minutes is set
  // by the agent-otel launcher; records without it default to UTC.
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

# Lapsed = had at least one prompt in the dashboard window but no prompt in
# the last 4 days. 4 covers weekends + occasional Mon/Fri holidays.
Q_LAPSED_USERS = f"""let active_in_window = AppTraces
  | where $__timeFilter(TimeGenerated)
  {USER_FILTER}
  | where tostring(Properties['event.name']) == "user_prompt"
  | extend email = tostring(Properties['user.email'])
  | where isnotempty(email)
  | summarize first_prompt = min(TimeGenerated), last_prompt = max(TimeGenerated) by email;
let recent_active = AppTraces
  | where TimeGenerated > ago(4d)
  | where tostring(Properties['event.name']) == "user_prompt"
  | extend email = tostring(Properties['user.email'])
  | summarize count() by email;
active_in_window
| join kind=leftanti recent_active on email
| extend days_since_last_prompt = datetime_diff('day', now(), last_prompt)
| project email, first_prompt, last_prompt, days_since_last_prompt
| order by days_since_last_prompt asc
"""

Q_NEW_USERS = f"""// First-ever prompt landed within the dashboard window.
let history_before = AppTraces
  | where TimeGenerated < $__timeFrom()
  | where tostring(Properties['event.name']) == "user_prompt"
  | summarize prior_prompts = count() by email = tostring(Properties['user.email']);
let in_window = AppTraces
  | where $__timeFilter(TimeGenerated)
  {USER_FILTER}
  | where tostring(Properties['event.name']) == "user_prompt"
  | summarize first_prompt = min(TimeGenerated), prompts = count() by email = tostring(Properties['user.email']);
in_window
| join kind=leftouter history_before on email
| where isnull(prior_prompts) or prior_prompts == 0
| project email, first_prompt, prompts
| order by first_prompt desc
"""

Q_ACTIVE_HOURS_PER_USER = f"""AppTraces
| where TimeGenerated > ago(7d)
{USER_FILTER}
| where tostring(Properties['event.name']) == "user_prompt"
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| extend hour_bucket = bin(TimeGenerated, 1h)
| summarize prompts = count(), active_hours = count_distinct(hour_bucket) by email
| order by active_hours desc
"""

Q_ACTIVE_DAYS_PER_USER = f"""AppTraces
| where TimeGenerated > ago(30d)
{USER_FILTER}
| where tostring(Properties['event.name']) == "user_prompt"
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize active_days = count_distinct(bin(TimeGenerated, 1d)) by email
| order by active_days desc
"""

Q_PROMPTS_PER_DAY_PER_USER = f"""AppTraces
| where TimeGenerated > ago(30d)
{USER_FILTER}
| where tostring(Properties['event.name']) == "user_prompt"
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize prompts_total = count(),
            active_days = count_distinct(bin(TimeGenerated, 1d))
  by email
| extend prompts_per_active_day = round(todouble(prompts_total) / active_days, 1)
| project email, prompts_total, active_days, prompts_per_active_day
| order by prompts_per_active_day desc
"""

Q_DISTINCT_PROJECTS_PER_USER = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{USER_FILTER}
| extend email = tostring(Properties['user.email'])
| extend proj = tostring(Properties['project.name'])
| where isnotempty(email) and isnotempty(proj)
| summarize projects = make_set(proj), distinct_projects = dcount(proj) by email
| project email, distinct_projects, projects
| order by distinct_projects desc
"""

Q_TOTAL_COMMITS_30D = f"""AppMetrics
| where TimeGenerated > ago(30d)
| where Name == "claude_code.commit.count"
{USER_FILTER_METRICS}
| summarize Commits = sum(Sum)
"""

Q_TOTAL_PRS_30D = f"""AppMetrics
| where TimeGenerated > ago(30d)
| where Name == "claude_code.pull_request.count"
{USER_FILTER_METRICS}
| summarize PRs = sum(Sum)
"""

Q_TOTAL_LOC_30D = f"""AppMetrics
| where TimeGenerated > ago(30d)
| where Name == "claude_code.lines_of_code.count"
{USER_FILTER_METRICS}
| summarize LoC = sum(Sum)
"""

# Per-user outcomes — joins commits, PRs, and lines from AppMetrics.
# `type` for lines_of_code is "added" or "removed".
Q_OUTCOMES_PER_USER = """let _commits = AppMetrics
  | where $__timeFilter(TimeGenerated)
  | where Name == "claude_code.commit.count"
  | extend p = parse_json(Properties)
  | extend email = tostring(p['user.email'])
  | where tostring(p['user.email']) in (${users:doublequote})
  | summarize Commits = sum(Sum) by email;
let _prs = AppMetrics
  | where $__timeFilter(TimeGenerated)
  | where Name == "claude_code.pull_request.count"
  | extend p = parse_json(Properties)
  | extend email = tostring(p['user.email'])
  | where tostring(p['user.email']) in (${users:doublequote})
  | summarize PRs = sum(Sum) by email;
let _loc = AppMetrics
  | where $__timeFilter(TimeGenerated)
  | where Name == "claude_code.lines_of_code.count"
  | extend p = parse_json(Properties)
  | extend email = tostring(p['user.email']),
           type = tostring(p['type'])
  | where tostring(p['user.email']) in (${users:doublequote})
  | summarize
      LoC_added   = sumif(Sum, type == "added"),
      LoC_removed = sumif(Sum, type == "removed")
    by email;
let all_users = union (_commits | project email),
                      (_prs | project email),
                      (_loc | project email)
  | distinct email;
all_users
| join kind=leftouter _commits on email
| join kind=leftouter _prs on email
| join kind=leftouter _loc on email
| project
    email,
    Commits     = coalesce(Commits, real(0)),
    PRs         = coalesce(PRs, real(0)),
    LoC_added   = coalesce(LoC_added, real(0)),
    LoC_removed = coalesce(LoC_removed, real(0)),
    Net_LoC     = coalesce(LoC_added, real(0)) - coalesce(LoC_removed, real(0))
| order by Commits desc
"""


# ── layout ────────────────────────────────────────────────────────────

panels: list[dict] = []
pid = 200  # offset to avoid collisions with other dashboards


def next_id() -> int:
    global pid
    pid += 1
    return pid


# Footprint
panels.append(row("📊 Footprint", y=0))
panels.append(
    stat_panel(
        next_id(),
        "Active users (24h)",
        0,
        1,
        Q_ACTIVE_USERS("1d"),
        description="Distinct user.email with at least one event in the last 24h.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Active users (7d)",
        6,
        1,
        Q_ACTIVE_USERS("7d"),
        description="Distinct user.email with at least one event in the last 7d.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Active users (30d)",
        12,
        1,
        Q_ACTIVE_USERS("30d"),
        description="Distinct user.email with at least one event in the last 30d.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Active users (90d)",
        18,
        1,
        Q_ACTIVE_USERS("90d"),
        description="Distinct user.email with at least one event in the last 90d.",
    )
)
panels.append(
    timeseries_panel(
        next_id(),
        "DAU / WAU / MAU (dashboard window)",
        0,
        5,
        24,
        8,
        Q_DAU_WAU_MAU,
        description="Rolling distinct-user counts. Each x-axis day shows users active in the trailing 1d / 7d / 30d window from that point.",
    )
)

# Rhythm
panels.append(row("⏱  Rhythm", y=13))
panels.append(
    heatmap_table_panel(
        next_id(),
        "Prompt rhythm — hour-of-day × day-of-week (last 30d)",
        0,
        14,
        24,
        10,
        Q_HEATMAP_HOUR_DOW,
        description="Cell = count of user_prompt events at that hour on that weekday across the last 30 days. Aggregated across selected users (use $users to filter). Table-with-cell-coloring renders categorical-X heatmaps reliably where Grafana's native heatmap panel can't.",
    )
)

# Engagement gaps
panels.append(row("⚠  Engagement gaps", y=24))
panels.append(
    table_panel(
        next_id(),
        "Lapsed users — no prompts in last 4 days",
        0,
        25,
        12,
        9,
        Q_LAPSED_USERS,
        description=(
            "Users who had at least one prompt in the dashboard window but no prompt in the last 4 days. "
            "Threshold (4 days) is chosen to cover normal weekends + the occasional Monday or Friday holiday "
            "without flagging routine non-work days. Bigger numbers in 'days_since_last_prompt' = longer lapse."
        ),
    )
)
panels.append(
    table_panel(
        next_id(),
        "New users this period",
        12,
        25,
        12,
        9,
        Q_NEW_USERS,
        description="Users whose first-ever prompt (across all of history) landed within the current dashboard window.",
    )
)

# Per-user activity
panels.append(row("👥 Per-user activity", y=34))
panels.append(
    table_panel(
        next_id(),
        "Active hours per user (last 7d)",
        0,
        35,
        12,
        9,
        Q_ACTIVE_HOURS_PER_USER,
        description="An 'active hour' = a 1-hour bucket containing at least one user_prompt. Approximation of engagement time.",
    )
)
panels.append(
    barchart_panel(
        next_id(),
        "Active days per user (out of last 30)",
        12,
        35,
        12,
        9,
        Q_ACTIVE_DAYS_PER_USER,
        description="Number of distinct calendar days (out of the last 30) on which the user submitted at least one prompt. Rhythm/consistency signal.",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Prompts per active day (last 30d)",
        0,
        44,
        12,
        9,
        Q_PROMPTS_PER_DAY_PER_USER,
        description="Average prompts on days when the user was active. Density signal — independent of total active days.",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Distinct projects per user (dashboard window)",
        12,
        44,
        12,
        9,
        Q_DISTINCT_PROJECTS_PER_USER,
        description="Breadth signal — how many distinct project.name values appear across the user's sessions.",
    )
)

# Outcomes
panels.append(row("🎯 Outcomes", y=53))
panels.append(
    stat_panel(
        next_id(),
        "Total commits (30d)",
        0,
        54,
        Q_TOTAL_COMMITS_30D,
        w=8,
        h=4,
        description="Sum of claude_code.commit.count across selected users in the last 30 days.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Total PRs (30d)",
        8,
        54,
        Q_TOTAL_PRS_30D,
        w=8,
        h=4,
        description="Sum of claude_code.pull_request.count across selected users in the last 30 days.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Total LoC changed (30d)",
        16,
        54,
        Q_TOTAL_LOC_30D,
        w=8,
        h=4,
        description="Sum of claude_code.lines_of_code.count across selected users in the last 30 days. Includes both additions and removals (the metric breaks down by `type`).",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Outcomes per user (dashboard window)",
        0,
        58,
        24,
        10,
        Q_OUTCOMES_PER_USER,
        description="Per-user totals from claude_code.commit.count, .pull_request.count, .lines_of_code.count. Net_LoC = added − removed.",
    )
)


# ── dashboard envelope ────────────────────────────────────────────────

dashboard = {
    "annotations": {"list": []},
    "description": (
        "Team-level adoption and engagement view. Theme: how effectively, "
        "consistently, and vigorously the team uses Claude Code in development. "
        "For per-user drilldown, see the User Profile dashboard. For cost / ROI "
        "analysis, see the Cost dashboard."
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
    "tags": ["claude-code", "observability", "adoption", "team"],
    "templating": {
        "list": [
            {
                "name": "law_resource",
                "type": "textbox",
                "label": "Log Analytics Workspace Resource ID",
                "description": "Paste the full resource ID of the Log Analytics workspace (e.g. /subscriptions/.../resourceGroups/agent-otel-rg/providers/Microsoft.OperationalInsights/workspaces/agent-otel-law).",
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
        ]
    },
    "time": {"from": "now-30d", "to": "now"},
    "timepicker": {},
    "timezone": "",
    "title": "Agent Observability — Team Adoption",
    "uid": "agent-otel-team-adoption",
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
