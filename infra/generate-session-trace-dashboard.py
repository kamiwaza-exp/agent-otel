#!/usr/bin/env python3
"""Generate claude-code-session-trace.azure.json — per-session replay view.

Pick a session, see its prompts as a table, click a prompt row to load
its trace into Grafana's native Traces panel (collapsible span tree).
Plus a free-text span search across the whole session, and drill-downs
for tool stats and errors.

Data sources used:
  - AppDependencies: claude_code.interaction (prompt unit, has user_prompt
    text + OperationId), claude_code.llm_request, claude_code.tool,
    claude_code.tool.execution, claude_code.tool.blocked_on_user
  - AppTraces: api_request events (cost/tokens), tool_result events
  - The trace panel itself uses the Azure Monitor data source's
    "Azure Traces" query type, which talks to the Application Insights
    resource directly (not via Log Analytics KQL) and feeds Grafana's
    built-in trace visualization.

Variables:
  - $law_resource       hidden, set at import; standard pattern
  - $ai_resource        hidden, set at import; full App Insights resource
                        ID needed by the Azure Traces query
  - $session_id         queried single-select, label = "<email> · <date> · Np"
  - $operation_id       textbox, populated by data link on prompts table
  - $search             textbox, optional span content search
"""

import json
from pathlib import Path

DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-session-trace.azure.json")

AZURE_DS = {"type": "grafana-azure-monitor-datasource", "uid": "${DS_AZURE_MONITOR}"}
LA_RESOURCE_VAR = "$law_resource"
AI_RESOURCE_VAR = "${ai_resource}"


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


def stat_panel(pid, title, x, y, query, w=6, h=3, unit="short", description=""):
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
                        {"color": "blue", "value": None},
                    ],
                },
                "unit": unit,
            },
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
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


def trace_panel(pid, title, x, y, w, h, description=""):
    """Native Grafana traces panel fed by the Azure Monitor data source's
    "Azure Traces" query type (Grafana 10+). Renders the operation's full
    span tree as a collapsible waterfall with no client-side indenting.

    Minimal target shape: only resources + operationId + resultFormat. We
    intentionally omit traceTypes (Grafana defaults to all event categories
    when unspecified, which is what we want — claude_code.* spans are in
    the dependencies category)."""
    return {
        "id": pid,
        "type": "traces",
        "title": title,
        "description": description,
        "datasource": AZURE_DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "options": {},
        "targets": [
            {
                "datasource": AZURE_DS,
                "queryType": "Azure Traces",
                "azureTraces": {
                    "resources": [AI_RESOURCE_VAR],
                    "operationId": "${operation_id}",
                    "resultFormat": "trace",
                },
                "refId": "A",
            }
        ],
    }


def row(title, y, collapsed=False):
    return {
        "type": "row",
        "title": title,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "collapsed": collapsed,
    }


# ── shared filter clauses ─────────────────────────────────────────────

SESSION_FILTER_TRACES = "| where tostring(Properties['session.id']) == '${session_id}'"
SESSION_FILTER_DEPS = "| where tostring(Properties['session.id']) == '${session_id}'"


# ── queries ───────────────────────────────────────────────────────────

# Row 1 — metadata stats. Each query returns a single value.

Q_META_USER = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{SESSION_FILTER_TRACES}
| extend email = tostring(Properties['user.email']), proj = tostring(Properties['project.name'])
| summarize email=any(email), proj=any(proj)
| extend display = iff(isempty(proj), email, strcat(email, ' • ', proj))
| project display
"""

Q_META_DURATION_MIN = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{SESSION_FILTER_TRACES}
| summarize started = min(TimeGenerated), ended = max(TimeGenerated)
| extend duration_min = round(todouble(datetime_diff('second', ended, started)) / 60.0, 1)
| project duration_min
"""

Q_META_TOTAL_COST = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{SESSION_FILTER_TRACES}
| where tostring(Properties['event.name']) == 'api_request'
| extend cost = todouble(Properties['cost_usd'])
| summarize total_cost = sum(cost)
"""

Q_META_TOTAL_TOKENS = f"""AppTraces
| where $__timeFilter(TimeGenerated)
{SESSION_FILTER_TRACES}
| where tostring(Properties['event.name']) == 'api_request'
| extend in_tok = toint(Properties['input_tokens']),
         out_tok = toint(Properties['output_tokens'])
