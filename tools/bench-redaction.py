#!/usr/bin/env python3
"""Benchmark redaction implementations head-to-head.

Compares:
  - Python `re` (slow ref point)
  - Google `re2` (apples-to-apples with the OTTL collector layer that uses
    Go's RE2 under the hood)
  - openai/privacy-filter via Optimum + ONNX Runtime, three quants:
      * fp32 (model.onnx)         — quality ceiling
      * fp16 (model_fp16.onnx)    — half-precision activations
      * q4f16 (model_q4f16.onnx)  — 4-bit weights + fp16 activations,
                                    the realistic on-device shipping target

Corpus is built from four buckets so we can see how each implementation
behaves across the kinds of input we actually emit:
  - clean:     real api_response_body bodies pulled from App Insights
  - secrets:   one record per regex pattern in PATTERNS
  - pii:       names/emails/addresses/phones/dates that regex misses
  - mixed:     secret + PII in the same record

For each implementation we measure:
  - load time (cold start — relevant for on-device first-launch UX)
  - per-record latency (median + P95)
  - throughput (records / sec)
  - peak RSS during the run
  - redaction count (sanity check that the implementation is actually doing work)

Run:
    python tools/bench-redaction.py
"""

from __future__ import annotations

import gc
import json
import os
import re
import resource
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import re2  # type: ignore[import-untyped]

# Import patterns from the canonical source so the benchmark stays in sync.
# The generator file is named with hyphens (matches the rest of the
# infra/ scripts), so importlib's machinery is the cleanest way to load
# it without renaming for Python's identifier rules.
import importlib.util  # noqa: E402

_GEN_PATH = (
    Path(__file__).resolve().parent.parent / "infra" / "generate-collector-config.py"
)
_spec = importlib.util.spec_from_file_location("_gen_collector_config", _GEN_PATH)
assert _spec is not None and _spec.loader is not None
_gen_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen_mod)
PATTERNS = _gen_mod.PATTERNS

REAL_BODIES_PATH = Path("/tmp/bench-real-bodies.json")


# ────────────────────────────────────────────────────────────────────────
# corpus
# ────────────────────────────────────────────────────────────────────────


def real_bodies() -> list[str]:
    raw = json.loads(REAL_BODIES_PATH.read_text())
    return [r["body"] for r in raw]


# One synthetic record per regex pattern: a sentence ending with a fake
# token that should match the pattern. Keeps each record short so the
# regex/regex2 cost is dominated by pattern dispatch, not text length.
SYNTHETIC_SECRET_RECORDS = [
    "deploying with anthropic key sk-ant-aaaaaaaaaaaaaaaaaaaaaa",
    "openai key sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa for the test",
    "aws creds: AKIAAAAAAAAAAAAAAAAA region us-west-2",
    "github token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa for ci",
    "slack bot xoxb-12345-67890-aaaaaaaaaaaaaaaaaaaa connected",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.aaaaaaaaaaaaaaaaaaaaaaaa",
    (
        "ssh key: -----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890\n"
        "-----END RSA PRIVATE KEY-----"
    ),
    "DATABASE_URL=postgres://otel:hunter2@db.example.internal:5432/agent_otel",
    "linear API token lin_api_abcdefghijklmnopqrstuvwxyz0123456789ABCDEF",
    "tavily key tvly-abcdefghijklmnopqrstuvwx",
    "google api AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI",
    "perplexity pplx-abcdefghij0123456789ABCDEF",
    "xai-1234567890abcdefghij",
    "groq gsk_abcdefghij0123456789ABCDEFGHIJ0123456789",
    "huggingface hf_abcdefghij0123456789ABCDEFGH",
    "replicate r8_abcdefghij0123456789ABCDEFGH",
    "firecrawl fc-1234567890abcdef1234567890abcdef",
    "gitlab glpat-abcdefghij0123456789",
    "stripe sk_live_abcdefghij0123456789",
    "npm npm_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "docker dckr_pat_abcdefghij0123456789ABCDEFGH",
    "figma figd_abcdefghij0123456789",
    "sendgrid SG.aaaaaaaaaaaaaaaaaaaaaa.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "notion secret_abcdefghij0123456789ABCDEFGHIJ0123456789ABCD",
    "API_KEY=abcdefghijklmnop1234567890XYZ",
    "client_secret = abcdefghij0123456789ABCDEF",
]

