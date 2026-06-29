"""BigQuery query-performance pattern (a new, BigQuery-specific axis).

Runs queries and reports authoritative Job-API stats (bytes processed/billed, slot
ms, server-side elapsed) — not client wall-clock. Three explicit modes:

  mode: measure   -> record numbers, NO gate (measurement=True; not a pass/fail claim)
  mode: assert    -> gate against absolute thresholds (PASS/FAIL)
  mode: regression-> (phase 2) compare to a stored baseline

Cache is off by default (a cache hit makes the numbers meaningless). dry_run=true
estimates bytes for FREE (no execution). Cost guard via max_bytes_billed.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

SCHEMA = {
    "type": "object",
    "required": ["pattern", "queries"],
    "properties": {
        "pattern": {"const": "query_performance"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "target_dataset": {"type": "string"},
        "queries": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "mode"],
                "properties": {
                    "id": {"type": "string"},
                    "sql": {"type": "string"},
                    "mode": {"enum": ["measure", "assert", "compare", "regression"]},
                    "runs": {"type": "integer", "minimum": 1},
                    "dry_run": {"type": "boolean"},
                    "use_cache": {"type": "boolean"},
                    "max_bytes_billed": {"type": "number"},
                    "thresholds": {
                        "type": "object",
                        "properties": {
                            "max_bytes_scanned": {"type": "number"},   # common (BQ + Impala)
                            "max_elapsed_ms": {"type": "number"},       # common (p95 over runs)
                            "max_bytes_billed": {"type": "number"},     # BigQuery only
                            "max_slot_ms": {"type": "number"},          # BigQuery only
                            "max_peak_memory": {"type": "number"},      # Impala only
                            "max_cpu_ms": {"type": "number"},           # Impala only
                        },
                        "additionalProperties": False,
                    },
                    # compare (A vs B): each side is a query on source|target (default target)
                    "a": {"$ref": "#/$defs/side"},
                    "b": {"$ref": "#/$defs/side"},
                    # metric -> condition, e.g. "b <= a", "b <= a * 0.5", or "report"
                    "compare": {"type": "object", "minProperties": 1,
                                "additionalProperties": {"type": "string"}},
                    # regression: compare current metrics to a stored baseline file,
                    # metric -> allowed drift ("0%" = must not grow, "25%", or abs number)
                    "baseline": {"type": "string"},
                    "tolerances": {"type": "object", "minProperties": 1,
                                   "additionalProperties": {"type": ["string", "number"]}},
                },
                # each mode requires its own keys (loud spec on mismatch).
                "allOf": [
                    {"if": {"properties": {"mode": {"const": "assert"}}},
                     "then": {"required": ["sql", "thresholds"]}},
                    {"if": {"properties": {"mode": {"const": "measure"}}},
                     "then": {"required": ["sql"], "not": {"required": ["thresholds"]}}},
                    {"if": {"properties": {"mode": {"const": "compare"}}},
                     "then": {"required": ["a", "b", "compare"]}},
                    {"if": {"properties": {"mode": {"const": "regression"}}},
                     "then": {"required": ["sql", "baseline", "tolerances"]}},
                ],
                "additionalProperties": False,
            },
        },
    },
    "$defs": {
        "side": {
            "type": "object",
            "required": ["sql"],
            "properties": {
                "sql": {"type": "string"},
                "engine": {"enum": ["source", "target"]},   # default target
                "dry_run": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def _aggregate(runs: list[dict]) -> dict:
    """Engine-agnostic: fold every numeric metric across runs into {p50,p95,max,min}.
    Deterministic metrics (bytes) collapse to a single value; time/memory get percentiles."""
    first = runs[0]
    if first.get("dry_run"):
        return {"dry_run": True, "runs": len(runs), "bytes_scanned": first.get("bytes_scanned", 0)}
    agg = {"dry_run": False, "runs": len(runs)}
    for k, v in first.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        vals = [r[k] for r in runs if isinstance(r.get(k), (int, float))]
        agg[k] = {"p50": _pct(vals, 50), "p95": _pct(vals, 95), "max": max(vals), "min": min(vals)}
    return agg


def _metric_val(m: dict, metric: str):
    """The comparable value of a metric: p95 over runs (or the scalar for dry-run bytes)."""
    if m.get("dry_run"):
        return m.get("bytes_scanned") if metric == "bytes_scanned" else None
    cell = m.get(metric)
    return cell.get("p95") if isinstance(cell, dict) else None


def _resolve(metric_key: str, m: dict):
    return _metric_val(m, metric_key[len("max_"):])


_CMP_RE = re.compile(r"^\s*b\s*(<=|<|>=|>)\s*a(?:\s*\*\s*([\d.]+))?\s*$")
_CMP_OPS = {"<=": lambda x, y: x <= y, "<": lambda x, y: x < y,
            ">=": lambda x, y: x >= y, ">": lambda x, y: x > y}


def _eval_compare(cond: str, a_val: float, b_val: float):
    """Evaluate a B-vs-A condition. Returns (ok_or_None, message); ok=None means
    'report' (record the ratio, no gate). Conditions: 'report', 'b <= a',
    'b <= a * 0.5' (>=2x faster), 'b < a * 1.25', etc."""
    ratio = (b_val / a_val) if a_val else float("inf")
    if cond.strip() == "report":
        return None, f"a={a_val} b={b_val} (b/a={ratio:.2f}x)"
    m = _CMP_RE.match(cond)
    if not m:
        raise RuntimeError(f"bad compare condition {cond!r} (use 'report' or 'b <= a [* factor]')")
    op, factor = m.group(1), float(m.group(2) or 1.0)
    limit = a_val * factor
    ok = _CMP_OPS[op](b_val, limit)
    return ok, f"b={b_val} {op} a*{factor}={limit} (a={a_val}, b/a={ratio:.2f}x)"


def _run_side(ctx: Context, spec: dict, runs: int):
    eng = ctx.source if spec.get("engine") == "source" else ctx.target
    dry = spec.get("dry_run", False)
    n = 1 if dry else runs
    return _aggregate([eng.query_stats(spec["sql"], dry_run=dry) for _ in range(n)]), eng.name


def _baseline_lookup(path: str, query_id: str, current: dict) -> dict | None:
    """Return the stored baseline for query_id, or establish it (write current) and
    return None on first sight — the measure->baseline->gate workflow in one mode."""
    p = Path(path)
    data = json.loads(p.read_text()) if p.exists() else {}
    if query_id in data:
        return data[query_id]
    data[query_id] = {k: v for k, v in current.items() if v is not None}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True))
    return None


def _fmt(m: dict) -> str:
    if m.get("dry_run"):
        return f"[dry-run] bytes_scanned={m['bytes_scanned']}"
    parts = [f"runs={m['runs']}"]
    for k in ("bytes_scanned", "slot_ms", "cpu_ms", "peak_memory", "elapsed_ms"):
        if k in m:
            parts.append(f"{k}(p95)={m[k]['p95']}")
    return " ".join(parts)


@register("query_performance", SCHEMA)
def run(suite: dict, ctx: Context) -> SuiteResult:
    sr = SuiteResult(pattern="query_performance", suite_id=suite.get("id", "query_performance"))
    story = suite.get("story_id")

    for q in suite["queries"]:
        mode, runs = q["mode"], q.get("runs", 1)

        # compare (A vs B): run both sides, gate/report relative conditions per metric.
        if mode == "compare":
            am, an = _run_side(ctx, q["a"], runs)
            bm, bn = _run_side(ctx, q["b"], runs)
            for metric, cond in q["compare"].items():
                av, bv = _metric_val(am, metric), _metric_val(bm, metric)
                tgt = f"{q['id']}:{metric}"
                if av is None or bv is None:
                    sr.add(CheckResult(pattern="query_performance", target=tgt, status=Status.FAIL,
                        story_id=story, message=f"{metric} unavailable (a={an}, b={bn})"))
                    continue
                ok, msg = _eval_compare(cond, av, bv)
                sr.add(CheckResult(pattern="query_performance", target=tgt,
                    status=Status.PASS if (ok is None or ok) else Status.FAIL,
                    measurement=(ok is None), story_id=story,
                    message=f"{an}->{bn} {metric}: {msg}", metrics={"a": am, "b": bm}))
            continue

        # measure / assert: single query on the target engine.
        dry = q.get("dry_run", False)
        eng = ctx.target          # BigQuery (Job API) or Impala (runtime profile)
        n = 1 if dry else runs
        m = _aggregate([eng.query_stats(q["sql"], dry_run=dry, use_cache=q.get("use_cache", False),
                                        max_bytes_billed=q.get("max_bytes_billed")) for _ in range(n)])

        if mode == "measure":
            sr.add(CheckResult(pattern="query_performance", target=q["id"], status=Status.PASS,
                               story_id=story, measurement=True,
                               message=f"MEASURE {_fmt(m)}", metrics=m))
            continue

        # regression: compare current metrics to a stored baseline within tolerances.
        # First sight establishes the baseline (PASS); later runs gate on drift.
        if mode == "regression":
            from .parity import parse_tolerance
            cur = {mk: _metric_val(m, mk) for mk in q["tolerances"]}
            base = _baseline_lookup(q["baseline"], q["id"], cur)
            if base is None:
                sr.add(CheckResult(pattern="query_performance", target=q["id"], status=Status.PASS,
                    story_id=story, measurement=True,
                    message=f"baseline established: {cur}", metrics=m))
                continue
            breaches = []
            for mk, tol in q["tolerances"].items():
                c, b = cur.get(mk), base.get(mk)
                if c is None or b is None:
                    breaches.append(f"{mk}: unavailable (cur={c}, baseline={b})")
                    continue
                allowed = b + parse_tolerance(tol, base=b)
                if c > allowed:
                    breaches.append(f"{mk}: {c} > baseline {b} +{tol} ({allowed:g})")
            sr.add(CheckResult(pattern="query_performance", target=q["id"],
                status=Status.PASS if not breaches else Status.FAIL, story_id=story,
                message=(f"within baseline — {_fmt(m)}" if not breaches else "; ".join(breaches)),
                metrics=m))
            continue

        # assert: every threshold must hold (<=). A threshold on an unavailable metric
        # (e.g. elapsed under dry_run) FAILS loudly rather than silently passing.
        breaches = []
        for key, limit in q["thresholds"].items():
            val = _resolve(key, m)
            if val is None:
                breaches.append(f"{key}: metric unavailable (dry_run?)")
            elif val > limit:
                breaches.append(f"{key}: {val} > {limit}")
        sr.add(CheckResult(pattern="query_performance", target=q["id"],
            status=Status.PASS if not breaches else Status.FAIL, story_id=story,
            message=(f"within budget — {_fmt(m)}" if not breaches else "; ".join(breaches)),
            metrics=m))
    return sr
