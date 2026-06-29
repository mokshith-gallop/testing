"""Pattern 16 — Egress parity (Axis A+C, P0-7).

The migration egresses a target table to a sink. We assert the outbound data matches
the legacy export:
  - GCS EXPORT DATA: run the migration's EXPORT, read the files back, and assert the
    exported rowset is byte-identical to the legacy rows after canonical sort, with
    control totals (row_cnt, control_total) reconciling to 0 variance.

(RDBMS upsert sinks — SQL Server MERGE / Postgres ON CONFLICT — are out of scope
until the sandbox has those engines; we don't skip what we can't run, we omit it.)

The export SQL is a migration artifact (escape hatch §5.1).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from . import canonicalize as C
from . import config as cfg
from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

SCHEMA = {
    "type": "object",
    "required": ["pattern", "source_database", "exports"],
    "properties": {
        "pattern": {"const": "egress_parity"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "source_database": {"type": "string"},
        "exports": {
            "type": "array", "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "source_sql", "target_export_sql", "export_uri_prefix", "columns"],
                "properties": {
                    "id": {"type": "string"},
                    "source_sql": {"type": "string"},          # legacy rows that SHOULD be exported
                    "target_export_sql": {"type": "string"},   # the migration's EXPORT DATA (escape hatch)
                    "export_uri_prefix": {"type": "string"},   # gs://bucket/prefix to read back
                    "columns": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "control_total_column": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def _to_num(v):
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _read_gcs_jsonl(uri_prefix: str) -> list[dict]:
    """Read all newline-delimited-JSON objects under a gs:// prefix."""
    from google.cloud import storage
    from .gcp_auth import bigquery_credentials

    assert uri_prefix.startswith("gs://")
    bucket_name, _, prefix = uri_prefix[5:].partition("/")
    client = storage.Client(project=cfg.require_env("GCP_PROJECT"), credentials=bigquery_credentials())
    rows = []
    for blob in client.list_blobs(bucket_name, prefix=prefix):
        for line in blob.download_as_text().splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _sorted_digest(rows, cols) -> str:
    """Order-independent byte digest: sha256 over canon rows sorted lexicographically."""
    canon = sorted(C.canon_row(r, cols, coerce_numeric_strings=True) for r in rows)
    return hashlib.sha256("\n".join(canon).encode("utf-8")).hexdigest()


@register("egress_parity", SCHEMA, mutates=True)   # runs EXPORT DATA (writes to GCS)
def run(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "impala", "bigquery")
    sr = SuiteResult(pattern="egress_parity", suite_id=suite.get("id", "egress_parity"))
    story = suite.get("story_id")

    for ex in suite["exports"]:
        eid = ex["id"]
        cols = ex["columns"]
        legacy = ctx.source.query(ex["source_sql"])

        # Run the migration's EXPORT DATA, then read the files back.
        ctx.target.query(ex["target_export_sql"])
        exported = _read_gcs_jsonl(ex["export_uri_prefix"])

        # Byte-identity after canonical sort.
        ld, ed = _sorted_digest(legacy, cols), _sorted_digest(exported, cols)
        sr.add(CheckResult(pattern="egress_parity", target=f"{eid} (byte-identity)",
            status=Status.PASS if ld == ed else Status.FAIL, story_id=story,
            message=(f"export byte-identical after sort (n={len(exported)})" if ld == ed
                     else f"export differs: legacy n={len(legacy)} exported n={len(exported)}"),
            expected=ld, actual=ed))

        # Control totals reconcile to 0 variance.
        rc_ok = len(legacy) == len(exported)
        sr.add(CheckResult(pattern="egress_parity", target=f"{eid} (row_cnt)",
            status=Status.PASS if rc_ok else Status.FAIL, story_id=story,
            message=f"row_cnt legacy={len(legacy)} exported={len(exported)}",
            expected=len(legacy), actual=len(exported)))
        ctc = ex.get("control_total_column")
        if ctc:
            ls = sum((_to_num(r.get(ctc)) or Decimal(0)) for r in legacy)
            es = sum((_to_num(r.get(ctc)) or Decimal(0)) for r in exported)
            sr.add(CheckResult(pattern="egress_parity", target=f"{eid} (control_total:{ctc})",
                status=Status.PASS if ls == es else Status.FAIL, story_id=story,
                message=f"control_total legacy={ls} exported={es} variance={abs(ls-es)}",
                expected=str(ls), actual=str(es)))
    return sr