# PII the regex layer can't see, but privacy-filter can.
SYNTHETIC_PII_RECORDS = [
    "Hi John Stanford, please email me at jxstanford@wemodulate.energy.",
    "Contact 702-555-0142 to schedule. We met on April 15, 2026.",
    "Ship to 4180 W Old Vegas Rd, Las Vegas NV 89124, attention Daisy.",
    "My credit card is 4111-1111-1111-1111, exp 12/27.",
    "Patient name: Sarah Chen. DOB: 1989-03-14. SSN omitted on purpose.",
    "Loop in alex.green@example.com and (415) 555-0199 — they're in SF.",
    "The CEO is Marcus Holloway, reachable at marcus.holloway@acmecorp.com",
    "Bank account 1234567890 routing 021000021 for the wire.",
    "She lives at 221B Baker Street, London NW1 6XE.",
    "Today is 2026-04-27 and the deadline is May 1, 2026.",
]

# Mixed: regex hits AND PII in the same record. Both layers should fire.
MIXED_RECORDS = [
    "Email John Stanford <jxstanford@wemodulate.energy>: rotate sk-ant-aaaaaaaaaaaaaaaaaaaaaa today.",
    "Tell Matt Wallace (67.190.58.98) the new DB url is postgres://otel:hunter2@db.example.internal:5432/agent_otel.",
    "Slack alex.green@example.com the gh token ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.",
    "Ship the AWS key AKIAAAAAAAAAAAAAAAAA to Sarah Chen at 4180 W Old Vegas Rd.",
    "client_secret = abcdefghij0123456789ABCDEF — call 702-555-0142 if rotation fails.",
]


def build_corpus() -> dict[str, list[str]]:
    return {
        "clean": real_bodies(),
        "secrets": SYNTHETIC_SECRET_RECORDS,
        "pii": SYNTHETIC_PII_RECORDS,
        "mixed": MIXED_RECORDS,
    }


# ────────────────────────────────────────────────────────────────────────
# regex backends
# ────────────────────────────────────────────────────────────────────────


def python_re_redact(records: list[str]) -> tuple[list[str], int]:
    """Apply patterns sequentially using Python `re`. Return outputs and total
    redactions. Pattern compilation is cached at module load."""
    outs: list[str] = []
    total = 0
    for rec in records:
        out = rec
        for _label, pattern, replacement in _PY_RE_COMPILED:
            out, n = pattern.subn(replacement, out)
            total += n
        outs.append(out)
    return outs, total


def re2_redact(records: list[str]) -> tuple[list[str], int]:
    """Same patterns, Google RE2 engine — DFA-based, matches what the OTel
    collector does in production (Go's regexp package = RE2)."""
    outs: list[str] = []
    total = 0
    for rec in records:
        out = rec
        for _label, pattern, replacement in _RE2_COMPILED:
            out, n = pattern.subn(replacement, out)
            total += n
        outs.append(out)
    return outs, total


def _compile_patterns() -> tuple[list, list]:
    """Compile patterns under both engines once, before timing.

    The OTTL replacement template uses `$$N` for capture-group refs (the
    `$$` is a YAML escape so the ACA secret loader doesn't treat it as an
    env var). When matching with Python `re` directly we want `\\1` style.
    """
    py_compiled = []
    re2_compiled = []
    for label, pattern, replacement in PATTERNS:
        # Translate $$N → \\N for Python's re/re2 sub semantics.
        py_repl = re.sub(r"\$\$(\d)", r"\\\1", replacement)
        re2_repl = py_repl  # google-re2's sub() uses backslash refs too
        # google-re2 doesn't support \\.* in alternation lookaheads; both
        # libraries handle our pattern set fine since we only use
        # alternation, character classes, and bounded quantifiers.
        py_compiled.append((label, re.compile(pattern), py_repl))
        re2_compiled.append((label, re2.compile(pattern), re2_repl))
    return py_compiled, re2_compiled


_PY_RE_COMPILED, _RE2_COMPILED = _compile_patterns()


# ────────────────────────────────────────────────────────────────────────
# privacy-filter via ONNX Runtime
# ────────────────────────────────────────────────────────────────────────


