"""transform_diff — cross-engine equivalence of a rewritten transform (the killer
migration test).

Feed the SAME controlled `given` to BOTH the legacy transform (HiveQL on the source
engine) and the migrated transform (SQL on BigQuery), then assert the two output row
sets are identical (canonicalized). The legacy transform is the INDEPENDENT ORACLE —
the author never hand-writes an `expect`, so the CUT can supply input freely and still
can't fabricate the answer. Proves the rewrite is behaviorally equivalent on controlled
input — stronger than production parity (no input drift to confound it).

Both transforms must be SELECTs (the pattern compares result sets). Unqualified refs
resolve via session default DB (source) / default dataset (BigQuery).

mutates=True: seeds both engines -> blocked in read_only.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import build
from . import canonicalize as C
from .harness import Context, require
from .mvs import expand_env
from .registry import register
from .report import CheckResult, Status, SuiteResult

_GIVEN_TABLE = {
    "type": "object", "required": ["columns", "rows"],
    "properties": {
        "columns": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object", "required": ["name", "type"],
                "properties": {"name": {"type": "string"}, "type": {"type": "string"},
                               "scale": {"type": "integer"}, "mode": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "rows": {"type": "array", "items": {"type": "object"}},
    },
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "required": ["pattern", "given"],
    "properties": {
        "pattern": {"const": "transform_diff"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "source_database": {"type": "string"},   # HS2 db to seed/run the legacy T in
        "build_dataset": {"type": "string"},       # BQ ds to seed/run the migrated T in
        "given": {"type": "object", "minProperties": 1, "additionalProperties": _GIVEN_TABLE},
        "legacy_transform": {"type": "string"}, "legacy_sql": {"type": "string"},
        "migrated_transform": {"type": "string"}, "migrated_sql": {"type": "string"},
    },
    "additionalProperties": False,
}


def _sql(suite: dict, role: str) -> str:
    if suite.get(f"{role}_sql"):
        return expand_env(suite[f"{role}_sql"])
    if suite.get(f"{role}_transform"):
        return expand_env(Path(suite[f"{role}_transform"]).read_text())
    raise ValueError(f"transform_diff needs '{role}_sql' (inline) or '{role}_transform' (path)")


def _norm(rows):
    """Lower-case keys (Impala lowercases column names; BigQuery preserves case)."""
    return [{k.lower(): v for k, v in r.items()} for r in rows]


def run(suite: dict, ctx: Context) -> SuiteResult:
    sr = SuiteResult(pattern="transform_diff", suite_id=suite.get("id") or "transform_diff")

    def chk(target, status, message="", **kw):
        return sr.add(CheckResult(pattern="transform_diff", target=target, status=status,
                                  message=message, story_id=suite.get("story_id"), **kw))

    require(ctx, ctx.source_kind, "bigquery")
    bq = ctx.target
    src_db = suite.get("source_database", "dmt_diff_src")
    bq_ds = suite.get("build_dataset", f"{build.BUILD_DS_DEFAULT}_diff")

    try:
        legacy_sql = _sql(suite, "legacy")
        migrated_sql = _sql(suite, "migrated")
        # seed the SAME given into both engines
        build.provision_build_dataset(bq, bq_ds)
        build.seed_given(bq, bq_ds, suite["given"])
        ctx.hive.execute(f"CREATE DATABASE IF NOT EXISTS {src_db}")
        build.seed_given(ctx.hive, src_db, suite["given"])
        ctx.source.execute("INVALIDATE METADATA")    # make Hive-written tables visible to Impala
        # run both transforms (legacy = oracle)
        legacy_rows = _norm(ctx.source.run_in(src_db, legacy_sql))
        migrated_rows = _norm(build.query_sql(bq, bq_ds, migrated_sql))
    except Exception as e:  # noqa: BLE001
        chk(suite.get("id") or "transform_diff", Status.ERROR, f"{type(e).__name__}: {e}")
        return sr

    cols = sorted({k for r in legacy_rows for k in r})
    lc = Counter(C.canon_row(r, cols, coerce_numeric_strings=True) for r in legacy_rows)
    mc = Counter(C.canon_row(r, cols, coerce_numeric_strings=True) for r in migrated_rows)
    target = suite.get("id") or "transform_diff"
    if lc == mc:
        chk(target, Status.PASS,
            f"legacy == migrated: {sum(lc.values())} rows identical (cols: {', '.join(cols)})",
            metrics={"rows": sum(lc.values())})
    else:
        only_legacy = list((lc - mc).elements())
        only_migrated = list((mc - lc).elements())
        chk(target, Status.FAIL,
            f"rewrite NOT equivalent: {len(only_legacy)} rows only in legacy, "
            f"{len(only_migrated)} only in migrated "
            f"(legacy {sum(lc.values())} vs migrated {sum(mc.values())} rows)",
            expected=only_legacy[:5], actual=only_migrated[:5])
    return sr


register("transform_diff", SCHEMA, mutates=True)(run)
