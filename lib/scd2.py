"""Pattern 8 — SCD-2 continuity (Axis B+C, P0-5).

Per entity key on the target: versions form a gap-free, non-overlapping timeline;
exactly one is_current=TRUE; the current version's eff_to is the terminal sentinel
(9999-12-31); and the surrogate row_hash is byte-identical to md5(concat_ws('|', ...))
— recomputed by the harness and cross-checked against the legacy stored hash.
"""
from __future__ import annotations

import datetime as dt
import hashlib

from . import canonicalize as C
from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

SCHEMA = {
    "type": "object",
    "required": ["pattern", "target_dataset", "tables"],
    "properties": {
        "pattern": {"const": "scd2_continuity"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "target_dataset": {"type": "string"},
        "source_database": {"type": "string"},
        "tables": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["target", "entity_key", "eff_from", "eff_to", "current_flag"],
                "properties": {
                    "target": {"type": "string"}, "source": {"type": "string"},
                    "entity_key": {"type": "string"},
                    "eff_from": {"type": "string"}, "eff_to": {"type": "string"},
                    "current_flag": {"type": "string"},
                    "terminal_eff_to": {"type": "string"},
                    "row_hash": {"type": "string"},
                    "hash_columns": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _terminal_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def _md5_concat(row, cols) -> str:
    return hashlib.md5("|".join(str(row[c]) for c in cols).encode("utf-8")).hexdigest()


@register("scd2_continuity", SCHEMA)
def run(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "bigquery")
    sr = SuiteResult(pattern="scd2_continuity", suite_id=suite.get("id", "scd2_continuity"))
    story = suite.get("story_id")
    ds = suite["target_dataset"]
    src_db = suite.get("source_database")
    src = ctx.source if src_db else None

    for t in suite["tables"]:
        ek, ef, et, cur = t["entity_key"], t["eff_from"], t["eff_to"], t["current_flag"]
        terminal = _terminal_date(t.get("terminal_eff_to", "9999-12-31"))
        rh = t.get("row_hash")
        hcols = t.get("hash_columns")
        prefix = f"{ds}.{t['target']}"
        rows = ctx.target.query(f"SELECT * FROM {ctx.target.qualify(ds, t['target'])}")

        groups: dict = {}
        for r in rows:
            groups.setdefault(r[ek], []).append(r)

        gaps = current_bad = terminal_bad = hash_bad = 0
        for key, versions in groups.items():
            versions.sort(key=lambda r: r[ef])
            # gap-free + non-overlapping
            for a, b in zip(versions, versions[1:]):
                if C._norm_datetime(a[et]) != C._norm_datetime(b[ef]):
                    gaps += 1
                    sr.add(CheckResult(pattern="scd2_continuity", target=f"{prefix}[{key}]",
                        status=Status.FAIL, story_id=story,
                        message=f"timeline gap/overlap: {a[et]} != next {b[ef]}"))
            # exactly one current
            currents = [v for v in versions if v[cur]]
            if len(currents) != 1:
                current_bad += 1
                sr.add(CheckResult(pattern="scd2_continuity", target=f"{prefix}[{key}]",
                    status=Status.FAIL, story_id=story,
                    message=f"expected exactly 1 is_current, got {len(currents)}"))
            else:
                cur_et = currents[0][et]
                cur_date = cur_et.date() if isinstance(cur_et, dt.datetime) else None
                if cur_date != terminal:
                    terminal_bad += 1
                    sr.add(CheckResult(pattern="scd2_continuity", target=f"{prefix}[{key}]",
                        status=Status.FAIL, story_id=story,
                        message=f"current eff_to {cur_et} != terminal {terminal}"))
            # closed (non-current) versions must NOT be terminal
            for v in versions:
                if not v[cur] and isinstance(v[et], dt.datetime) and v[et].date() == terminal:
                    terminal_bad += 1
                    sr.add(CheckResult(pattern="scd2_continuity", target=f"{prefix}[{key}]",
                        status=Status.FAIL, story_id=story,
                        message="non-current version has terminal eff_to (should be closed)"))

        # surrogate hash byte-identical to recomputed md5(concat_ws('|', hash_columns))
        if rh and hcols:
            for r in rows:
                if _md5_concat(r, hcols) != r[rh]:
                    hash_bad += 1
                    sr.add(CheckResult(pattern="scd2_continuity",
                        target=f"{prefix}.{rh}[{r[ek]}]", status=Status.FAIL, story_id=story,
                        message=f"row_hash mismatch: stored {r[rh]} != md5(concat_ws('|',{hcols}))"))

        sr.add(CheckResult(pattern="scd2_continuity", target=prefix,
            status=Status.PASS if (gaps + current_bad + terminal_bad + hash_bad) == 0 else Status.FAIL,
            story_id=story,
            message=f"{len(groups)} entities, {len(rows)} versions; "
                    f"gaps={gaps} current_bad={current_bad} terminal_bad={terminal_bad} hash_bad={hash_bad}",
            metrics={"entities": len(groups), "versions": len(rows)}))
    return sr