def make_privacy_filter(quant: str):
    """Build a raw ONNX Runtime classifier for openai/privacy-filter.

    We bypass Optimum because its config registry doesn't recognize the
    `openai_privacy_filter` model_type. The HF snapshot ships one .onnx
    file per quant level inside the `onnx/` subdirectory.

    Returns a callable: `clf(text) -> list of (entity_group, start, end)`
    where start/end are character offsets in the original text.
    """
    import glob

    import numpy as np
    import onnxruntime
    from tokenizers import Tokenizer  # type: ignore[import-untyped]

    file_name = {
        "fp32": "model.onnx",
        "fp16": "model_fp16.onnx",
        "q4": "model_q4.onnx",
        "q4f16": "model_q4f16.onnx",
    }[quant]

    # The HF snapshot dir holds the ONNX files. Pick whatever snapshot
    # was downloaded (there's only one).
    snap = glob.glob(
        os.path.expanduser(
            "~/.cache/huggingface/hub/models--openai--privacy-filter/snapshots/*/"
        )
    )[0]
    onnx_path = os.path.join(snap, "onnx", file_name)

    # Use the rust tokenizers library directly — sidesteps transformers'
    # AutoTokenizer registry which doesn't recognize the model_type yet.
    tokenizer = Tokenizer.from_file(os.path.join(snap, "tokenizer.json"))
    # Cap inputs at 8192 tokens for the bench. Production deployment can
    # raise this — privacy-filter's max ctx is 128k.
    tokenizer.enable_truncation(max_length=8192)

    # id2label lives in config.json
    config = json.loads(open(os.path.join(snap, "config.json")).read())
    id2label = {int(k): v for k, v in config["id2label"].items()}

    sess_options = onnxruntime.SessionOptions()
    # Prefer aggressive graph optimization — adds a bit of load time, big
    # speed-up at inference. Match what a production deployment would do.
    sess_options.graph_optimization_level = (
        onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    )
    session = onnxruntime.InferenceSession(
        onnx_path,
        sess_options=sess_options,
        providers=["CPUExecutionProvider"],
    )

    def classify(text: str):
        # The rust tokenizers library returns ids + offsets (char-space)
        # in one Encoding. Add a leading [0,0] sentinel only if the model
        # expects a BOS token — privacy-filter doesn't (no bos_token_id
        # in config), so we feed exactly what tokenizer.encode produces.
        enc = tokenizer.encode(text)
        ids = np.array([enc.ids], dtype=np.int64)
        attn = np.array([enc.attention_mask], dtype=np.int64)
        offsets = enc.offsets
        feed = {"input_ids": ids, "attention_mask": attn}
        logits = session.run(["logits"], feed)[0]
        pred_ids = logits.argmax(-1)[0]
        # BIOES decode: walk the per-token tag sequence; coalesce B/I/E
        # of the same entity into one span; S- is a singleton; O closes.
        spans: list[tuple[str, int, int]] = []
        cur_label: str | None = None
        cur_start: int = 0
        cur_end: int = 0
        for i, lab_id in enumerate(pred_ids):
            tag = id2label[int(lab_id)]
            if tag == "O":
                if cur_label is not None:
                    spans.append(
                        (
                            cur_label,
                            int(offsets[cur_start][0]),
                            int(offsets[cur_end][1]),
                        )
                    )
                    cur_label = None
                continue
            prefix, _, ent = tag.partition("-")
            if prefix == "S":
                if cur_label is not None:
                    spans.append(
                        (
                            cur_label,
                            int(offsets[cur_start][0]),
                            int(offsets[cur_end][1]),
                        )
                    )
                spans.append((ent, int(offsets[i][0]), int(offsets[i][1])))
                cur_label = None
            elif prefix == "B":
                if cur_label is not None:
                    spans.append(
                        (
                            cur_label,
                            int(offsets[cur_start][0]),
                            int(offsets[cur_end][1]),
                        )
                    )
                cur_label = ent
                cur_start = i
                cur_end = i
            else:  # I or E
                if cur_label == ent:
                    cur_end = i
                else:
                    if cur_label is not None:
                        spans.append(
                            (
                                cur_label,
                                int(offsets[cur_start][0]),
                                int(offsets[cur_end][1]),
                            )
                        )
                    cur_label = ent
                    cur_start = i
                    cur_end = i
        if cur_label is not None:
            spans.append(
                (cur_label, int(offsets[cur_start][0]), int(offsets[cur_end][1]))
            )
        return spans

    return classify


def privacy_filter_redact(clf, records: list[str]) -> tuple[list[str], int]:
    """Run privacy-filter on each record; replace detected spans with
    [REDACTED:<entity>]. Walk spans right-to-left so prior offsets stay
    valid as we mutate the string."""
    outs: list[str] = []
    total = 0
    for rec in records:
        spans = clf(rec)
        out = rec
        for entity, start, end in sorted(spans, key=lambda s: -s[1]):
            out = out[:start] + f"[REDACTED:{entity}]" + out[end:]
            total += 1
        outs.append(out)
    return outs, total


# ────────────────────────────────────────────────────────────────────────
# benchmark loop
# ────────────────────────────────────────────────────────────────────────


