"""Axis-B encoding correctness (P0-3) — the silent-corruption killers.

  5  epoch_conversion   — legacy epoch int -> target TIMESTAMP, per row; lying-column
                          year guard; out-of-range -> NULL (+ optional _dq_audit row)
  6  decimal_roundtrip  — DECIMAL/NUMERIC values match exactly across engines

The agent declares column->encoding and expected year range; the harness owns the
conversion math + canonical comparison.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from . import canonicalize as C
from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

UTC = dt.timezone.utc
_DIVISOR = {"seconds": 1, "millis": 1000, "micros": 1_000_000}


def epoch_to_instant(raw: int, encoding: str) -> dt.datetime | None:
    """Convert a legacy epoch int to a UTC instant, or None if out of representable range."""
    try:
        return dt.datetime.fromtimestamp(int(raw) / _DIVISOR[encoding], tz=UTC)
    except (OverflowError, OSError, ValueError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------------------
# Pattern 5 — epoch / timestamp conversion
# ---------------------------------------------------------------------------

EPOCH_SCHEMA = {
    "type": "object",
    "required": ["pattern", "source_database", "target_dataset", "columns"],
    "properties": {
        "pattern": {"const": "epoch_conversion"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "source_database": {"type": "string"},
        "target_dataset": {"type": "string"},
        "columns": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["source_table", "source_column", "target_table", "target_column",
                             "key", "source_encoding"],
                "properties": {
                    "source_table": {"type": "string"}, "source_column": {"type": "string"},
                    "target_table": {"type": "string"}, "target_column": {"type": "string"},
                    "key": {"type": "string"},
                    "source_encoding": {"enum": ["seconds", "millis", "micros"]},
                    "expect_year_between": {"type": "array", "items": {"type": "integer"},
                                            "minItems": 2, "maxItems": 2},
                    "sane_year_range": {"type": "array", "items": {"type": "integer"},
                                        "minItems": 2, "maxItems": 2},
                    "out_of_range_is_null": {"type": "boolean"},
                    "audit_table": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


@register("epoch_conversion", EPOCH_SCHEMA)
def run_epoch(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "impala", "bigquery")
    sr = SuiteResult(pattern="epoch_conversion", suite_id=suite.get("id", "epoch_conversion"))
    story = suite.get("story_id")
    src_db = suite["source_database"]
    ds = suite["target_dataset"]

    for col in suite["columns"]:
        enc = col["source_encoding"]
        key = col["key"]
        lo, hi = col.get("expect_year_between", [1970, 9999])
        sane_lo, sane_hi = col.get("sane_year_range", [1970, 2100])
        oor_null = col.get("out_of_range_is_null", True)
        prefix = f"{ds}.{col['target_table']}.{col['target_column']}"

        src_rows = {r[key]: r[col["source_column"]] for r in ctx.source.query(
            f"SELECT {key}, {col['source_column']} FROM {src_db}.{col['source_table']}")}
        tgt_rows = {r[key]: r[col["target_column"]] for r in ctx.target.query(
            f"SELECT {key}, {col['target_column']} FROM {ctx.target.qualify(ds, col['target_table'])}")}

        matched = mismatched = nulled = 0
        for k, raw in src_rows.items():
            tgt = tgt_rows.get(k)
            inst = epoch_to_instant(raw, enc)
            in_sane = inst is not None and sane_lo <= inst.year <= sane_hi
            if not in_sane:
                # Out-of-range: target must be NULL (corruption quarantined, not errored).
                nulled += 1
                if oor_null and tgt is not None:
                    mismatched += 1
                    sr.add(CheckResult(pattern="epoch_conversion", target=f"{prefix}[{k}]",
                        status=Status.FAIL, story_id=story,
                        message=f"out-of-range epoch {raw} should be NULL, got {tgt!r}"))
                continue
            # In-range: lying-column guard + value match (to the second).
            if not (lo <= inst.year <= hi):
                mismatched += 1
                sr.add(CheckResult(pattern="epoch_conversion", target=f"{prefix}[{k}]",
                    status=Status.FAIL, story_id=story,
                    message=f"lying-column guard: year {inst.year} outside [{lo},{hi}] "
                            f"(raw {raw} as {enc})", expected=f"{lo}-{hi}", actual=inst.year))
                continue
            exp = C._norm_datetime(inst)
            got = C._norm_datetime(tgt) if isinstance(tgt, dt.datetime) else None
            if exp == got:
                matched += 1
            else:
                mismatched += 1
                sr.add(CheckResult(pattern="epoch_conversion", target=f"{prefix}[{k}]",
                    status=Status.FAIL, story_id=story,
                    message=f"converted instant mismatch: expected {exp}, got {got}",
                    expected=exp, actual=got))

        # Optional: assert a _dq_audit row exists for each quarantined epoch.
        if col.get("audit_table") and nulled:
            n_audit = int(ctx.target.scalar(
                f"SELECT COUNT(*) AS n FROM {ctx.target.qualify(ds, col['audit_table'])} "
                f"WHERE src_table = '{col['source_table']}'"))
            sr.add(CheckResult(pattern="epoch_conversion", target=f"{prefix} (_dq_audit)",
                status=Status.PASS if n_audit >= nulled else Status.FAIL, story_id=story,
                message=f"{n_audit} audit rows for {nulled} quarantined epochs",
                expected=f">={nulled}", actual=n_audit))

        sr.add(CheckResult(pattern="epoch_conversion", target=prefix,
            status=Status.PASS if mismatched == 0 else Status.FAIL, story_id=story,
            message=f"{matched} matched, {nulled} nulled(out-of-range), {mismatched} bad",
            metrics={"matched": matched, "nulled": nulled, "mismatched": mismatched,
                     "encoding": enc}))
    return sr


# ---------------------------------------------------------------------------
# Pattern 6 — DECIMAL precision roundtrip
# ---------------------------------------------------------------------------

DECIMAL_SCHEMA = {
    "type": "object",
    "required": ["pattern", "source_database", "target_dataset", "columns"],
    "properties": {
        "pattern": {"const": "decimal_roundtrip"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "source_database": {"type": "string"},
        "target_dataset": {"type": "string"},
        "columns": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["source_table", "target_table", "key", "columns"],
                "properties": {
                    "source_table": {"type": "string"}, "target_table": {"type": "string"},
                    "target_dataset": {"type": "string"},
                    "key": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _as_decimal(v):
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


@register("decimal_roundtrip", DECIMAL_SCHEMA)
def run_decimal(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "impala", "bigquery")
    sr = SuiteResult(pattern="decimal_roundtrip", suite_id=suite.get("id", "decimal_roundtrip"))
    story = suite.get("story_id")
    src_db = suite["source_database"]
    default_ds = suite["target_dataset"]

    for spec in suite["columns"]:
        key = spec["key"]
        cols = spec["columns"]
        ds = spec.get("target_dataset", default_ds)
        sel = ", ".join([key] + cols)
        src_rows = {r[key]: r for r in ctx.source.query(
            f"SELECT {sel} FROM {src_db}.{spec['source_table']}")}
        tgt_rows = {r[key]: r for r in ctx.target.query(
            f"SELECT {sel} FROM {ctx.target.qualify(ds, spec['target_table'])}")}
        prefix = f"{ds}.{spec['target_table']}"

        bad = 0
        for k, srow in src_rows.items():
            trow = tgt_rows.get(k)
            for c in cols:
                sv = _as_decimal(srow.get(c)) if srow else None
                tv = _as_decimal(trow.get(c)) if trow else None
                # Numeric equality (Decimal('1.50') == Decimal('1.5')); scale is a schema check.
                if sv != tv:
                    bad += 1
                    sr.add(CheckResult(pattern="decimal_roundtrip",
                        target=f"{prefix}.{c}[{k}]", status=Status.FAIL, story_id=story,
                        message=f"decimal mismatch: src={sv} tgt={tv}",
                        expected=str(sv), actual=str(tv)))
        for c in cols:
            sr.add(CheckResult(pattern="decimal_roundtrip", target=f"{prefix}.{c}",
                status=Status.PASS if bad == 0 else Status.FAIL, story_id=story,
                message=f"{len(src_rows)} rows roundtripped" if bad == 0 else f"{bad} mismatches"))
    return sr
