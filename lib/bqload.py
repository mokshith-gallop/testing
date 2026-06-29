"""Pattern 14 — Bulk load per-format (Axis E, P0-2).

`bq load` each source URI (PARQUET / CSV / NEWLINE_DELIMITED_JSON, optionally with
hive partitioning) into a scratch BigQuery table, then assert landed row count ==
expected and (for hive-partitioned loads) that the partition column is present.

Fully declarative (SPEC §5.1): the agent supplies file->table map, format, and
expected counts; the harness performs the load and the assertions.
"""
from __future__ import annotations

from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

_FORMATS = ["NEWLINE_DELIMITED_JSON", "CSV", "PARQUET", "AVRO", "ORC"]

BULK_LOAD_SCHEMA = {
    "type": "object",
    "required": ["pattern", "target_dataset", "loads"],
    "properties": {
        "pattern": {"const": "bulk_load"},
        "id": {"type": "string"},
        "story_id": {"type": "string"},
        "target_dataset": {"type": "string"},
        "loads": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["table", "uri", "format", "expected_count"],
                "properties": {
                    "table": {"type": "string"},
                    "uri": {"type": "string"},
                    "format": {"enum": _FORMATS},
                    "expected_count": {"type": "integer", "minimum": 0},
                    "field_delimiter": {"type": "string"},
                    "skip_leading_rows": {"type": "integer", "minimum": 0},
                    "autodetect": {"type": "boolean"},
                    "hive_partitioning": {
                        "type": "object",
                        "required": ["source_uri_prefix"],
                        "properties": {
                            "mode": {"enum": ["AUTO", "STRINGS", "CUSTOM"]},
                            "source_uri_prefix": {"type": "string"},
                            "expect_partition_column": {"type": "string"},
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


@register("bulk_load", BULK_LOAD_SCHEMA, mutates=True)   # creates/loads scratch tables
def run_bulk_load(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "bigquery")
    from google.cloud import bigquery

    sr = SuiteResult(pattern="bulk_load", suite_id=suite.get("id", "bulk_load"))
    story = suite.get("story_id")
    ds = suite["target_dataset"]
    bq = ctx.target.client
    project = ctx.target.cfg.project

    for ld in suite["loads"]:
        table = ld["table"]
        ref = bigquery.DatasetReference(project, ds).table(table)
        target = f"{ds}.{table}"
        cfg = bigquery.LoadJobConfig(
            source_format=getattr(bigquery.SourceFormat, ld["format"]),
            write_disposition="WRITE_TRUNCATE",
            autodetect=ld.get("autodetect", True),
        )
        if ld["format"] == "CSV":
            cfg.field_delimiter = ld.get("field_delimiter", ",")
            cfg.skip_leading_rows = ld.get("skip_leading_rows", 0)
        hp = ld.get("hive_partitioning")
        if hp:
            opts = bigquery.HivePartitioningOptions()
            opts.mode = hp.get("mode", "AUTO")
            opts.source_uri_prefix = hp["source_uri_prefix"]
            cfg.hive_partitioning = opts

        try:
            bq.load_table_from_uri(ld["uri"], ref, job_config=cfg).result()
        except Exception as e:  # noqa: BLE001
            sr.add(CheckResult(pattern="bulk_load", target=target, status=Status.FAIL,
                               story_id=story, message=f"load failed: {type(e).__name__}: {e}"))
            continue

        loaded = bq.get_table(ref)
        n = loaded.num_rows
        want = ld["expected_count"]
        sr.add(CheckResult(
            pattern="bulk_load", target=f"{target} ({ld['format']})",
            status=Status.PASS if n == want else Status.FAIL, story_id=story,
            message=f"landed {n} rows (expected {want})", expected=want, actual=n,
            metrics={"format": ld["format"], "rows": n}))

        if hp and hp.get("expect_partition_column"):
            col = hp["expect_partition_column"]
            present = any(f.name == col for f in loaded.schema)
            sr.add(CheckResult(
                pattern="bulk_load", target=f"{target} (partition meta)",
                status=Status.PASS if present else Status.FAIL, story_id=story,
                message=f"hive partition column '{col}' {'present' if present else 'MISSING'}",
                expected=col, actual=[f.name for f in loaded.schema]))
    return sr
