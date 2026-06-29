"""Axis-A parity family. Patterns:
  1  rowcount_parity      — per-table COUNT(*) legacy(Impala) vs BigQuery (P0-2)
  2  aggregate_parity     — SUM/COUNT-DISTINCT/MIN-MAX per column (P0-4)
  3  fingerprint_parity   — order-independent full-row digest (P0-4)
  4  query_parity         — legacy SQL vs converted SQL on the same seed (P0-6)

Tolerance is declared in the MVS; the harness routes/compares. The agent never
writes the comparison, the hashing, or the dialect SQL — those live here.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from . import build
from . import canonicalize as C
from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult


# ---------------------------------------------------------------------------
# Tolerance
# ---------------------------------------------------------------------------

def parse_tolerance(spec, base: int | float | None = None) -> float:
    """Absolute allowed delta from a tolerance spec.

    "0"/0 -> exact; "5" -> 5 absolute; "0.1%" -> 0.1% of `base` (the source value).
    """
    if spec is None:
        return 0.0
    if isinstance(spec, (int, float)):
        return float(spec)
    s = str(spec).strip()
    if s.endswith("%"):
        return abs(base or 0) * (float(s[:-1]) / 100.0)
    return float(s)


# ---------------------------------------------------------------------------
# Pattern 1 — row-count parity
# ---------------------------------------------------------------------------

ROWCOUNT_SCHEMA = {
    "type": "object",
    "required": ["pattern", "source_database", "target_dataset", "tables"],
    "properties": {
        "pattern": {"const": "rowcount_parity"},
        "id": {"type": "string"},
        "story_id": {"type": "string"},
        "source_database": {"type": "string"},
        "target_dataset": {"type": "string"},
        "tables": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["source", "target"],
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "target_dataset": {"type": "string"},
                    "tolerance": {"type": ["string", "number"]},
                    "source_filter": {"type": "string"},
                    "target_filter": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


@register("rowcount_parity", ROWCOUNT_SCHEMA)
def run_rowcount(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, ctx.source_kind, ctx.target_kind)
    sr = SuiteResult(pattern="rowcount_parity", suite_id=suite.get("id", "rowcount_parity"))
    story = suite.get("story_id")
    src_db = suite["source_database"]
    default_ds = suite["target_dataset"]

    for t in suite["tables"]:
        src_tbl, tgt_tbl = t["source"], t["target"]
        tgt_ds = t.get("target_dataset", default_ds)
        target = f"{tgt_ds}.{tgt_tbl}"
        src_where = f" WHERE {t['source_filter']}" if t.get("source_filter") else ""
        tgt_where = f" WHERE {t['target_filter']}" if t.get("target_filter") else ""

        src_n = int(ctx.source.scalar(
            f"SELECT COUNT(*) AS n FROM {ctx.source.qualify(src_db, src_tbl)}{src_where}"))
        tgt_n = int(ctx.target.scalar(
            f"SELECT COUNT(*) AS n FROM {ctx.target.qualify(tgt_ds, tgt_tbl)}{tgt_where}"))
        delta = abs(src_n - tgt_n)
        allowed = parse_tolerance(t.get("tolerance", "0"), base=src_n)
        status = Status.PASS if delta <= allowed else Status.FAIL
        sr.add(CheckResult(
            pattern="rowcount_parity", target=target, status=status, story_id=story,
            message=f"src={src_n} tgt={tgt_n} delta={delta} (allowed {allowed:g})",
            expected=src_n, actual=tgt_n,
            metrics={"source_count": src_n, "target_count": tgt_n, "delta": delta, "allowed": allowed},
        ))
    return sr


# ---------------------------------------------------------------------------
# Pattern 2 — aggregate-checksum parity
# ---------------------------------------------------------------------------

_AGG_FNS = ["sum", "count_distinct", "min", "max", "count"]

AGGREGATE_SCHEMA = {
    "type": "object",
    "required": ["pattern", "source_database", "target_dataset", "tables"],
    "properties": {
        "pattern": {"const": "aggregate_parity"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "source_database": {"type": "string"}, "target_dataset": {"type": "string"},
        "tables": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["source", "target", "aggregates"],
                "properties": {
                    "source": {"type": "string"}, "target": {"type": "string"},
                    "target_dataset": {"type": "string"},
                    "aggregates": {
                        "type": "array", "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["column", "fn"],
                            "properties": {
                                "column": {"type": "string"},
                                "fn": {"enum": _AGG_FNS},
                                "tolerance": {"type": ["string", "number"]},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _agg_sql(fn: str, col: str) -> str:
    if fn == "count_distinct":
        return f"COUNT(DISTINCT {col})"
    if fn == "count":
        return f"COUNT({col})"
    return f"{fn.upper()}({col})"


def _to_num(v):
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _compare_agg(fn, col, sval, tval, tol_spec):
    """Return (status, message) for one aggregate comparison."""
    if fn in ("min", "max") and isinstance(sval, dt.datetime):
        s = C._norm_datetime(sval) if isinstance(sval, dt.datetime) else None
        t = C._norm_datetime(tval) if isinstance(tval, dt.datetime) else None
        tol = parse_tolerance(tol_spec if tol_spec is not None else "0")  # exact by default; set tolerance for ±Ns
        if s is None or t is None:
            ok = (s == t)
            delta = 0
        else:
            d1 = dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            d2 = dt.datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
            delta = abs((d1 - d2).total_seconds())
            ok = delta <= tol
        return (Status.PASS if ok else Status.FAIL,
                f"{fn}({col}) src={s} tgt={t} Δ={delta}s (±{tol}s)")
    # numeric / count
    sn, tnv = _to_num(sval), _to_num(tval)
    if sn is None or tnv is None:
        ok = (sval == tval)
        return (Status.PASS if ok else Status.FAIL, f"{fn}({col}) src={sval!r} tgt={tval!r}")
    base = abs(sn)
    tol = Decimal(str(parse_tolerance(tol_spec if tol_spec is not None else "0", base=float(base))))
    delta = abs(sn - tnv)
    ok = delta <= tol
    return (Status.PASS if ok else Status.FAIL,
            f"{fn}({col}) src={sn} tgt={tnv} Δ={delta} (allowed {tol})")


@register("aggregate_parity", AGGREGATE_SCHEMA)
def run_aggregate(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, ctx.source_kind, ctx.target_kind)
    sr = SuiteResult(pattern="aggregate_parity", suite_id=suite.get("id", "aggregate_parity"))
    story = suite.get("story_id")
    src_db = suite["source_database"]
    default_ds = suite["target_dataset"]

    for t in suite["tables"]:
        ds = t.get("target_dataset", default_ds)
        prefix = f"{ds}.{t['target']}"
        sexprs = [f"{_agg_sql(a['fn'], a['column'])} AS a{i}" for i, a in enumerate(t["aggregates"])]
        srow = ctx.source.query(
            f"SELECT {', '.join(sexprs)} FROM {ctx.source.qualify(src_db, t['source'])}")[0]
        trow = ctx.target.query(
            f"SELECT {', '.join(sexprs)} FROM {ctx.target.qualify(ds, t['target'])}")[0]
        skeys, tkeys = list(srow.keys()), list(trow.keys())
        for i, a in enumerate(t["aggregates"]):
            sval, tval = srow[skeys[i]], trow[tkeys[i]]
            status, msg = _compare_agg(a["fn"], a["column"], sval, tval, a.get("tolerance"))
            sr.add(CheckResult(pattern="aggregate_parity", target=f"{prefix}.{a['column']}:{a['fn']}",
                               status=status, story_id=story, message=msg))
    return sr


# ---------------------------------------------------------------------------
# Pattern 3 — order-independent full-row fingerprint parity
# ---------------------------------------------------------------------------

FINGERPRINT_SCHEMA = {
    "type": "object",
    "required": ["pattern", "source_database", "target_dataset", "tables"],
    "properties": {
        "pattern": {"const": "fingerprint_parity"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "source_database": {"type": "string"}, "target_dataset": {"type": "string"},
        "tables": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["source", "target", "columns"],
                "properties": {
                    "source": {"type": "string"}, "target": {"type": "string"},
                    "target_dataset": {"type": "string"},
                    "key": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "float_decimals": {"type": "integer"},
                    "coerce_numeric_strings": {"type": "boolean"},
                    # "pushdown" computes the digest IN the warehouse (scale path, no row
                    # egress); requires same-engine source+target. "python" (default)
                    # pulls rows + canonicalizes (cross-engine, small/medium tables).
                    "scale": {"enum": ["python", "pushdown"]},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


@register("fingerprint_parity", FINGERPRINT_SCHEMA)
def run_fingerprint(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, ctx.source_kind, ctx.target_kind)
    sr = SuiteResult(pattern="fingerprint_parity", suite_id=suite.get("id", "fingerprint_parity"))
    story = suite.get("story_id")
    src_db = suite["source_database"]
    default_ds = suite["target_dataset"]

    for t in suite["tables"]:
        ds = t.get("target_dataset", default_ds)
        cols = t["columns"]
        prefix = f"{ds}.{t['target']}"

        # Scale path: digest computed in-warehouse, no row egress. Works cross-engine
        # (Impala<->BigQuery) because md5(canonical row) is byte-identical across both.
        if t.get("scale") == "pushdown":
            s = ctx.source.fingerprint_pushdown(src_db, t["source"], cols)
            d = ctx.target.fingerprint_pushdown(ds, t["target"], cols)
            ok = s == d
            sr.add(CheckResult(pattern="fingerprint_parity", target=f"{prefix} (pushdown)",
                status=Status.PASS if ok else Status.FAIL, story_id=story,
                message=(f"in-warehouse digest match (n={s['count']}, {ctx.source.name}->{ctx.target.name})" if ok
                         else f"digest MISMATCH src={s} tgt={d}"),
                expected=s, actual=d))
            # Smart-diff localization: on mismatch, narrow to differing buckets in-engine
            # (no egress), then drill only those for the exact differing keys.
            if not ok and t.get("key"):
                diffs = _localize_pushdown(ctx, t, src_db, ds, cols, t["key"])
                sr.add(CheckResult(pattern="fingerprint_parity", target=f"{prefix} (localized)",
                    status=Status.FAIL, story_id=story,
                    message=f"{len(diffs)} differing keys: {sorted(diffs)[:10]}",
                    metrics={"differing_keys": sorted(diffs)[:100]}))
            continue

        opts = {"float_decimals": t.get("float_decimals", C.DEFAULT_FLOAT_DECIMALS),
                "coerce_numeric_strings": t.get("coerce_numeric_strings", False)}
        sel = ", ".join(cols)
        srows = ctx.source.query(f"SELECT {sel} FROM {ctx.source.qualify(src_db, t['source'])}")
        trows = ctx.target.query(f"SELECT {sel} FROM {ctx.target.qualify(ds, t['target'])}")
        sfp = C.table_fingerprint(srows, cols, **opts)
        tfp = C.table_fingerprint(trows, cols, **opts)
        ok = sfp["digest"] == tfp["digest"]
        msg = (f"digest match (n={sfp['count']})" if ok
               else f"digest MISMATCH: src(n={sfp['count']},{sfp['digest'][:12]}) "
                    f"tgt(n={tfp['count']},{tfp['digest'][:12]})")
        sr.add(CheckResult(pattern="fingerprint_parity", target=prefix,
                           status=Status.PASS if ok else Status.FAIL, story_id=story,
                           message=msg, expected=sfp["digest"], actual=tfp["digest"],
                           metrics={"source": sfp, "target": tfp}))
        # Localize differing primary keys (reladiff-style) on mismatch.
        if not ok and t.get("key"):
            _localize(sr, ctx, t, src_db, ds, cols, opts, story)
    return sr


def _localize_pushdown(ctx, t, src_db, ds, cols, key, n_buckets: int = 256) -> list:
    """Smart-diff at scale: compare per-bucket (count,sum) digests folded in-engine,
    then drill ONLY the mismatching buckets to pull their (key->row md5) and return the
    exact differing keys. No full-table egress — only the suspect buckets move."""
    sb = ctx.source.bucket_digests(src_db, t["source"], cols, key, n_buckets)
    tb = ctx.target.bucket_digests(ds, t["target"], cols, key, n_buckets)
    bad_buckets = [b for b in set(sb) | set(tb) if sb.get(b) != tb.get(b)]
    diffs = []
    for b in bad_buckets:
        sk = ctx.source.bucket_keys(src_db, t["source"], cols, key, n_buckets, b)
        tk = ctx.target.bucket_keys(ds, t["target"], cols, key, n_buckets, b)
        diffs += [k for k in set(sk) | set(tk) if sk.get(k) != tk.get(k)]
    return diffs


def _localize(sr, ctx, t, src_db, ds, cols, opts, story):
    key = t["key"]
    srows = {r[key]: r for r in ctx.source.query(
        f"SELECT {', '.join(cols)} FROM {ctx.source.qualify(src_db, t['source'])}")}
    trows = {r[key]: r for r in ctx.target.query(
        f"SELECT {', '.join(cols)} FROM {ctx.target.qualify(ds, t['target'])}")}
    diffs = []
    for k in set(srows) | set(trows):
        sd = C.row_digest(srows[k], cols, **opts) if k in srows else None
        td = C.row_digest(trows[k], cols, **opts) if k in trows else None
        if sd != td:
            diffs.append(k)
    sr.add(CheckResult(pattern="fingerprint_parity", target=f"{ds}.{t['target']} (localized)",
                       status=Status.FAIL, story_id=story,
                       message=f"{len(diffs)} differing keys: {sorted(diffs)[:10]}",
                       metrics={"differing_keys": sorted(diffs)[:50]}))


# ---------------------------------------------------------------------------
# Pattern 4 — cross-engine query / view parity
# ---------------------------------------------------------------------------

QUERY_SCHEMA = {
    "type": "object",
    "required": ["pattern", "queries"],
    "properties": {
        "pattern": {"const": "query_parity"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "queries": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "source_sql": {"type": "string"},        # legacy query, inline
                    "target_sql": {"type": "string"},        # converted query, inline
                    "source_sql_path": {"type": "string"},   # OR path to a .sql file
                    "target_sql_path": {"type": "string"},
                    "mode": {"enum": ["rowset", "scalar"]},
                    "key": {"type": "array", "items": {"type": "string"}},
                    "tolerance": {"type": ["string", "number"]},
                    "float_decimals": {"type": "integer"},
                    "coerce_numeric_strings": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


@register("query_parity", QUERY_SCHEMA)
def run_query(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, ctx.source_kind, ctx.target_kind)
    sr = SuiteResult(pattern="query_parity", suite_id=suite.get("id", "query_parity"))
    story = suite.get("story_id")

    for q in suite["queries"]:
        mode = q.get("mode", "rowset")
        # SQL is inline (source_sql/target_sql) or a path (source_sql_path/target_sql_path).
        src_sql = build.sql_from(q, "source_sql_path", "source_sql")
        tgt_sql = build.sql_from(q, "target_sql_path", "target_sql")
        if src_sql is None or tgt_sql is None:
            sr.add(CheckResult(pattern="query_parity", target=q["id"], status=Status.ERROR,
                story_id=story, message="query needs source_sql/target_sql (inline) or "
                                        "source_sql_path/target_sql_path (file)"))
            continue
        srows = ctx.source.query(src_sql)
        trows = ctx.target.query(tgt_sql)

        if mode == "scalar":
            sval = next(iter(srows[0].values())) if srows else None
            tval = next(iter(trows[0].values())) if trows else None
            sn, tn = _to_num(sval), _to_num(tval)
            if sn is None or tn is None:
                ok = (sval == tval)
                msg = f"scalar src={sval!r} tgt={tval!r}"
            else:
                tol = Decimal(str(parse_tolerance(q.get("tolerance", "0"), base=float(abs(sn)))))
                ok = abs(sn - tn) <= tol
                msg = f"scalar src={sn} tgt={tn} Δ={abs(sn-tn)} (allowed {tol})"  # e.g. ±5% NDV
            sr.add(CheckResult(pattern="query_parity", target=q["id"],
                status=Status.PASS if ok else Status.FAIL, story_id=story, message=msg))
            continue

        # rowset: order-independent fingerprint over the union of result columns
        cols = sorted({k for r in srows for k in r} | {k for r in trows for k in r})
        opts = {"float_decimals": q.get("float_decimals", C.DEFAULT_FLOAT_DECIMALS),
                "coerce_numeric_strings": q.get("coerce_numeric_strings", False)}
        sfp = C.table_fingerprint(srows, cols, **opts)
        tfp = C.table_fingerprint(trows, cols, **opts)
        ok = sfp["digest"] == tfp["digest"]
        sr.add(CheckResult(pattern="query_parity", target=q["id"],
            status=Status.PASS if ok else Status.FAIL, story_id=story,
            message=(f"rowset match (n={sfp['count']})" if ok
                     else f"rowset MISMATCH src(n={sfp['count']}) tgt(n={tfp['count']})"),
            expected=sfp["digest"], actual=tfp["digest"]))
    return sr
