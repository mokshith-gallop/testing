"""Edge fixture for pattern 5 out-of-range handling: a legacy table with a valid
millis epoch, an out-of-range epoch, and a negative one; the migrated target NULLs
the out-of-range/negative rows and records them in a _dq_audit table (the
'out-of-range epochs -> NULL + _dq_audit row, not error' behavior, SPEC §5).

    python -m fixtures.load_epoch_edge        # seed legacy + target
"""
from __future__ import annotations

import datetime as dt

from lib import config as cfg
from lib.engines import BigQueryEngine, HiveEngine, ImpalaEngine

UTC = dt.timezone.utc
_VALID = int(dt.datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC).timestamp() * 1000)  # millis, year 2026
# id, raw_sec(millis), in_range
ROWS = [
    (1, _VALID, True),
    (2, 99999999999999999, False),   # absurdly large -> out of range
    (3, -5, False),                  # negative -> out of range
]


def load(db: str, scratch_ds: str) -> None:
    ods_ds = scratch_ds  # edge/negative tables live in the scratch dataset, not the canonical one
    hive = HiveEngine(cfg.ImpalaConfig.from_env())
    hive.execute(
        f"CREATE DATABASE IF NOT EXISTS {db}",
        f"DROP TABLE IF EXISTS {db}.ods_epoch_edge",
        f"CREATE TABLE {db}.ods_epoch_edge (id BIGINT, raw_sec BIGINT) STORED AS PARQUET",
        f"INSERT INTO {db}.ods_epoch_edge VALUES " + ",".join(f"({i},{s})" for (i, s, _) in ROWS),
    )
    ImpalaEngine(cfg.ImpalaConfig.from_env()).execute("INVALIDATE METADATA")

    from google.cloud import bigquery
    c = cfg.BigQueryConfig.from_env()
    bq = BigQueryEngine(c).client
    bq.create_dataset(bigquery.Dataset(f"{c.project}.{ods_ds}"), exists_ok=True)
    SF = bigquery.SchemaField

    # target: out-of-range rows converted to NULL
    edge_rows = []
    for (i, s, ok) in ROWS:
        ts = dt.datetime.fromtimestamp(s / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S") if ok else None
        edge_rows.append({"id": i, "converted_ts": ts})
    _recreate(bq, c.project, ods_ds, "ods_epoch_edge",
              [SF("id", "INT64"), SF("converted_ts", "TIMESTAMP")], edge_rows)

    # _dq_audit: one row per quarantined epoch
    audit = [{"src_table": "ods_epoch_edge", "src_column": "raw_sec", "row_id": i,
              "reason": "epoch out of range"} for (i, s, ok) in ROWS if not ok]
    _recreate(bq, c.project, ods_ds, "_dq_audit",
              [SF("src_table", "STRING"), SF("src_column", "STRING"),
               SF("row_id", "INT64"), SF("reason", "STRING")], audit)

    # Deliberately-corrupt target for the decimal negative twin: invoice 5001 amount
    # 100.00 -> 100.01 (a one-cent silent corruption the roundtrip check must catch).
    from . import seed_data as S
    corrupt = []
    for r in S.invoices():
        amt = "100.01" if r["invoice_id"] == 5001 else str(r["amount"])
        corrupt.append({"invoice_id": r["invoice_id"], "amount": amt})
    _recreate(bq, c.project, ods_ds, "ods_invoice_corrupt",
              [SF("invoice_id", "INT64"), SF("amount", "NUMERIC", precision=12, scale=2)], corrupt)
    print(f"[epoch-edge] seeded {db}.ods_epoch_edge + {ods_ds}.ods_epoch_edge/_dq_audit/ods_invoice_corrupt")


def _recreate(bq, project, ds, table, schema, rows):
    from google.cloud import bigquery
    ref = bigquery.DatasetReference(project, ds).table(table)
    bq.delete_table(ref, not_found_ok=True)
    bq.create_table(bigquery.Table(ref, schema=schema))
    if rows:
        bq.load_table_from_json(
            rows, ref, job_config=bigquery.LoadJobConfig(schema=schema,
                                                         write_disposition="WRITE_APPEND")).result()


if __name__ == "__main__":
    load(cfg.require_env("SOURCE_DATABASE"), cfg.require_env("BQ_DATASET_2"))
