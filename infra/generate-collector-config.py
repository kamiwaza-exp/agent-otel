#!/usr/bin/env python3
"""Generate collector-config.azure.yaml from a single source of truth.

Why a generator: the OTel collector config holds an N×M matrix of redaction
rules (patterns × attribute names). Each new attribute or pattern previously
required hand-editing every other line. This script materializes the cross-
product so adding either a pattern or an attribute is a one-line change.

Run:
    python infra/generate-collector-config.py

Output: collector-config.azure.yaml at repo root.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parent.parent
DST = REPO_ROOT / "collector-config.azure.yaml"

# Attributes that may carry free-form text containing secrets. Each Claude
# Code event type uses a different attribute key:
#   - user_prompt event       → attributes["prompt"]
#   - api_error event         → attributes["error"]
#   - api_request_body event  → attributes["body"]   (large, often truncated)
#   - api_response_body event → attributes["body"]   (same key, smaller)
#   - tool_result event       → attributes["tool_input"]
#
# Adding a new attribute here multiplies every pattern below by it. OTTL
# error_mode: ignore handles the case where a record doesn't have the attribute.
REDACTED_ATTRIBUTES = ["prompt", "error", "body", "tool_input"]


# (label, regex_pattern, replacement_template)
# replacement_template uses $$N for capture groups (OTel collector escapes $ as
# $$ to disambiguate from env var expansion).
PATTERNS: list[tuple[str, str, str]] = [
    # Anthropic API keys (must run first — sk-ant-* would otherwise be
    # partially matched by the OpenAI sk- pattern, though the hyphen would
    # break it; defensive ordering).
    ("anthropic-key", r"sk-ant-[a-zA-Z0-9_-]{20,}", "[REDACTED:anthropic-key]"),
    # OpenAI API keys (48-char alnum after sk-)
    ("openai-key", r"sk-[a-zA-Z0-9]{48}", "[REDACTED:openai-key]"),
    # AWS access key IDs
    ("aws-access-key", r"AKIA[A-Z0-9]{16}", "[REDACTED:aws-access-key]"),
    # GitHub personal access tokens (ghp_, gho_, ghu_, ghs_)
    ("github-token", r"gh[opsu]_[A-Za-z0-9_]{36,}", "[REDACTED:github-token]"),
    # Slack tokens (xoxb-, xoxp-, xoxa-, xoxr-, xoxs-)
    ("slack-token", r"xox[baprs]-[A-Za-z0-9-]+", "[REDACTED:slack-token]"),
    # JWT tokens (3 base64url segments separated by .)
    (
        "jwt",
        r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
        "[REDACTED:jwt]",
    ),
    # Bearer-style auth tokens
    (
        "bearer-token",
        r"(?i)bearer\s+[A-Za-z0-9._\-+/=]{20,}",
        "Bearer [REDACTED:bearer-token]",
    ),
    # PEM-encoded private keys (multi-line, lazy match the body)
    (
        "private-key",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END[\s\S]+?-----",
        "[REDACTED:private-key]",
    ),
    # Database connection strings with embedded creds
    (
        "db-credentials",
        r"(postgres|postgresql|mysql|mongodb|redis)://[^:]+:[^@]+@",
        r"$$1://[REDACTED:db-credentials]@",
    ),
    # Vendor-prefix tokens
    ("linear", r"lin_(api|oauth)_[A-Za-z0-9]{40,}", "[REDACTED:linear]"),
    ("tavily", r"tvly-[A-Za-z0-9_-]{20,}", "[REDACTED:tavily]"),
    ("google-api", r"AIza[0-9A-Za-z_-]{35}", "[REDACTED:google-api]"),
    ("perplexity", r"pplx-[A-Za-z0-9]{20,}", "[REDACTED:perplexity]"),
    ("xai", r"xai-[A-Za-z0-9_-]{20,}", "[REDACTED:xai]"),
    ("groq", r"gsk_[A-Za-z0-9]{40,}", "[REDACTED:groq]"),
    ("huggingface", r"hf_[A-Za-z0-9]{30,}", "[REDACTED:huggingface]"),
    ("replicate", r"r8_[A-Za-z0-9]{30,}", "[REDACTED:replicate]"),
    ("firecrawl", r"fc-[a-f0-9]{32}", "[REDACTED:firecrawl]"),
    ("gitlab", r"glpat-[A-Za-z0-9_-]{20}", "[REDACTED:gitlab]"),
    ("stripe", r"(sk|pk|rk)_(live|test)_[A-Za-z0-9]{20,}", "[REDACTED:stripe]"),
    ("npm-token", r"npm_[A-Za-z0-9]{36}", "[REDACTED:npm-token]"),
    ("docker-hub", r"dckr_pat_[A-Za-z0-9_-]{32,}", "[REDACTED:docker-hub]"),
    ("figma", r"figd_[A-Za-z0-9_-]{20,}", "[REDACTED:figma]"),
    (
        "sendgrid",
        r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}",
        "[REDACTED:sendgrid]",
    ),
    ("notion", r"(secret|ntn)_[A-Za-z0-9]{43,44}", "[REDACTED:notion]"),
    # Contextual catch-all — runs LAST so vendor-specific labels win on overlap
    (
        "contextual-secret",
        (
            r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret[_-]?key|"
            r"secret[_-]?token|auth[_-]?token|access[_-]?token|"
            r"client[_-]?secret|signing[_-]?key|password|passwd|"
            r"credential)\s*[:=]\s*[A-Za-z0-9+/_.\-=]{16,}"
        ),
        r"$$1=[REDACTED:contextual-secret]",
    ),
]


def yaml_quote(s: str) -> str:
    """Render `s` as a single-quoted YAML scalar.

    OTel collector configs use single-quoted YAML for OTTL statements because
    OTTL is whitespace-tolerant and contains its own internal double quotes.
    We escape internal single quotes by doubling them per the YAML spec.
    """
    return "'" + s.replace("'", "''") + "'"


def render_statement(attribute: str, pattern: str, replacement: str) -> str:
    """One OTTL replace_pattern statement for (attribute, pattern, replacement).

    The OTel transform processor parses each statement string as OTTL.
    Patterns must use Go's RE2 syntax. Replacement uses $$N for capture
    refs ($-doubling avoids env-var expansion in the YAML pipeline).
    """
    inner = f'replace_pattern(attributes["{attribute}"], "{pattern}", "{replacement}")'
    return yaml_quote(inner)


def build_redaction_block() -> str:
    """Render the transform/redact_secrets processor stanza."""
    lines: list[str] = []
    for label, pattern, replacement in PATTERNS:
        # Escape backslashes once for YAML embedding (regex \s → \\s in YAML).
        # We use single-quoted YAML so YAML doesn't re-escape backslashes,
        # but OTTL still needs the literal escape characters preserved as
        # written. Doubling backslashes here produces the correct OTTL string.
        # Note: in Python source we write r"\s", so pattern already contains
        # a literal backslash + s. We need YAML to deliver "\\s" so OTTL
        # sees \s, which means we escape \ → \\ in the rendered YAML.
        yaml_pattern = pattern.replace("\\", "\\\\").replace('"', '\\"')
        yaml_replacement = replacement.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f"          # {label}")
        for attr in REDACTED_ATTRIBUTES:
            lines.append(
                "          - " + render_statement(attr, yaml_pattern, yaml_replacement)
            )
    return "\n".join(lines)


HEADER = dedent(
    """\
    # OpenTelemetry Collector config — Azure deployment
    #
    # GENERATED FILE — edit infra/generate-collector-config.py instead.
    # Run: python infra/generate-collector-config.py
    #
    # Counterpart to collector-config.yaml (local/Loki). This config is
    # mounted into the Azure Container App version of the collector.
    #
    # Data flow:
    #   OTLP in (4317/4318)
    #     ├─ metrics → azuremonitor → Application Insights → AppMetrics
    #     ├─ logs    → azuremonitor → Application Insights → AppTraces
    #     └─ traces  → azuremonitor → Application Insights → AppDependencies
    #
    # Traces are populated only when clients run with
    # CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1 + OTEL_TRACES_EXPORTER=otlp.
    """
)


def main() -> None:
    redaction_block = build_redaction_block()
    config = (
        HEADER
        + dedent(
            """\

            extensions:
              health_check:
                endpoint: 0.0.0.0:13133

            receivers:
              otlp:
                protocols:
                  grpc:
                    endpoint: 0.0.0.0:4317
                  http:
                    endpoint: 0.0.0.0:4318

            processors:
              memory_limiter:
                check_interval: 1s
                limit_percentage: 80
                spike_limit_percentage: 25

              resource:
                attributes:
                  - key: deployment.environment
                    value: "azure-production"
                    action: upsert
                  - key: deployment.platform
                    value: "azure-container-apps"
                    action: upsert

              batch:
                send_batch_size: 1000
                send_batch_max_size: 1500
                timeout: 10s

              # In-flight secret redaction. Acts on attribute values that carry
              # free-form text (prompt, error, body, tool_input). Runs BEFORE
              # export so cleartext never leaves the collector.
              #
              # error_mode: ignore — most records lack most of these attributes
              # (only user_prompt has prompt; only api_*_body has body; etc).
              # Without ignore, missing-attribute errors would fail entire
              # batches.
              #
              # Pattern strategy: only redact tokens with structured prefixes /
              # shapes — high-confidence, low false-positive. Generic high-
              # entropy strings (32-char hex) are NOT redacted because they
              # collide with request IDs, file hashes, etc.
              #
              # Patterns and target attributes are sourced from
              # infra/generate-collector-config.py — DO NOT edit this block
              # directly.
              transform/redact_secrets:
                error_mode: ignore
                log_statements:
                  - context: log
                    statements:
            """
        )
        + redaction_block
        + dedent(
            """

            exporters:
              azuremonitor:
                connection_string: ${env:APPLICATIONINSIGHTS_CONNECTION_STRING}
                maxbatchsize: 100
                maxbatchinterval: 10s

            service:
              extensions: [health_check]
              pipelines:
                metrics:
                  receivers: [otlp]
                  processors: [memory_limiter, resource, batch]
                  exporters: [azuremonitor]
                logs:
                  receivers: [otlp]
                  # Order: memory_limiter (admission) → resource (decorate) →
                  # transform/redact_secrets (scrub) → batch (efficiency).
                  # Redaction MUST precede batch so individual records are
                  # scrubbed before any cross-record grouping.
                  processors: [memory_limiter, resource, transform/redact_secrets, batch]
                  exporters: [azuremonitor]
                traces:
                  receivers: [otlp]
                  # No redaction here. transform/redact_secrets uses
                  # log_statements only. Span attributes pass through.
                  # Acceptable because the bodies we care about (prompt,
                  # api_*_body, tool_input) are emitted as LOG events, not
                  # span attributes. Revisit if Claude Code starts attaching
                  # body content to spans directly.
                  processors: [memory_limiter, resource, batch]
                  exporters: [azuremonitor]
              telemetry:
                logs:
                  level: info
                metrics:
                  level: basic
            """
        )
    )
    DST.write_text(config)
    pattern_count = len(PATTERNS)
    attr_count = len(REDACTED_ATTRIBUTES)
    print(
        f"Wrote {DST} ({DST.stat().st_size} bytes)\n"
        f"  {pattern_count} patterns × {attr_count} attributes "
        f"= {pattern_count * attr_count} redaction statements"
    )


if __name__ == "__main__":
    main()
