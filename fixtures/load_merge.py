"""MERGE fixture for pattern 9: a target table + a delta with insert/update/delete
ops. Re-seeds the initial target state so the idempotency test is deterministic.

    python -m fixtures.load_merge
"""
from __future__ import annotations

from lib import config as cfg
from lib.engines import BigQueryEngine
from . import seed_data as S


def load(scratch_ds: str) -> None:
    from google.cloud import bigquery
    ods_ds = scratch_ds  # merge target/delta live in the scratch dataset
    c = cfg.BigQueryConfig.from_env()
    bq = BigQueryEngine(c).client
    bq.create_dataset(bigquery.Dataset(f"{c.project}.{ods_ds}"), exists_ok=True)
    SF = bigquery.SchemaField
    schema = [SF("invoice_id", "INT64"), SF("contact_id", "INT64"),
              SF("amount", "NUMERIC", precision=12, scale=2)]

    # initial target: invoices 5001-5005 at original amounts
    target = [{"invoice_id": r["invoice_id"], "contact_id": r["contact_id"],
               "amount": str(r["amount"])} for r in S.invoices()]
    _recreate(bq, c.project, ods_ds, "mrg_target", schema, target)

    # delta: update 5001, insert new 5006, delete 5005
    delta_schema = schema + [SF("op", "STRING")]
    delta = [
        {"invoice_id": 5001, "contact_id": 101, "amount": "111.11", "op": "U"},
        {"invoice_id": 5006, "contact_id": 106, "amount": "60.00", "op": "I"},
        {"invoice_id": 5005, "contact_id": 105, "amount": "75.25", "op": "D"},
    ]
    _recreate(bq, c.project, ods_ds, "mrg_delta", delta_schema, delta)
    print(f"[merge] seeded {ods_ds}.mrg_target (5 rows) + mrg_delta (U/I/D)")


def _recreate(bq, project, ds, table, schema, rows):
    from google.cloud import bigquery
    ref = bigquery.DatasetReference(project, ds).table(table)
    bq.delete_table(ref, not_found_ok=True)
    bq.create_table(bigquery.Table(ref, schema=schema))
    if rows:
        bq.load_table_from_json(rows, ref, job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition="WRITE_APPEND")).result()


if __name__ == "__main__":
    load(cfg.require_env("BQ_DATASET_2"))
