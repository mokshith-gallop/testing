"""transform_unit — the ELT transform UNIT test (the cheap, numerous tier).

given -> T -> expect, entirely in BigQuery: seed tiny known inputs into a clean build
dataset, run the CUT's transform SQL VERBATIM (refs redirect via default-dataset), read
the output back, and compare to author-declared truth (canonicalized) or property
assertions. Hermetic, seconds, ~$0 — catches transform bugs earliest and most precisely.

  expect.rows  -> exact, order-independent, canonicalized multiset equality
  expect.assert -> properties: {rowcount}, {unique: [...]}, {no_nulls: [...]}

Correctness vs regression: author `expect` independently (reason about what T SHOULD
produce) for a correctness test; a captured snapshot is a regression test. For proving
a rewrite equivalent to the legacy T, use transform_diff (the legacy T is the oracle).

mutates=True: seeds + builds, so it needs a throwaway slate -> blocked in read_only.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

from . import build
from . import canonicalize as C
from .harness import Context, require
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
    "required": ["pattern", "given", "expect"],
    "properties": {
        "pattern": {"const": "transform_unit"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "build_dataset": {"type": "string"},
        "given": {"type": "object", "minProperties": 1, "additionalProperties": _GIVEN_TABLE},
        # The CUT's transform: canonical `sql` (path) / `sql_text` (inline); `transform`
        # / `transform_sql` are accepted aliases (same path/inline meaning).
        "sql": {"type": "string"}, "sql_text": {"type": "string"},
        "transform": {"type": "string"}, "transform_sql": {"type": "string"},
        "expect": {
            "type": "object", "required": ["table"],
            "properties": {
                "table": {"type": "string"},
                "rows": {"type": "array", "items": {"type": "object"}},
                "assert": {"type": "array", "items": {"type": "object"}},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


# ISO date/timestamp detection so an author-declared "2026-06-01T00:00:00Z" canonicalizes
# the same way the actual TIMESTAMP value does (else the string vs datetime forms differ).
def _coerce_expected(v):
    if not isinstance(v, str):
        return v
    s = v.strip()
    try:
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return dt.date.fromisoformat(s)
        if "T" in s or (":" in s and "-" in s):
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return v
    return v


def _canon(row: dict, cols) -> str:
    norm = {c: _coerce_expected(row.get(c)) for c in cols}
    return C.canon_row(norm, cols, coerce_numeric_strings=True)


def run(suite: dict, ctx: Context) -> SuiteResult:
    sr = SuiteResult(pattern="transform_unit", suite_id=suite.get("id") or "transform_unit")

    def chk(target, status, message="", **kw):
        return sr.add(CheckResult(pattern="transform_unit", target=target, status=status,
                                  message=message, story_id=suite.get("story_id"), **kw))

    def fail(message):
        chk(suite.get("id") or "transform_unit", Status.ERROR, message)
        return sr

    sql = build.sql_from(suite, "sql", "sql_text") or build.sql_from(suite, "transform", "transform_sql")
    if sql is None:
        return fail("transform_unit needs 'sql' (path) or 'sql_text' (inline)")

    require(ctx, "bigquery")
    bq = ctx.target
    build_ds = suite.get("build_dataset", f"{build.BUILD_DS_DEFAULT}_unit")

    try:
        build.provision_build_dataset(bq, build_ds)
        build.seed_given(bq, build_ds, suite["given"])
        build.run_sql(bq, build_ds, sql)
    except Exception as e:  # noqa: BLE001 — seeding/applying failure -> ERROR
        return fail(f"{type(e).__name__}: {e}")

    expect = suite["expect"]
    out_table = expect["table"]
    fq = bq.qualify(build_ds, out_table)
    try:
        if "rows" in expect:
            _check_rows(bq, fq, out_table, expect["rows"], chk)
        for a in expect.get("assert", []):
            _check_assert(bq, fq, out_table, a, chk)
    except Exception as e:  # noqa: BLE001
        return fail(f"read-back failed: {type(e).__name__}: {e}")
    return sr


def _check_rows(bq, fq, out_table, expected_rows, chk) -> None:
    cols = sorted({k for r in expected_rows for k in r})
    actual = bq.query(f"SELECT * FROM {fq}")
    exp_ms = Counter(_canon(r, cols) for r in expected_rows)
    act_ms = Counter(_canon(r, cols) for r in actual)
    if exp_ms == act_ms:
        chk(out_table, Status.PASS, f"{len(actual)} rows match expected (cols: {', '.join(cols)})",
            metrics={"rows": len(actual)})
        return
    missing = list((exp_ms - act_ms).elements())
    extra = list((act_ms - exp_ms).elements())
    chk(out_table, Status.FAIL,
        f"output mismatch: {len(missing)} missing, {len(extra)} unexpected "
        f"(expected {sum(exp_ms.values())}, got {sum(act_ms.values())} rows)",
        expected=missing[:5], actual=extra[:5])


def _check_assert(bq, fq, out_table, a, chk) -> None:
    if "rowcount" in a:
        n = bq.query(f"SELECT COUNT(*) AS n FROM {fq}")[0]["n"]
        st = Status.PASS if n == a["rowcount"] else Status.FAIL
        chk(f"{out_table}.rowcount", st, f"rowcount={n} (expected {a['rowcount']})",
            expected=a["rowcount"], actual=n)
    elif "unique" in a:
        keys = ", ".join(a["unique"])
        r = bq.query(f"SELECT COUNT(*) AS n, COUNT(DISTINCT TO_JSON_STRING(STRUCT({keys}))) AS d FROM {fq}")[0]
        st = Status.PASS if r["n"] == r["d"] else Status.FAIL
        chk(f"{out_table}.unique({keys})", st,
            f"{r['n']} rows, {r['d']} distinct on ({keys})" + ("" if st == Status.PASS else " — duplicates"))
    elif "no_nulls" in a:
        for col in a["no_nulls"]:
            n = bq.query(f"SELECT COUNTIF({col} IS NULL) AS n FROM {fq}")[0]["n"]
            st = Status.PASS if n == 0 else Status.FAIL
            chk(f"{out_table}.{col} no_nulls", st, f"{n} NULLs" + ("" if st == Status.PASS else " — expected 0"))
    else:
        chk(out_table, Status.ERROR, f"unknown assert: {sorted(a)} (use rowcount/unique/no_nulls)")


register("transform_unit", SCHEMA, mutates=True)(run)
