# tools

Helper scripts for the agent-otel deployment.

## `install-claude-launcher.sh`

Installs `~/.local/bin/claude` — a thin wrapper that injects host and project
context into `OTEL_RESOURCE_ATTRIBUTES` before exec'ing the real Claude Code
binary. The OTel SDK reads resource attributes at process startup, before any
SessionStart hook fires, so the wrapper has to set them *before* `claude` runs.

Attributes added to every metric and event:

| Attribute | Value |
|---|---|
| `host.name` | `hostname -s` (short hostname) |
| `project.name` | git repo root basename, or `$PWD` basename |
| `project.path` | git repo root, or `$PWD` |
| `project.repo` | `git remote get-url origin` (omitted if not a repo) |

Existing attributes set by Claude Code itself (`user.email`, `session.id`,
`organization.id`, …) are unaffected — the wrapper appends, never overwrites.

### Install

```bash
./tools/install-claude-launcher.sh
```

What it does:
1. Resolves the current `claude` in PATH (must not already be the wrapper).
2. Writes `~/.local/bin/claude` with the real binary path baked in.
3. Adds `~/.local/bin` to PATH at the front of `~/.zshrc` or `~/.bashrc`,
   guarded by a marker comment so re-running is idempotent.

After install, open a fresh shell (or `exec $SHELL`) and verify:

```bash
which claude        # → ~/.local/bin/claude
claude --version    # same version as before
```

### Verify telemetry

After `claude` runs in a project for ~60s, query Log Analytics:

```kql
AppMetrics
| where TimeGenerated > ago(5m)
| where Name == "claude_code.token.usage"
| extend p = parse_json(Properties)
| project host = tostring(p["host.name"]),
          project = tostring(p["project.name"]),
          email = tostring(p["user.email"]),
          Sum
| take 5
```

### Re-run / refresh

Re-run the installer any time to regenerate the wrapper (e.g., after a Claude
Code update relocates the binary). The PATH hook is preserved.

### Uninstall

```bash
rm ~/.local/bin/claude
# Then remove the marker block from ~/.zshrc or ~/.bashrc:
#   # agent-otel: claude launcher
#   case ":${PATH}:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH";; esac
```

### Limitations

- Project is detected from `$PWD` at the moment `claude` starts. `cd`'ing
  mid-session doesn't update the resource attributes (env vars are read once
  during OTel SDK init).
- `OTEL_RESOURCE_ATTRIBUTES` is W3C Baggage format (comma-separated
  `key=value` pairs). Commas or `=` in values would break parsing —
  unlikely in hostnames/paths/git URLs but worth knowing.
