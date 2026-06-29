"""BigQuery->BigQuery ELT fixture: seed a staging table in BigQuery, then run the
migration's transform SQL to build a mart table — entirely in-warehouse. Validated
by tests/bq_to_bq with source.engine=bigquery (the in-warehouse transform case;
the reference migration's staging->ods->dm builds are exactly this shape).

    python -m fixtures.load_elt
"""
from __future__ import annotations

from lib import config as cfg
from lib.engines import BigQueryEngine

# id, channel, amount, event_ts, status — already-typed staging rows
_STAGING = [
    (1, "voice", "12.50", "2026-05-03 09:15:00", "raw"),
    (2, "chat",  "0.00",  "2026-05-03 10:00:00", "raw"),
    (3, "email", "5.00",  "2026-05-20 14:30:00", "raw"),
    (4, "voice", "25.00", "2026-06-01 08:00:00", "raw"),
    (5, "voice", "7.25",  "2026-06-02 16:45:00", "raw"),
    (6, "email", "0.00",  "2026-06-15 11:11:11", "raw"),
]


def load(ds: str) -> None:
    from google.cloud import bigquery

    c = cfg.BigQueryConfig.from_env()
    eng = BigQueryEngine(c)
    bq = eng.client
    bq.create_dataset(bigquery.Dataset(f"{c.project}.{ds}"), exists_ok=True)
    SF = bigquery.SchemaField

    # 1) seed staging in BigQuery
    schema = [SF("id", "INT64"), SF("channel", "STRING"),
              SF("amount", "NUMERIC", precision=12, scale=2),
              SF("event_ts", "TIMESTAMP"), SF("status", "STRING")]
    rows = [{"id": i, "channel": ch, "amount": a, "event_ts": ts, "status": s}
            for (i, ch, a, ts, s) in _STAGING]
    ref = bigquery.DatasetReference(c.project, ds).table("elt_staging")
    bq.delete_table(ref, not_found_ok=True)
    bq.create_table(bigquery.Table(ref, schema=schema))
    bq.load_table_from_json(rows, ref, job_config=bigquery.LoadJobConfig(
        schema=schema, write_disposition="WRITE_APPEND")).result()

    stg = eng.qualify(ds, "elt_staging")
    mart = eng.qualify(ds, "elt_mart")
    mart_bad = eng.qualify(ds, "elt_mart_bad")

    # 2) the migration's transform: project + filter staging -> mart (in-warehouse)
    bq.query(f"""CREATE OR REPLACE TABLE {mart} AS
                 SELECT id, channel, amount, event_ts FROM {stg} WHERE status = 'raw'""").result()

    # negative twin target: same transform but one amount silently corrupted
    bq.query(f"""CREATE OR REPLACE TABLE {mart_bad} AS
                 SELECT id, channel,
                        IF(id = 1, NUMERIC '99.99', amount) AS amount, event_ts
                 FROM {stg} WHERE status = 'raw'""").result()
    print(f"[elt] seeded {ds}.elt_staging (6) -> transformed {ds}.elt_mart (+ elt_mart_bad)")


if __name__ == "__main__":
    load(cfg.require_env("BQ_DATASET_2"))
