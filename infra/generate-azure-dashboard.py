#!/usr/bin/env python3
"""Generate claude-code-dashboard.azure.json from the local dashboard."""

import json
import copy
import re
from pathlib import Path

SRC = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-dashboard.json")
DST = Path("/Users/jxstanford/devel/kz/agent-otel/claude-code-dashboard.azure.json")

PROM_DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
AZURE_DS = {"type": "grafana-azure-monitor-datasource", "uid": "${DS_AZURE_MONITOR}"}
LA_RESOURCE_VAR = "$law_resource"

# Local Prometheus assigns `job="otel-collector"` via its scrape config.
# Azure Managed Prometheus receives via remote-write — no scrape, no job label.
# Selectors that hard-code that label match zero series on Azure, so strip them
# during the port. Two shapes to handle:
#   {job="otel-collector"}              → drop the whole brace block
#   {foo="bar",job="otel-collector"}    → remove just the job clause
_JOB_ONLY_SELECTOR = re.compile(r'\{\s*job\s*=\s*"otel-collector"\s*\}')
_JOB_WITH_OTHERS = re.compile(
    r',\s*job\s*=\s*"otel-collector"|job\s*=\s*"otel-collector"\s*,'
)


def strip_job_selector(expr: str) -> str:
    """Remove the {job="otel-collector"} selector from a PromQL expression."""
    expr = _JOB_ONLY_SELECTOR.sub("", expr)
    return _JOB_WITH_OTHERS.sub("", expr)


