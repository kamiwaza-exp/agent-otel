#!/usr/bin/env python3
"""Local OTLP redaction proxy for Claude Code telemetry.

Sits in front of the cloud collector. Claude Code emits OTLP/HTTP to
localhost:4318; this proxy applies regex + optional privacy-filter
redaction to the free-form attribute values that carry conversation
content (prompt, body, tool_input, user_prompt, error), then forwards
to the cloud collector unchanged otherwise.

Pipeline:
    Claude Code  → http://localhost:4318/v1/{logs,traces,metrics}
                 → this proxy (regex + ML)
                 → https://agent-otel-collector.<region>.azurecontainerapps.io
                 → Application Insights

Metrics pass through unmodified — no free-text attributes.

Run:
    python tools/redaction-proxy.py
        [--listen 0.0.0.0:4318]
        [--upstream https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io]
        [--privacy-filter]   # enable ML redaction in addition to regex
        [--quant q4f16]      # fp32 | fp16 | q4f16 (default q4f16)

The privacy-filter model is loaded once at startup (~0.1–1s for q4f16).
First-run cost: HuggingFace will download ~400MB of weights to
~/.cache/huggingface if not present.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import re
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("redaction-proxy")


# ─────────────────────────────────────────────────────────────────────
# Shared pattern source
# ─────────────────────────────────────────────────────────────────────
#
# Patterns live in infra/generate-collector-config.py — same source the
# cloud collector YAML is generated from. Importing here keeps regex
# rules in lockstep across both layers.

_HERE = Path(__file__).resolve().parent
# Two layouts work:
#   - dev / repo: tools/redaction-proxy.py + infra/generate-collector-config.py
#   - installed:  $INSTALL_DIR/redaction-proxy.py + $INSTALL_DIR/generate-collector-config.py
_GEN_CANDIDATES = [
    _HERE / "generate-collector-config.py",  # installed (sibling)
    _HERE.parent / "infra" / "generate-collector-config.py",  # repo
]
_GEN_PATH = next((p for p in _GEN_CANDIDATES if p.exists()), None)
if _GEN_PATH is None:
    raise FileNotFoundError(
        f"generate-collector-config.py not found in any of: {_GEN_CANDIDATES}"
    )
_spec = importlib.util.spec_from_file_location("_gen_collector_config", _GEN_PATH)
assert _spec is not None and _spec.loader is not None
_gen_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen_mod)
PATTERNS = _gen_mod.PATTERNS
REDACTED_ATTRIBUTES = set(_gen_mod.REDACTED_ATTRIBUTES) | {
    # Span-level attribute Claude Code attaches to claude_code.interaction
    # spans when OTEL_LOG_USER_PROMPTS=1; not in the collector config (which
    # only handles log_statements) but we want to scrub it here too.
    "user_prompt",
}

# Compile patterns once. Translate the YAML/OTTL `$$N` capture-group
# escapes back to Python's `\\N` style.
_COMPILED_PATTERNS: list[tuple[str, re.Pattern[str], str]] = []
for _label, _pattern, _replacement in PATTERNS:
    _py_repl = re.sub(r"\$\$(\d)", r"\\\1", _replacement)
    _COMPILED_PATTERNS.append((_label, re.compile(_pattern), _py_repl))


def regex_redact(s: str) -> str:
    """Apply every pattern in declaration order. Patterns are designed to
    have low false-positive overlap, so sequential application is fine."""
    for _label, pattern, replacement in _COMPILED_PATTERNS:
        s = pattern.sub(replacement, s)
    return s


# ─────────────────────────────────────────────────────────────────────
# Privacy-filter (optional)
# ─────────────────────────────────────────────────────────────────────


class PrivacyFilter:
    """Wraps the openai/privacy-filter ONNX model. Single-threaded — ORT's
    own thread pool handles parallelism inside the session.

    Tokenizer is the rust `tokenizers` library (sidesteps transformers'
    AutoTokenizer registry which doesn't yet recognize the model_type).
    """

    # Default category allowlist. The model classifies into 8 entity types
    # (account_number, private_address, private_date, private_email,
    # private_person, private_phone, private_url, secret). For a logging/
    # audit deployment that prioritizes secret leaks over PII, restrict
    # to the two categories that map to credentials/account info — drop
    # the PII categories which produce more FPs and weren't the target.
    DEFAULT_CATEGORIES = frozenset({"secret", "account_number"})

    def __init__(
        self,
        quant: str = "q4f16",
        categories: frozenset[str] | None = None,
    ):
        self._allowed = categories or self.DEFAULT_CATEGORIES
        import glob

        import numpy as np
        import onnxruntime
        from tokenizers import Tokenizer  # type: ignore[import-untyped]

        self._np = np
        file_name = {
            "fp32": "model.onnx",
            "fp16": "model_fp16.onnx",
            "q4": "model_q4.onnx",
            "q4f16": "model_q4f16.onnx",
        }[quant]
        snaps = glob.glob(
            os.path.expanduser(
                "~/.cache/huggingface/hub/models--openai--privacy-filter/snapshots/*/"
            )
        )
        if not snaps:
            raise RuntimeError(
                "openai/privacy-filter not in HF cache. Run: hf download openai/privacy-filter"
            )
        snap = snaps[0]
        self._tk = Tokenizer.from_file(os.path.join(snap, "tokenizer.json"))
        self._tk.enable_truncation(max_length=8192)
        cfg = json.loads(Path(snap, "config.json").read_text())
        self._id2label = {int(k): v for k, v in cfg["id2label"].items()}

        opts = onnxruntime.SessionOptions()
        opts.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        self._sess = onnxruntime.InferenceSession(
            os.path.join(snap, "onnx", file_name),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        log.info("privacy-filter loaded (quant=%s)", quant)

    def redact(self, text: str) -> str:
        """Run privacy-filter on `text`, replace every detected span with
        [REDACTED:<entity_group>]. Walk spans right-to-left so prior
        offsets stay valid as the string is mutated."""
        if not text:
            return text
        np = self._np
        enc = self._tk.encode(text)
        ids = np.array([enc.ids], dtype=np.int64)
        attn = np.array([enc.attention_mask], dtype=np.int64)
        logits = self._sess.run(["logits"], {"input_ids": ids, "attention_mask": attn})[
            0
        ]
        pred = logits.argmax(-1)[0]

        # BIOES decode
        spans: list[tuple[str, int, int]] = []
        cur_label: str | None = None
        cur_s, cur_e = 0, 0
        for i, lab_id in enumerate(pred):
            tag = self._id2label[int(lab_id)]
            if tag == "O":
                if cur_label is not None:
                    spans.append(
                        (cur_label, enc.offsets[cur_s][0], enc.offsets[cur_e][1])
                    )
                    cur_label = None
                continue
            prefix, _, ent = tag.partition("-")
            if prefix == "S":
                if cur_label is not None:
                    spans.append(
                        (cur_label, enc.offsets[cur_s][0], enc.offsets[cur_e][1])
                    )
                spans.append((ent, enc.offsets[i][0], enc.offsets[i][1]))
                cur_label = None
            elif prefix == "B":
                if cur_label is not None:
                    spans.append(
                        (cur_label, enc.offsets[cur_s][0], enc.offsets[cur_e][1])
                    )
                cur_label = ent
                cur_s = i
                cur_e = i
            else:  # I or E
                if cur_label == ent:
                    cur_e = i
                else:
                    if cur_label is not None:
                        spans.append(
                            (cur_label, enc.offsets[cur_s][0], enc.offsets[cur_e][1])
                        )
                    cur_label = ent
                    cur_s = i
                    cur_e = i
        if cur_label is not None:
            spans.append((cur_label, enc.offsets[cur_s][0], enc.offsets[cur_e][1]))

        # Apply category allowlist. The PII categories (private_person,
        # private_address, etc.) get dropped here for the secrets-first
        # deployment; only categories in self._allowed survive to redaction.
        spans = [s for s in spans if s[0] in self._allowed]

        # Apply spans right-to-left so prior offsets stay valid.
        out = text
        for entity, start, end in sorted(spans, key=lambda s: -s[1]):
            out = out[:start] + f"[REDACTED:{entity}]" + out[end:]
        return out


# Global; assigned in main() if --privacy-filter flag is set
_PF: PrivacyFilter | None = None


def redact_string(s: str) -> str:
    """Two-pass redaction: regex first (cheap, deterministic on structured
    secrets), then privacy-filter (catches unstructured PII regex misses).
    Each pass is a no-op on already-redacted spans because [REDACTED:*]
    doesn't match either pattern set."""
    s = regex_redact(s)
    if _PF is not None:
        s = _PF.redact(s)
    return s


def redact_kv_attributes(attrs) -> int:
    """Walk an OTLP `KeyValue` repeated field; redact stringValues whose
    key is in REDACTED_ATTRIBUTES. Returns count of attributes touched."""
    n = 0
    for kv in attrs:
        if kv.key not in REDACTED_ATTRIBUTES:
            continue
        v = kv.value
        if v.HasField("string_value") and v.string_value:
            new = redact_string(v.string_value)
            if new != v.string_value:
                v.string_value = new
                n += 1
    return n


# ─────────────────────────────────────────────────────────────────────
# OTLP handlers
# ─────────────────────────────────────────────────────────────────────

UPSTREAM_URL: str = ""  # set in main()
_HTTP: httpx.AsyncClient | None = None


def _redact_logs(req: logs_service_pb2.ExportLogsServiceRequest) -> int:
    n = 0
    for rl in req.resource_logs:
        for sl in rl.scope_logs:
            for lr in sl.log_records:
                n += redact_kv_attributes(lr.attributes)
    return n


def _redact_traces(req: trace_service_pb2.ExportTraceServiceRequest) -> int:
    n = 0
    for rs in req.resource_spans:
        for ss in rs.scope_spans:
            for span in ss.spans:
                n += redact_kv_attributes(span.attributes)
                for ev in span.events:
                    n += redact_kv_attributes(ev.attributes)
    return n


async def _forward(path: str, content_type: str, body: bytes) -> Response:
    assert _HTTP is not None
    upstream = f"{UPSTREAM_URL.rstrip('/')}{path}"
    try:
        r = await _HTTP.post(
            upstream, content=body, headers={"Content-Type": content_type}, timeout=30.0
        )
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/octet-stream"),
        )
    except httpx.RequestError as e:
        log.error("upstream forward failed (%s): %s", upstream, e)
        return Response(content=b"", status_code=502)