@dataclass
class Result:
    impl: str
    bucket: str
    n_records: int
    total_chars: int
    total_seconds: float
    per_record_ms: list[float] = field(default_factory=list)
    redactions: int = 0

    @property
    def records_per_sec(self) -> float:
        return self.n_records / self.total_seconds if self.total_seconds else 0.0

    @property
    def chars_per_sec(self) -> float:
        return self.total_chars / self.total_seconds if self.total_seconds else 0.0

    @property
    def median_ms(self) -> float:
        return statistics.median(self.per_record_ms) if self.per_record_ms else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.per_record_ms:
            return 0.0
        sorted_lat = sorted(self.per_record_ms)
        idx = int(0.95 * (len(sorted_lat) - 1))
        return sorted_lat[idx]


def time_one(impl: str, run, records: list[str]) -> tuple[Result, list[str]]:
    per_rec: list[float] = []
    total_redactions = 0
    outs: list[str] = []
    t_total_start = time.perf_counter()
    for rec in records:
        t0 = time.perf_counter()
        out, n = run([rec])
        per_rec.append((time.perf_counter() - t0) * 1000)
        total_redactions += n
        outs.append(out[0])
    total = time.perf_counter() - t_total_start
    return (
        Result(
            impl=impl,
            bucket="(per_rec)",
            n_records=len(records),
            total_chars=sum(len(r) for r in records),
            total_seconds=total,
            per_record_ms=per_rec,
            redactions=total_redactions,
        ),
        outs,
    )


def peak_rss_mb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    # macOS reports ru_maxrss in bytes; Linux in kilobytes.
    if sys.platform == "darwin":
        return ru.ru_maxrss / (1024 * 1024)
    return ru.ru_maxrss / 1024


# ────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────


def fmt_row(r: Result) -> str:
    return (
        f"{r.impl:18}  {r.bucket:8}  n={r.n_records:3}  "
        f"chars={r.total_chars:6}  "
        f"med={r.median_ms:7.2f}ms  p95={r.p95_ms:7.2f}ms  "
        f"thru={r.records_per_sec:7.1f} rec/s  "
        f"chars/s={r.chars_per_sec:9.0f}  "
        f"redactions={r.redactions:3}"
    )


def run_regex_bench(impl: str, run, corpus: dict[str, list[str]]) -> list[Result]:
    out_results = []
    for bucket, records in corpus.items():
        # warm up once to fill any per-process caches
        run(records)
        r, _ = time_one(impl, run, records)
        r.bucket = bucket
        out_results.append(r)
    return out_results


def run_pf_bench(
    quant: str, corpus: dict[str, list[str]]
) -> tuple[list[Result], float]:
    impl = f"privacy-filter:{quant}"
    print(f"loading {impl}...", file=sys.stderr, flush=True)
    t0 = time.perf_counter()
    clf = make_privacy_filter(quant)
    load_seconds = time.perf_counter() - t0
    print(f"  loaded in {load_seconds:.1f}s", file=sys.stderr, flush=True)

    def run(records: list[str], _clf=clf):
        return privacy_filter_redact(_clf, records)

    out_results = []
    for bucket, records in corpus.items():
        # warm up — kernel caching, ORT graph optimization passes
        run(records[:1])
        r, _ = time_one(impl, run, records)
        r.bucket = bucket
        out_results.append(r)
    gc.collect()
    return out_results, load_seconds


def main() -> None:
    corpus = build_corpus()
    print(f"\ncorpus: {dict((k, len(v)) for k, v in corpus.items())}", file=sys.stderr)

    print("\n" + "=" * 100)
    print("REGEX BACKENDS (single-threaded, hot caches)")
    print("=" * 100)
    py_results = run_regex_bench("python-re", python_re_redact, corpus)
    re2_results = run_regex_bench("google-re2", re2_redact, corpus)
    for r in py_results + re2_results:
        print(fmt_row(r))
    print(f"  peak RSS so far: {peak_rss_mb():.0f} MB")

    print("\n" + "=" * 100)
    print("PRIVACY-FILTER (ONNX Runtime, CPU execution provider)")
    print("=" * 100)
    pf_load: dict[str, float] = {}
    pf_results: list[Result] = []
    quants = os.environ.get("BENCH_QUANTS", "fp32,fp16,q4f16").split(",")
    for q in quants:
        results, load_s = run_pf_bench(q, corpus)
        pf_load[q] = load_s
        pf_results.extend(results)
        for r in results:
            print(fmt_row(r))
        print(f"  peak RSS after {q}: {peak_rss_mb():.0f} MB")

    print("\n" + "=" * 100)
    print("LOAD TIMES (cold start, model -> ready-for-inference)")
    print("=" * 100)
    for q, s in pf_load.items():
        print(f"  privacy-filter:{q:8} → {s:.2f}s")
    print("  python-re / google-re2 → effectively 0 (pattern compile @ module import)")


if __name__ == "__main__":
    main()
