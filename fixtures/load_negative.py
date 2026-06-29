"""Negative fixtures for P0-5: a deliberately broken SCD-2 table (two-current, a
timeline gap, a wrong row_hash) and an append-only 'merge' target (non-idempotent).

    python -m fixtures.load_negative
"""
from __future__ import annotations

from lib import config as cfg
from lib.engines import BigQueryEngine
from . import seed_data as S


def load(scratch_ds: str, _unused: str = "") -> None:
    from google.cloud import bigquery
    dm_ds = ods_ds = scratch_ds  # negative tables live in the scratch dataset
    c = cfg.BigQueryConfig.from_env()
    bq = BigQueryEngine(c).client
    SF = bigquery.SchemaField

    # --- broken SCD-2 ---
    h = S.scd2_row_hash
    cust_schema = [
        SF("cust_key", "INT64"), SF("contact_id", "INT64"), SF("full_name", "STRING"),
        SF("segment", "STRING"), SF("eff_from", "TIMESTAMP"), SF("eff_to", "TIMESTAMP"),
        SF("is_current", "BOOL"), SF("row_hash", "STRING"),
    ]
    INF = "9999-12-31 00:00:00"
    broken = [
        # contact 201: TWO is_current=TRUE
        {"cust_key": 10, "contact_id": 201, "full_name": "X", "segment": "gold",
         "eff_from": "2024-01-01 00:00:00", "eff_to": INF, "is_current": True, "row_hash": h(201, "X", "gold")},
        {"cust_key": 11, "contact_id": 201, "full_name": "X", "segment": "platinum",
         "eff_from": "2025-01-01 00:00:00", "eff_to": INF, "is_current": True, "row_hash": h(201, "X", "platinum")},
        # contact 202: a GAP (v1 closes 2024-06-01 but v2 opens 2024-09-01) + wrong hash on v2
        {"cust_key": 12, "contact_id": 202, "full_name": "Y", "segment": "silver",
         "eff_from": "2024-01-01 00:00:00", "eff_to": "2024-06-01 00:00:00", "is_current": False,
         "row_hash": h(202, "Y", "silver")},
        {"cust_key": 13, "contact_id": 202, "full_name": "Y", "segment": "gold",
         "eff_from": "2024-09-01 00:00:00", "eff_to": INF, "is_current": True,
         "row_hash": "deadbeefdeadbeefdeadbeefdeadbeef"},
    ]
    _recreate(bq, c.project, dm_ds, "dim_customer_broken", cust_schema, broken)

    # --- append-only 'merge' target (non-idempotent) ---
    schema = [SF("invoice_id", "INT64"), SF("contact_id", "INT64"),
              SF("amount", "NUMERIC", precision=12, scale=2)]
    rows = [{"invoice_id": r["invoice_id"], "contact_id": r["contact_id"],
             "amount": str(r["amount"])} for r in S.invoices()]
    _recreate(bq, c.project, ods_ds, "mrg_target_bad", schema, rows)
    print(f"[negative] seeded {dm_ds}.dim_customer_broken + {ods_ds}.mrg_target_bad")


def _recreate(bq, project, ds, table, schema, rows):
    from google.cloud import bigquery
    bq.create_dataset(bigquery.Dataset(f"{project}.{ds}"), exists_ok=True)
    ref = bigquery.DatasetReference(project, ds).table(table)
    bq.delete_table(ref, not_found_ok=True)
    bq.create_table(bigquery.Table(ref, schema=schema))
    if rows:
        bq.load_table_from_json(rows, ref, job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition="WRITE_APPEND")).result()


if __name__ == "__main__":
    load(cfg.require_env("BQ_DATASET_2"))