| summarize total_tokens = sum(in_tok) + sum(out_tok)
"""

# Row 2 — prompts table. Each row is one claude_code.interaction span.
# OperationId is exposed for the data link that sets $operation_id.

Q_PROMPTS = f"""AppDependencies
| where $__timeFilter(TimeGenerated)
| where Name == 'claude_code.interaction'
{SESSION_FILTER_DEPS}
| extend full_prompt = tostring(Properties['user_prompt']),
         prompt_len = toint(Properties['user_prompt_length']),
         seq = toint(Properties['interaction.sequence']),
         status_code = tostring(Properties['otel.status_code'])
| extend preview = iff(strlen(full_prompt) > 140,
                       strcat(substring(full_prompt, 0, 140), '…'),
                       full_prompt)
| project ['#'] = seq,
          started = TimeGenerated,
          duration_ms = DurationMs,
          chars = prompt_len,
          status_code,
          OperationId,
          prompt = preview
| order by started asc
"""

# Row 4 — span search. Default (empty $search) returns ALL spans for the
# session; non-empty $search narrows to spans whose Name OR any Properties
# value contains the search string. KQL has no "contains across all keys"
# operator, so we serialize Properties to string and contains_cs against it.

Q_SPAN_SEARCH = f"""AppDependencies
| where $__timeFilter(TimeGenerated)
{SESSION_FILTER_DEPS}
| extend props_text = tostring(Properties)
| where '${{search}}' == '' or Name contains_cs '${{search}}' or props_text contains_cs '${{search}}'
| extend tool = tostring(Properties['tool_name']),
         model = tostring(Properties['gen_ai.request.model']),
         status_code = tostring(Properties['otel.status_code']),
         full_command = tostring(Properties['full_command'])
| project started = TimeGenerated, span = Name, tool, model, full_command,
          duration_ms = DurationMs, status_code, OperationId, Id, ParentId
| order by started asc
| take 500
"""

# Row 5 — drill-downs.

Q_TOOL_STATS = f"""AppDependencies
| where $__timeFilter(TimeGenerated)
| where Name == 'claude_code.tool.execution'
{SESSION_FILTER_DEPS}
| extend tool = tostring(Properties['tool_name'])
| where isnotempty(tool)
| summarize calls = count(),
            total_ms = sum(DurationMs),
            avg_ms = round(avg(DurationMs), 0),
            p95_ms = round(percentile(DurationMs, 95), 0)
            by tool
| order by total_ms desc
"""

Q_ERRORS = f"""union
  (AppDependencies
   | where $__timeFilter(TimeGenerated)
   {SESSION_FILTER_DEPS}
   | where tostring(Properties['otel.status_code']) == 'ERROR'
   | extend entry_kind = 'span_error', detail = Name),
  (AppTraces
   | where $__timeFilter(TimeGenerated)
   {SESSION_FILTER_TRACES}
   | where tostring(Properties['event.name']) == 'tool_decision'
       and tostring(Properties['decision_type']) == 'reject'
   | extend entry_kind = 'tool_rejected', detail = tostring(Properties['tool_name']))