app = FastAPI(title="redaction-proxy", docs_url=None, redoc_url=None)


@app.post("/v1/logs")
async def logs_handler(req: Request) -> Response:
    body = await req.body()
    ctype = req.headers.get("content-type", "application/x-protobuf")
    if ctype.startswith("application/x-protobuf"):
        msg = logs_service_pb2.ExportLogsServiceRequest()
        msg.ParseFromString(body)
        n = _redact_logs(msg)
        if n:
            log.info("logs: redacted %d attributes", n)
        body = msg.SerializeToString()
    return await _forward("/v1/logs", ctype, body)


@app.post("/v1/traces")
async def traces_handler(req: Request) -> Response:
    body = await req.body()
    ctype = req.headers.get("content-type", "application/x-protobuf")
    if ctype.startswith("application/x-protobuf"):
        msg = trace_service_pb2.ExportTraceServiceRequest()
        msg.ParseFromString(body)
        n = _redact_traces(msg)
        if n:
            log.info("traces: redacted %d attributes", n)
        body = msg.SerializeToString()
    return await _forward("/v1/traces", ctype, body)


@app.post("/v1/metrics")
async def metrics_handler(req: Request) -> Response:
    body = await req.body()
    ctype = req.headers.get("content-type", "application/x-protobuf")
    # Pass through. metric attributes are numeric counters / structured
    # labels — no free-text fields the redactor knows about.
    return await _forward("/v1/metrics", ctype, body)


