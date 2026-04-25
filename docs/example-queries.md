# Example KQL Queries

Copy-paste into Azure Monitor Logs (portal → workspace `agent-otel-law` →
Logs) or Grafana → Explore (Azure Monitor data source, Service: Logs,
Resource: the LA workspace, **not** the resource group).

For aggregations (`summarize`, `count`, etc.), set Grafana's **Format as:
Table**. For time series (anything ending with `bin(TimeGenerated, ...)`),
**Format as: Time series**.

> Schema notes: `AppMetrics` carries our metric data points (`Name`, `Sum`,
> `ItemCount`, `Properties`). `AppTraces` carries Claude Code event log
> records (`Properties.event.name`, plus the event's per-type attributes).
> `Properties` is a JSON-serialized string in `AppMetrics` and a parsed bag
> in `AppTraces` — examples below handle both.

---

## 1. Smoke / sanity

### Are metrics arriving at all?

```kql
AppMetrics
| where TimeGenerated > ago(1h)
| summarize count() by Name
| order by count_ desc
```

### Are events arriving at all?

```kql
AppTraces
| where TimeGenerated > ago(1h)
| summarize count() by event_name = tostring(Properties['event.name'])
| order by count_ desc
```

### What attributes does a metric carry?

```kql
AppMetrics
| where TimeGenerated > ago(1h)
| where Name == "claude_code.cost.usage"
| take 1
| project Properties
```

---

## 2. Active users

### Active users in the last 1 day

```kql
AppTraces
| where TimeGenerated > ago(1d)
| summarize events = count() by email = tostring(Properties['user.email'])
| where isnotempty(email)
| order by events desc
```

### Active users in the last 7 days, with first/last activity

```kql
AppTraces
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
```

### Daily active users (DAU) timeseries

```kql
AppTraces
| where TimeGenerated > ago(30d)
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| summarize DAU = dcount(email) by bin(TimeGenerated, 1d)
| order by TimeGenerated asc
```

Format as: **Time series**.

### Active users grouped by username (collapse multiple emails per person)

Splits on `@` so `john@work.com` + `john@personal.com` aggregate as `john`.
For people whose local-parts diverge (e.g., `john.s@` vs `js@`) you'd need
a separate alias map.

```kql
AppTraces
| where TimeGenerated > ago(7d)
| extend email = tostring(Properties['user.email'])
| where isnotempty(email)
| extend username = tostring(split(email, "@")[0])
| summarize
    emails = make_set(email),
    events = count(),
    sessions = dcount(tostring(Properties['session.id']))
  by username
| order by events desc
```

---

## 3. Activity over time

### Active hours where prompts were submitted (per user, last 7d)

An "active hour" = a 1-hour bucket that contains at least one
`user_prompt` event. Approximation of "hours of engagement."

```kql
AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "user_prompt"
| extend email = tostring(Properties['user.email'])
| extend hour_bucket = bin(TimeGenerated, 1h)
| summarize prompts = count(), active_hour = count_distinct(hour_bucket) by email
| project email, prompts, active_hours = active_hour
| order by active_hours desc
```

### Total prompt-active hours across the team

```kql
AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "user_prompt"
| summarize active_hours = count_distinct(bin(TimeGenerated, 1h))
```

### Hour-of-day usage pattern

```kql
AppTraces
| where TimeGenerated > ago(30d)
| where tostring(Properties['event.name']) == "user_prompt"
| extend hour_of_day = datetime_part("hour", TimeGenerated)
| summarize prompts = count() by hour_of_day
| order by hour_of_day asc
```

Format as: **Time series** isn't right here — use **Table** or **Bar
chart**.

### Day-of-week usage pattern

```kql
AppTraces
| where TimeGenerated > ago(60d)
| where tostring(Properties['event.name']) == "user_prompt"
| extend dow = case(
    dayofweek(TimeGenerated) == 0d, "Sun",
    dayofweek(TimeGenerated) == 1d, "Mon",
    dayofweek(TimeGenerated) == 2d, "Tue",
    dayofweek(TimeGenerated) == 3d, "Wed",
    dayofweek(TimeGenerated) == 4d, "Thu",
    dayofweek(TimeGenerated) == 5d, "Fri",
    "Sat")
| summarize prompts = count() by dow
```

---

## 4. Activity by project

> Requires the `agent-otel` plugin's launcher to be installed for at least
> some sessions, since `host.name` / `project.name` come from
> `OTEL_RESOURCE_ATTRIBUTES`. Sessions before launcher install will have
> empty values for these.

### Top projects in the last 7 days

```kql
AppTraces
| where TimeGenerated > ago(7d)
| extend project = tostring(Properties['project.name'])
| where isnotempty(project)
| summarize
    events = count(),
    users = dcount(tostring(Properties['user.email'])),
    sessions = dcount(tostring(Properties['session.id']))
  by project
| order by events desc
```

### Activity per project, broken down by user

```kql
AppTraces
| where TimeGenerated > ago(7d)
| extend project = tostring(Properties['project.name'])
| extend email = tostring(Properties['user.email'])
| where isnotempty(project) and isnotempty(email)
| summarize prompts = countif(tostring(Properties['event.name']) == "user_prompt")
  by project, email
| order by project asc, prompts desc
```

### Cost by project (last 7d)

```kql
AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend project = tostring(p['project.name'])
| where isnotempty(project)
| summarize Cost = sum(Sum) by project
| order by Cost desc
```

### Active hosts per project

```kql
AppTraces
| where TimeGenerated > ago(7d)
| extend project = tostring(Properties['project.name'])
| extend host = tostring(Properties['host.name'])
| where isnotempty(project)
| summarize hosts = make_set(host) by project
```

---

## 5. Cost & usage

> **Cost is an approximation, not a bill.** `claude_code.cost.usage` is
> computed client-side by Claude Code: token counts × published per-token
> API rates. It is **not** what you actually pay — Max plan users pay
> subscription, not per-token; AWS Bedrock / GCP Vertex have their own
> bills. Treat this as a comparable "what the same activity would cost
> at standard API rates" signal across users and projects, not a
> reconciliation source. (See `CLAUDE_OBSERVABILITY.md` for Anthropic's
> own caveat.)

### Total cost in the last 24h

```kql
AppMetrics
| where TimeGenerated > ago(24h)
| where Name == "claude_code.cost.usage"
| summarize Cost = sum(Sum)
```

### Cost by user (top 10, last 7d)

```kql
AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend email = tostring(p['user.email'])
| summarize Cost = sum(Sum) by email
| top 10 by Cost desc
```

### Cost by username (multi-email aggregation)

```kql
AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend email = tostring(p['user.email'])
| extend username = tostring(split(email, "@")[0])
| summarize Cost = sum(Sum), Accounts = make_set(email) by username
| order by Cost desc
```

### Cost by model

```kql
AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend model = tostring(p['model'])
| summarize Cost = sum(Sum) by model
| order by Cost desc
```

### Token usage by type (input / output / cacheRead / cacheCreation)

```kql
AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.token.usage"
| extend p = parse_json(Properties)
| extend type = tostring(p['type'])
| summarize Tokens = sum(Sum) by type
| order by Tokens desc
```

### Cost per session (top sessions)

```kql
AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend session = tostring(p['session.id']), email = tostring(p['user.email'])
| summarize Cost = sum(Sum) by session, email
| top 20 by Cost desc
```

### Cost per prompt (rough efficiency proxy)

```kql
let costs = AppMetrics
  | where TimeGenerated > ago(7d)
  | where Name == "claude_code.cost.usage"
  | extend p = parse_json(Properties)
  | summarize Cost = sum(Sum) by email = tostring(p['user.email']);
let prompts = AppTraces
  | where TimeGenerated > ago(7d)
  | where tostring(Properties['event.name']) == "user_prompt"
  | summarize Prompts = count() by email = tostring(Properties['user.email']);
costs
| join kind=inner prompts on email
| project email, Prompts, Cost, CostPerPrompt = round(Cost / todouble(Prompts), 4)
| order by Cost desc
```

---

## 6. Tool usage

### Top tools (by call count, last 7d)

```kql
AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "tool_result"
| summarize calls = count() by tool = tostring(Properties['name'])
| order by calls desc
```

### Tool success rate by tool

```kql
AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "tool_result"
| summarize
    total = count(),
    successes = countif(tostring(Properties['success']) == "true")
  by tool = tostring(Properties['name'])
| extend success_rate_pct = round(100.0 * successes / total, 1)
| order by total desc
```

### Tool calls per prompt (engagement-per-ask, by user)

```kql
let prompts = AppTraces
  | where TimeGenerated > ago(7d)
  | where tostring(Properties['event.name']) == "user_prompt"
  | summarize prompts = count() by email = tostring(Properties['user.email']);
let tool_calls = AppTraces
  | where TimeGenerated > ago(7d)
  | where tostring(Properties['event.name']) == "tool_result"
  | summarize tools = count() by email = tostring(Properties['user.email']);
prompts
| join kind=inner tool_calls on email
| project email, prompts, tools, tools_per_prompt = round(todouble(tools) / prompts, 1)
| order by tools_per_prompt desc
```

### Recent failed tool calls

```kql
AppTraces
| where TimeGenerated > ago(24h)
| where tostring(Properties['event.name']) == "tool_result"
| where tostring(Properties['success']) != "true"
| project
    TimeGenerated,
    email = tostring(Properties['user.email']),
    project = tostring(Properties['project.name']),
    tool = tostring(Properties['name']),
    error = tostring(Properties['error'])
| order by TimeGenerated desc
| take 50
```

---

## 7. Prompts

### Recent user prompts (with redaction-aware view)

```kql
AppTraces
| where TimeGenerated > ago(1h)
| where tostring(Properties['event.name']) == "user_prompt"
| project
    TimeGenerated,
    email = tostring(Properties['user.email']),
    project = tostring(Properties['project.name']),
    length = toint(Properties['prompt_length']),
    prompt = tostring(Properties['prompt'])
| order by TimeGenerated desc
| take 50
```

### Prompt length distribution (histogram-friendly)

```kql
AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "user_prompt"
| extend len = toint(Properties['prompt_length'])
| extend bucket = case(
    len < 50,    "0-50",
    len < 200,   "50-200",
    len < 500,   "200-500",
    len < 2000,  "500-2k",
    len < 10000, "2k-10k",
    "10k+")
| summarize prompts = count() by bucket
| order by bucket asc
```

### Prompts per session (distribution)

```kql
AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['event.name']) == "user_prompt"
| summarize prompts = count() by session = tostring(Properties['session.id'])
| summarize sessions = count() by prompts_per_session = bin(prompts, 5)
| order by prompts_per_session asc
```

### Redaction sanity — show entries that contain redaction markers

```kql
AppTraces
| where TimeGenerated > ago(7d)
| where tostring(Properties['prompt']) contains "[REDACTED:"
   or tostring(Properties['error']) contains "[REDACTED:"
| project
    TimeGenerated,
    email = tostring(Properties['user.email']),
    event = tostring(Properties['event.name']),
    prompt_excerpt = substring(tostring(Properties['prompt']), 0, 200),
    error_excerpt = substring(tostring(Properties['error']), 0, 200)
| order by TimeGenerated desc
| take 50
```

---

## 8. Sessions

### Sessions started in the last 24h, with cost + prompt count

```kql
let sess_meta = AppTraces
  | where TimeGenerated > ago(24h)
  | extend session = tostring(Properties['session.id']),
           email = tostring(Properties['user.email']),
           project = tostring(Properties['project.name'])
  | summarize
      first_seen = min(TimeGenerated),
      last_seen = max(TimeGenerated),
      prompts = countif(tostring(Properties['event.name']) == "user_prompt")
    by session, email, project;
let sess_cost = AppMetrics
  | where TimeGenerated > ago(24h)
  | where Name == "claude_code.cost.usage"
  | extend p = parse_json(Properties), session = tostring(p['session.id'])
  | summarize Cost = sum(Sum) by session;
sess_meta
| join kind=leftouter sess_cost on session
| extend duration_min = datetime_diff('minute', last_seen, first_seen)
| project first_seen, email, project, prompts, Cost = coalesce(Cost, real(0)), duration_min, session
| order by first_seen desc
```

### Sessions per user (top, last 30d)

```kql
AppTraces
| where TimeGenerated > ago(30d)
| extend email = tostring(Properties['user.email']),
         session = tostring(Properties['session.id'])
| where isnotempty(email)
| summarize sessions = dcount(session) by email
| top 20 by sessions desc
```

---

## 9. Errors

### API error rate by status code (last 24h)

```kql
AppTraces
| where TimeGenerated > ago(24h)
| where tostring(Properties['event.name']) == "api_error"
| summarize errors = count() by status = tostring(Properties['status_code'])
| order by errors desc
```

### Error rate over time, by status code

```kql
AppTraces
| where $__timeFilter(TimeGenerated)
| where tostring(Properties['event.name']) == "api_error"
| summarize errors = count() by status = tostring(Properties['status_code']), bin(TimeGenerated, 5m)
| order by TimeGenerated asc
```

Format as: **Time series**.

### Recent API errors with context

```kql
AppTraces
| where TimeGenerated > ago(24h)
| where tostring(Properties['event.name']) == "api_error"
| project
    TimeGenerated,
    email = tostring(Properties['user.email']),
    project = tostring(Properties['project.name']),
    model = tostring(Properties['model']),
    status = tostring(Properties['status_code']),
    duration_ms = toint(Properties['duration_ms']),
    attempt = toint(Properties['attempt']),
    error = tostring(Properties['error'])
| order by TimeGenerated desc
| take 50
```

---

## 10. Hosts

### Distinct hosts seen (last 7d)

```kql
AppTraces
| where TimeGenerated > ago(7d)
| extend host = tostring(Properties['host.name'])
| where isnotempty(host)
| summarize
    users = make_set(tostring(Properties['user.email'])),
    sessions = dcount(tostring(Properties['session.id'])),
    first_seen = min(TimeGenerated),
    last_seen = max(TimeGenerated)
  by host
| order by sessions desc
```

### Cost by host

```kql
AppMetrics
| where TimeGenerated > ago(7d)
| where Name == "claude_code.cost.usage"
| extend p = parse_json(Properties)
| extend host = tostring(p['host.name'])
| where isnotempty(host)
| summarize Cost = sum(Sum) by host
| order by Cost desc
```

---

## Tips

- **Time picker vs `ago(N)`**: in Grafana, `$__timeFilter(TimeGenerated)`
  uses the dashboard's time picker. Hard-coded `ago(7d)` ignores the
  picker. Use the macro for dashboard panels and `ago()` for ad-hoc.
- **Empty results check**: if a query returns nothing, replace the filter
  clause temporarily with `| where Name startswith "claude_code"` (for
  metrics) or remove the `event.name` filter (for traces) to confirm
  data exists at all.
- **Joining metrics + events**: `session.id` and `user.email` are the
  reliable join keys (present on both sides).
- **Properties shape**: `AppMetrics` stores `Properties` as a JSON string
  — use `parse_json()`. `AppTraces` stores it as a parsed bag — use
  `Properties['key']` directly.