# KQL rewrites keyed by panel id, from docs/azure-kql-panels.md.
# All Claude Code event data lands in AppTraces (workspace-based App Insights).
# OTel attrs end up in the dynamic Properties column; access by quoted key.
KQL_BY_PANEL_ID = {
    7: {
        "query": (
            "AppTraces\n"
            "| where $__timeFilter(TimeGenerated)\n"
            "| where tostring(Properties['event.name']) == \"tool_result\"\n"
            "| summarize count() by tool_name = tostring(Properties['name']), bin(TimeGenerated, 5m)\n"
            "| order by TimeGenerated asc"
        ),
        "resultFormat": "time_series",
    },
    14: {
        "query": (
            "AppTraces\n"
            "| where $__timeFilter(TimeGenerated)\n"
            "| where tostring(Properties['event.name']) == \"tool_result\"\n"
            "| summarize count_ = count() by tool_name = tostring(Properties['name'])\n"
            "| order by count_ desc"
        ),
        "resultFormat": "table",
    },
    8: {
        "query": (
            "AppTraces\n"
            "| where $__timeFilter(TimeGenerated)\n"
            "| where tostring(Properties['event.name']) == \"tool_result\"\n"
            "| summarize\n"
            "    total = count(),\n"
            "    successes = countif(tostring(Properties['success']) == \"true\")\n"
            "    by tool_name = tostring(Properties['name']), bin(TimeGenerated, 15m)\n"
            "| extend success_rate = iif(total > 0, 100.0 * successes / total, real(null))\n"
            "| project TimeGenerated, tool_name, success_rate\n"
            "| order by TimeGenerated asc"
        ),
        "resultFormat": "time_series",
    },
    9: {
        "query": (
            "AppTraces\n"
            "| where $__timeFilter(TimeGenerated)\n"
            "| where tostring(Properties['event.name']) == \"api_request\"\n"
            "| summarize avg_duration_ms = avg(toint(Properties['duration_ms']))\n"
            "    by model = tostring(Properties['model']), bin(TimeGenerated, $__timeInterval)\n"
            "| order by TimeGenerated asc"
        ),
        "resultFormat": "time_series",
    },
    10: {
        "query": (
            "AppTraces\n"
            "| where $__timeFilter(TimeGenerated)\n"
            "| where tostring(Properties['event.name']) == \"api_error\"\n"
            "| summarize errors = count() by status_code = tostring(Properties['status_code']), bin(TimeGenerated, 1m)\n"
            "| extend rate_per_sec = errors / 60.0\n"
            "| project TimeGenerated, status_code, rate_per_sec\n"
            "| order by TimeGenerated asc"
        ),
        "resultFormat": "time_series",
    },
    13: {
        # Synthesize a single Message column: Grafana's "logs" result format
        # only renders the timestamp + a Message body. Multiple project columns
        # collapse to just the timestamp, which is why the panel showed dates
        # only. strcat-ing a one-line summary recovers the LogQL look.
        "query": (
            "AppTraces\n"
            "| where $__timeFilter(TimeGenerated)\n"
            "| where tostring(Properties['event.name']) == \"tool_result\"\n"
            "| project\n"
            "    TimeGenerated,\n"
            "    Message = strcat(\n"
            '        "[", tostring(Properties[\'name\']), "] ",\n'
            '        iif(tostring(Properties[\'success\']) == "true", "✅", "❌"), " ",\n'
            "        tostring(Properties['duration_ms']), \"ms\",\n"
            "        iif(isnotempty(tostring(Properties['error'])),\n"
            "            strcat(\" ERROR: \", tostring(Properties['error'])),\n"
            '            ""))\n'
            "| order by TimeGenerated desc"
        ),
        "resultFormat": "logs",
    },
    17: {
        # Same Message-column synthesis as panel 13. api_error always carries
        # an error attribute, so no isnotempty guard needed.
        "query": (
            "AppTraces\n"
            "| where $__timeFilter(TimeGenerated)\n"
            "| where tostring(Properties['event.name']) == \"api_error\"\n"
            "| project\n"
            "    TimeGenerated,\n"
            "    Message = strcat(\n"
            '        "[", tostring(Properties[\'model\']), "] HTTP ",\n'
            "        tostring(Properties['status_code']), \" \",\n"
            "        tostring(Properties['duration_ms']), \"ms\",\n"
            '        " (attempt ", tostring(Properties[\'attempt\']), ") ",\n'
            "        \"ERROR: \", tostring(Properties['error']))\n"
            "| order by TimeGenerated desc"
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
                        "AppTraces\n"
                        "| where $__timeFilter(TimeGenerated)\n"
                        "| where tostring(Properties['event.name']) == \"user_prompt\"\n"
                        "| project\n"
                        "    TimeGenerated,\n"
                        "    Message = strcat(\n"
                        '        "[", substring(tostring(Properties[\'session.id\']), 0, 8), "] ",\n'
                        '        "len=", tostring(Properties[\'prompt_length\']), " ",\n'
                        "        iif(isnotempty(tostring(Properties['prompt'])),\n"
                        "            tostring(Properties['prompt']),\n"
                        '            "<prompt text not logged: set OTEL_LOG_USER_PROMPTS=1 on the client>"))\n'
                        "| order by TimeGenerated desc"
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
        # Port: swap datasource UID and strip the local-only job selector
        # from each target's PromQL expression.
        p["datasource"] = PROM_DS
        for t in p.get("targets", []):
            if isinstance(t.get("datasource"), dict):
                t["datasource"] = PROM_DS
            if isinstance(t.get("expr"), str):
                t["expr"] = strip_job_selector(t["expr"])
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
    dst["__inputs"] = [
        {
            "name": "DS_PROMETHEUS",
            "label": "Azure Monitor Managed Prometheus",
            "description": "Prometheus data source pointing at the Azure Monitor Workspace.",
            "type": "datasource",
            "pluginId": "prometheus",
            "pluginName": "Prometheus",
        },
        {
            "name": "DS_AZURE_MONITOR",
            "label": "Azure Monitor",
            "description": "Azure Monitor data source with access to the Log Analytics workspace containing ClaudeCodeEvents_CL.",
            "type": "datasource",
            "pluginId": "grafana-azure-monitor-datasource",
            "pluginName": "Azure Monitor",
        },
    ]
    dst["__requires"] = [
        {"type": "grafana", "id": "grafana", "name": "Grafana", "version": "10.0.0"},
        {
            "type": "datasource",
            "id": "prometheus",
            "name": "Prometheus",
            "version": "1.0.0",
        },
        {
            "type": "datasource",
            "id": "grafana-azure-monitor-datasource",
            "name": "Azure Monitor",
            "version": "1.0.0",
        },
    ]

    # Add a law_resource dashboard variable so every KQL query can reference
    # the Log Analytics workspace without hardcoding subscription/resource-group.
    templating = dst.setdefault("templating", {"list": []})
    tvars = templating.setdefault("list", [])
    # Remove pre-existing law_resource to keep re-runs idempotent.
    tvars = [v for v in tvars if v.get("name") != "law_resource"]
    tvars.insert(
        0,
        {
            "name": "law_resource",
            "type": "textbox",
            "label": "Log Analytics Workspace Resource ID",
            "description": "Paste the full resource ID, e.g. /subscriptions/xxx/resourceGroups/claude-obs-rg/providers/Microsoft.OperationalInsights/workspaces/claude-obs-law",
            "query": "",
            "current": {"selected": False, "text": "", "value": ""},
            "hide": 0,
            "skipUrlSync": False,
        },
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