@app.get("/healthz")
async def health() -> dict[str, str | bool]:
    return {
        "ok": True,
        "patterns": str(len(_COMPILED_PATTERNS)),
        "privacy_filter": "enabled" if _PF is not None else "disabled",
        "upstream": UPSTREAM_URL,
    }


# ─────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1:4318")
    parser.add_argument(
        "--upstream",
        default="https://agent-otel-collector.kindbay-ee480b05.centralus.azurecontainerapps.io",
    )
    parser.add_argument("--privacy-filter", action="store_true")
    parser.add_argument("--quant", default="q4f16", choices=["fp32", "fp16", "q4f16"])
    parser.add_argument(
        "--pf-categories",
        default="secret,account_number",
        help=(
            "Comma-separated list of privacy-filter categories to redact. "
            "Available: account_number, private_address, private_date, "
            "private_email, private_person, private_phone, private_url, "
            "secret. Default keeps only credential-related categories so PII "
            "false-positives don't redact things audit teams need to read. "
            "Set to 'all' to enable every category."
        ),
    )
    args = parser.parse_args()

    global UPSTREAM_URL, _HTTP, _PF
    UPSTREAM_URL = args.upstream
    if args.privacy_filter:
        if args.pf_categories.strip().lower() == "all":
            cats: frozenset[str] | None = None
        else:
            cats = frozenset(
                s.strip() for s in args.pf_categories.split(",") if s.strip()
            )
        _PF = PrivacyFilter(quant=args.quant, categories=cats)
        log.info(
            "privacy-filter categories: %s",
            sorted(cats) if cats else "(all 8)",
        )
    else:
        log.info("privacy-filter DISABLED — regex only")
    log.info(
        "regex patterns: %d, target attributes: %s",
        len(_COMPILED_PATTERNS),
        sorted(REDACTED_ATTRIBUTES),
    )
    log.info("forwarding to %s", UPSTREAM_URL)

    host, _, port = args.listen.partition(":")
    port_i = int(port or "4318")

    async def run_server() -> None:
        global _HTTP
        async with httpx.AsyncClient() as client:
            _HTTP = client
            import uvicorn

            cfg = uvicorn.Config(
                app, host=host or "127.0.0.1", port=port_i, log_level="info"
            )
            server = uvicorn.Server(cfg)
            await server.serve()

    asyncio.run(run_server())


if __name__ == "__main__":
    main()
