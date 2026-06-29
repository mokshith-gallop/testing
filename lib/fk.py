"""Pattern 10 — FK orphan rate (Axis B, P0-5).

Per documented join path, count orphans (child rows whose key has no parent) via
LEFT JOIN ... IS NULL on both engines; assert the target orphan rate is within
±tolerance_pp (percentage points) of the legacy rate, and optionally below an
absolute max_orphan_rate.
"""
from __future__ import annotations

from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

SCHEMA = {
    "type": "object",
    "required": ["pattern", "source_database", "target_dataset", "joins"],
    "properties": {
        "pattern": {"const": "fk_orphan"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "source_database": {"type": "string"},
        "target_dataset": {"type": "string"},
        "joins": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["child", "child_key", "parent", "parent_key"],
                "properties": {
                    "child": {"type": "string"}, "child_key": {"type": "string"},
                    "parent": {"type": "string"}, "parent_key": {"type": "string"},
                    "tolerance_pp": {"type": "number"},
                    "max_orphan_rate": {"type": "number"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _orphan_rate(engine, child_fq, ckey, parent_fq, pkey):
    total = int(engine.scalar(f"SELECT COUNT(*) AS n FROM {child_fq}"))
    if total == 0:
        return 0, 0, 0.0
    orphans = int(engine.scalar(
        f"SELECT COUNT(*) AS n FROM {child_fq} c "
        f"LEFT JOIN {parent_fq} p ON c.{ckey} = p.{pkey} WHERE p.{pkey} IS NULL"))
    return orphans, total, orphans / total


@register("fk_orphan", SCHEMA)
def run(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "impala", "bigquery")
    sr = SuiteResult(pattern="fk_orphan", suite_id=suite.get("id", "fk_orphan"))
    story = suite.get("story_id")
    src_db = suite["source_database"]
    ds = suite["target_dataset"]

    for j in suite["joins"]:
        target = f"{ds}.{j['child']}->{j['parent']}"
        s_orph, s_tot, s_rate = _orphan_rate(
            ctx.source, f"{src_db}.{j['child']}", j["child_key"],
            f"{src_db}.{j['parent']}", j["parent_key"])
        t_orph, t_tot, t_rate = _orphan_rate(
            ctx.target, ctx.target.qualify(ds, j["child"]), j["child_key"],
            ctx.target.qualify(ds, j["parent"]), j["parent_key"])

        tol_pp = j.get("tolerance_pp", 0.0) / 100.0   # exact by default; set tolerance_pp to allow drift
        drift_ok = abs(t_rate - s_rate) <= tol_pp
        sr.add(CheckResult(pattern="fk_orphan", target=f"{target} (parity)",
            status=Status.PASS if drift_ok else Status.FAIL, story_id=story,
            message=f"orphan rate src={s_rate:.4%} ({s_orph}/{s_tot}) "
                    f"tgt={t_rate:.4%} ({t_orph}/{t_tot}) Δ={abs(t_rate-s_rate):.4%} (±{tol_pp:.4%})",
            metrics={"source_rate": s_rate, "target_rate": t_rate}))

        if "max_orphan_rate" in j:
            cap = j["max_orphan_rate"] / 100.0
            sr.add(CheckResult(pattern="fk_orphan", target=f"{target} (max)",
                status=Status.PASS if t_rate <= cap else Status.FAIL, story_id=story,
                message=f"target orphan rate {t_rate:.4%} vs cap {cap:.4%}"))
    return sr