| project started = TimeGenerated, entry_kind, detail
| order by started asc
"""


# ── data links ────────────────────────────────────────────────────────

# Per-cell data link on the OperationId column of the prompts table:
# clicking a row's OperationId rewrites the dashboard URL with
# var-operation_id=<that row's OperationId>, which the trace panel below
# picks up immediately.
PROMPTS_OPID_DATALINK = {
    "matcher": {"id": "byName", "options": "OperationId"},
    "properties": [
        {
            "id": "links",
            "value": [
                {
                    "title": "Load this prompt's trace ↓",
                    "url": "/d/agent-otel-session-trace?${__url_time_range}&var-session_id=${session_id}&var-operation_id=${__data.fields.OperationId}",
                    "targetBlank": False,
                }
            ],
        }
    ],
}


# ── panels ────────────────────────────────────────────────────────────

panels = []
_id = 0


def next_id():
    global _id
    _id += 1
    return _id


# Row 1 — session metadata
panels.append(row("📋 Session metadata", y=0))
panels.append(
    stat_panel(next_id(), "User • Project", 0, 1, Q_META_USER, w=8, h=3, unit="none")
)
panels.append(
    stat_panel(
        next_id(),
        "Duration",
        8,
        1,
        Q_META_DURATION_MIN,
        w=4,
        h=3,
        unit="m",
        description="Wall-clock minutes between first and last event in the dashboard window.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Total cost (USD)",
        12,
        1,
        Q_META_TOTAL_COST,
        w=6,
        h=3,
        unit="currencyUSD",
        description="Sum of cost_usd from api_request events. Excludes cached input.",
    )
)
panels.append(
    stat_panel(
        next_id(),
        "Total tokens (in+out)",
        18,
        1,
        Q_META_TOTAL_TOKENS,
        w=6,
        h=3,
        unit="short",
    )
)

# Row 2 — prompts table
panels.append(
    row("💬 Prompts in this session — click any OperationId cell to switch traces", y=4)
)
prompts_panel = table_panel(
    next_id(),
    "Prompts (interaction spans)",
    0,
    5,
    24,
    12,
    Q_PROMPTS,
    description="One row per claude_code.interaction span. Click a row's OperationId cell to populate $operation_id and load that prompt's trace below.",
)
prompts_panel["fieldConfig"]["overrides"] = [
    PROMPTS_OPID_DATALINK,
    {
        "matcher": {"id": "byName", "options": "prompt"},
        "properties": [
            {"id": "custom.cellOptions", "value": {"type": "auto", "wrapText": True}},
            {"id": "custom.minWidth", "value": 400},
        ],
    },
    {
        "matcher": {"id": "byName", "options": "duration_ms"},
        "properties": [{"id": "unit", "value": "ms"}],
    },
    {
        "matcher": {"id": "byName", "options": "#"},
        "properties": [{"id": "custom.width", "value": 50}],
    },
    {
        "matcher": {"id": "byName", "options": "started"},
        "properties": [{"id": "custom.width", "value": 180}],
    },
    {
        "matcher": {"id": "byName", "options": "chars"},
        "properties": [{"id": "custom.width", "value": 80}],
    },
    {
        "matcher": {"id": "byName", "options": "status_code"},
        "properties": [{"id": "custom.width", "value": 140}],
    },
    {
        "matcher": {"id": "byName", "options": "OperationId"},
        "properties": [{"id": "custom.width", "value": 280}],
    },
]
panels.append(prompts_panel)

# Row 3 — trace waterfall
panels.append(row("🌳 Trace for selected prompt", y=17))
panels.append(
    trace_panel(
        next_id(),
        "Trace waterfall",
        0,
        18,
        24,
        22,
        description="Click a row in the prompts table above to populate $operation_id. Then this panel renders the full span tree (claude_code.interaction → llm_request → tool → tool.execution) as a collapsible waterfall.",
    )
)

# Row 4 — span search
panels.append(row("🔎 Span search across the whole session", y=40))
search_panel = table_panel(
    next_id(),
    "Spans (filtered by $search)",
    0,
    41,
    24,
    14,
    Q_SPAN_SEARCH,
    description="All spans for the selected session. Type in $search above to filter by span name or any attribute value (case-sensitive substring). Up to 500 rows.",
)
search_panel["fieldConfig"]["overrides"] = [
    {
        "matcher": {"id": "byName", "options": "duration_ms"},
        "properties": [{"id": "unit", "value": "ms"}],
    },
    {
        "matcher": {"id": "byName", "options": "full_command"},
        "properties": [
            {"id": "custom.cellOptions", "value": {"type": "auto", "wrapText": True}},
            {"id": "custom.width", "value": 400},
        ],
    },
]
panels.append(search_panel)

# Row 5 — drill-downs
panels.append(row("📊 Tool usage & errors", y=55))
panels.append(
    table_panel(
        next_id(),
        "Tool usage breakdown",
        0,
        56,
        12,
        10,
        Q_TOOL_STATS,
        description="Aggregated over claude_code.tool.execution spans for this session.",
    )
)
panels.append(
    table_panel(
        next_id(),
        "Errors & rejected tools",
        12,
        56,
        12,
        10,
        Q_ERRORS,
        description="Span-level errors (otel.status_code=ERROR) and tool decisions where the user rejected the tool call.",
    )
)


# ── envelope ──────────────────────────────────────────────────────────

dashboard = {
    "annotations": {"list": []},
    "description": (
        "Per-session replay view. Pick a session via $session_id, then "
        "click any prompt row to load its trace into the waterfall "
        "panel below. Span search lets you find tool calls or model "
        "invocations across the whole session by free-text match."
    ),
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "id": None,
    "links": [],
    "liveNow": False,
    "panels": panels,
    "refresh": "",
    "schemaVersion": 27,
    "style": "dark",
    "tags": ["claude-code", "observability", "session-trace"],
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
                "name": "ai_resource",
                "type": "textbox",
                "label": "Application Insights Resource ID",
                "description": "Paste the full resource ID of the Application Insights component (used by the Azure Traces query).",
                "query": "",
                "current": {"selected": False, "text": "", "value": ""},
                "hide": 2,
                "skipUrlSync": False,
            },
            {
                "name": "session_id",
                "type": "query",
                "label": "Session",
                "description": "Recent sessions (last 7 days) ordered by start time. Label format: <email> · <date> · <prompt_count>p. Only sessions that have at least one claude_code.interaction span (i.e., emitted with beta tracing on) appear here.",
                "datasource": AZURE_DS,
                "query": {
                    "queryType": "Azure Log Analytics",
                    "azureLogAnalytics": {
                        "query": (
                            "AppDependencies\n"
                            "| where TimeGenerated > ago(7d)\n"
                            "| where Name == 'claude_code.interaction'\n"
                            "| extend email = tostring(Properties['user.email']),\n"
                            "         sess = tostring(Properties['session.id'])\n"
                            "| where isnotempty(sess)\n"
                            "| summarize first_seen = min(TimeGenerated), prompts = count() by sess, email\n"
                            "| order by first_seen desc\n"
                            "| take 100\n"
                            "| project __value = sess,\n"
                            "          __text = strcat(email, ' · ',\n"
                            "                          format_datetime(first_seen, 'yy-MM-dd HH:mm'),\n"
                            "                          ' · ', prompts, 'p')"
                        ),
                        "resource": LA_RESOURCE_VAR,
                        "resultFormat": "table",
                    },
                    "refId": "session_id",
                },
                "refresh": 2,
                "multi": False,
                "includeAll": False,
                "current": {"selected": False, "text": "", "value": ""},
                "hide": 0,
                "skipUrlSync": False,
            },
            {
                "name": "operation_id",
                "type": "query",
                "label": "Operation ID",
                "description": "Auto-populated from interactions in the selected session, newest first. Default selection is the most recent prompt's trace; click any other prompt row to switch.",
                "datasource": AZURE_DS,
                "query": {
                    "queryType": "Azure Log Analytics",
                    "azureLogAnalytics": {
                        "query": (
                            "AppDependencies\n"
                            "| where TimeGenerated > ago(7d)\n"
                            "| where Name == 'claude_code.interaction'\n"
                            "| where tostring(Properties['session.id']) == '${session_id}'\n"
                            "| extend seq = toint(Properties['interaction.sequence']),\n"
                            "         preview = substring(tostring(Properties['user_prompt']), 0, 60)\n"
                            "| order by TimeGenerated desc\n"
                            "| take 50\n"
                            "| project __value = OperationId,\n"
                            "          __text = strcat('#', seq, ' · ', preview)"
                        ),
                        "resource": LA_RESOURCE_VAR,
                        "resultFormat": "table",
                    },
                    "refId": "operation_id",
                },
                "refresh": 2,
                "multi": False,
                "includeAll": False,
                "current": {"selected": False, "text": "", "value": ""},
                "hide": 0,
                "skipUrlSync": False,
            },
            {
                "name": "search",
                "type": "textbox",
                "label": "Span search",
                "description": "Case-sensitive substring match across span name and all properties. Empty = show all spans.",
                "query": "",
                "current": {"selected": False, "text": "", "value": ""},
                "hide": 0,
                "skipUrlSync": False,
            },
        ]
    },
    "time": {"from": "now-24h", "to": "now"},
    "timepicker": {},
    "timezone": "",
    "title": "Agent Observability — Session Trace",
    "uid": "agent-otel-session-trace",
    "version": 1,
    "__inputs": [
        {
            "name": "DS_AZURE_MONITOR",
            "label": "Azure Monitor",
            "description": "Azure Monitor data source with access to Log Analytics + Application Insights.",
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
