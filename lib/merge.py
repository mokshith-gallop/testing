"""Pattern 9 — MERGE / delta idempotency (Axis C, P0-5).

Run the migration's MERGE statement (an artifact it already produced — the declarative
escape hatch, SPEC §5.1) twice against the same delta; assert the target's row count
and per-column fingerprint are unchanged between the two runs (idempotent), and that
op='D' keys are absent afterward.
"""
from __future__ import annotations

from . import canonicalize as C
from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

SCHEMA = {
    "type": "object",
    "required": ["pattern", "target_dataset", "merges"],
    "properties": {
        "pattern": {"const": "merge_idempotency"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "target_dataset": {"type": "string"},
        "merges": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["target", "merge_sql", "key_columns", "compare_columns"],
                "properties": {
                    "target": {"type": "string"},
                    "merge_sql": {"type": "string"},      # the migration's MERGE (escape hatch)
                    "key_columns": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "compare_columns": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "deleted_keys_absent": {
                        "type": "object",
                        "properties": {
                            "delta_table": {"type": "string"},
                            "op_column": {"type": "string"},
                            "delete_value": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _snapshot(ctx, ds, table, cols):
    rows = ctx.target.query(f"SELECT {', '.join(cols)} FROM {ctx.target.qualify(ds, table)}")
    return C.table_fingerprint(rows, cols)


@register("merge_idempotency", SCHEMA, mutates=True)   # runs MERGE (mutates the target)
def run(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "bigquery")
    sr = SuiteResult(pattern="merge_idempotency", suite_id=suite.get("id", "merge_idempotency"))
    story = suite.get("story_id")
    ds = suite["target_dataset"]

    for m in suite["merges"]:
        table = m["target"]
        cols = m["compare_columns"]
        prefix = f"{ds}.{table}"

        # Run MERGE twice; compare the two post-states.
        ctx.target.query(m["merge_sql"])
        snap1 = _snapshot(ctx, ds, table, cols)
        ctx.target.query(m["merge_sql"])
        snap2 = _snapshot(ctx, ds, table, cols)

        idem = snap1["digest"] == snap2["digest"]
        sr.add(CheckResult(pattern="merge_idempotency", target=prefix,
            status=Status.PASS if idem else Status.FAIL, story_id=story,
            message=(f"idempotent: count {snap1['count']} stable across re-run" if idem
                     else f"NOT idempotent: run1(n={snap1['count']}) != run2(n={snap2['count']})"),
            expected=snap1["digest"], actual=snap2["digest"]))

        # op='D' keys absent from target after merge.
        dk = m.get("deleted_keys_absent")
        if dk:
            keycols = m["key_columns"]
            on = " AND ".join(f"t.{k}=d.{k}" for k in keycols)
            n = int(ctx.target.scalar(
                f"SELECT COUNT(*) AS n FROM {ctx.target.qualify(ds, table)} t "
                f"JOIN {ctx.target.qualify(ds, dk['delta_table'])} d ON {on} "
                f"WHERE d.{dk['op_column']} = '{dk['delete_value']}'"))
            sr.add(CheckResult(pattern="merge_idempotency", target=f"{prefix} (deletes applied)",
                status=Status.PASS if n == 0 else Status.FAIL, story_id=story,
                message=f"{n} op='{dk['delete_value']}' keys still present (expected 0)",
                expected=0, actual=n))
    return sr
