"""Load the fixture migration into the live sandbox: legacy -> Hive (:10000),
target -> BigQuery. Idempotent (drop+recreate). Refreshes Impala metadata so reads
on :21050 see Hive-written data.

Usage:
    python -m fixtures.load --all          # seed both sides
    python -m fixtures.load --legacy       # Hive only
    python -m fixtures.load --target       # BigQuery only
    python -m fixtures.load --teardown     # drop everything
"""
from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal

from lib import config as cfg
from lib.engines import BigQueryEngine, HiveEngine, ImpalaEngine
from . import seed_data as S


# ---------------------------------------------------------------------------
# Hive value formatting
# ---------------------------------------------------------------------------

def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dt.datetime):
        return f"CAST('{v.strftime('%Y-%m-%d %H:%M:%S')}' AS TIMESTAMP)"
    if isinstance(v, dt.date):
        return f"CAST('{v.isoformat()}' AS DATE)"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _values(rows, cols) -> str:
    return ",\n".join("(" + ",".join(_lit(r[c]) for c in cols) + ")" for r in rows)


# ---------------------------------------------------------------------------
# Legacy (Hive)
# ---------------------------------------------------------------------------

def load_legacy(db: str) -> None:
    hive = HiveEngine(cfg.ImpalaConfig.from_env())
    hive.execute(f"CREATE DATABASE IF NOT EXISTS {db}")

    # dim_agent
    agents = [{"agent_id": a, "agent_name": n, "team": t} for (a, n, t) in S.AGENTS]
    hive.execute(
        f"DROP TABLE IF EXISTS {db}.dim_agent",
        f"CREATE TABLE {db}.dim_agent (agent_id BIGINT, agent_name STRING, team STRING) STORED AS PARQUET",
        f"INSERT INTO {db}.dim_agent VALUES\n{_values(agents, ['agent_id','agent_name','team'])}",
    )

    # dim_customer (SCD-2)
    ccols = ["cust_key","contact_id","full_name","segment","balance","eff_from","eff_to","is_current","row_hash"]
    hive.execute(
        f"DROP TABLE IF EXISTS {db}.dim_customer",
        f"""CREATE TABLE {db}.dim_customer (
              cust_key BIGINT, contact_id BIGINT, full_name STRING, segment STRING,
              balance DECIMAL(12,2), eff_from TIMESTAMP, eff_to TIMESTAMP,
              is_current BOOLEAN, row_hash STRING) STORED AS PARQUET""",
        f"INSERT INTO {db}.dim_customer VALUES\n{_values(list(S.customers()), ccols)}",
    )

    # ods_invoice (epoch millis under *_sec + op)
    icols = ["invoice_id","contact_id","amount","issued_sec","op"]
    hive.execute(
        f"DROP TABLE IF EXISTS {db}.ods_invoice_acid",
        f"""CREATE TABLE {db}.ods_invoice_acid (
              invoice_id BIGINT, contact_id BIGINT, amount DECIMAL(12,2),
              issued_sec BIGINT, op STRING) STORED AS PARQUET""",
        f"INSERT INTO {db}.ods_invoice_acid VALUES\n{_values(list(S.invoices()), icols)}",
    )

    # fact_interaction (multi-col partition via staging -> dynamic partition insert)
    fcols = ["interaction_id","contact_id","channel","duration_sec","amount","event_ts"]
    facts = list(S.interactions())
    hive.execute(
        f"DROP TABLE IF EXISTS {db}.fact_interaction",
        f"DROP TABLE IF EXISTS {db}.fact_interaction_stg",
        f"""CREATE TABLE {db}.fact_interaction (
              interaction_id BIGINT, contact_id BIGINT, channel STRING,
              duration_sec INT, amount DECIMAL(12,2), event_ts TIMESTAMP)
            PARTITIONED BY (event_year INT, event_month INT) STORED AS PARQUET""",
        f"""CREATE TABLE {db}.fact_interaction_stg (
              interaction_id BIGINT, contact_id BIGINT, channel STRING,
              duration_sec INT, amount DECIMAL(12,2), event_ts TIMESTAMP,
              event_year INT, event_month INT) STORED AS PARQUET""",
        f"INSERT INTO {db}.fact_interaction_stg VALUES\n"
        + _values(facts, fcols + ["event_year","event_month"]),
        "SET hive.exec.dynamic.partition=true",
        "SET hive.exec.dynamic.partition.mode=nonstrict",
        f"""INSERT INTO {db}.fact_interaction PARTITION(event_year, event_month)
            SELECT {', '.join(fcols)}, event_year, event_month FROM {db}.fact_interaction_stg""",
        f"DROP TABLE {db}.fact_interaction_stg",
    )

    # Make Hive-written data visible to Impala reads.
    impala = ImpalaEngine(cfg.ImpalaConfig.from_env())
    impala.execute("INVALIDATE METADATA")
    print(f"[legacy] seeded Hive db {db}; Impala metadata invalidated")


# ---------------------------------------------------------------------------
# Target (BigQuery)
# ---------------------------------------------------------------------------

def load_target(canon_ds: str) -> None:
    from google.cloud import bigquery

    c = cfg.BigQueryConfig.from_env()
    bq = BigQueryEngine(c).client
    # All migrated tables land in one canonical dataset (the customer's migration
    # decides how many datasets it actually uses; this fixture uses one + a scratch).
    ods_ds = dm_ds = canon_ds
    bq.create_dataset(bigquery.Dataset(f"{c.project}.{canon_ds}"), exists_ok=True)

    SF = bigquery.SchemaField

    def recreate(ds, table, schema, rows, partition_field=None, cluster=None,
                 expiration_days=None, labels=None, ingestion_partition=False, json_default=str):
        ref = bigquery.DatasetReference(c.project, ds).table(table)
        bq.delete_table(ref, not_found_ok=True)
        t = bigquery.Table(ref, schema=schema)
        if partition_field:
            t.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY, field=partition_field,
                expiration_ms=int(expiration_days * 86_400_000) if expiration_days else None)
        elif ingestion_partition:
            # No named field -> partitioned by ingestion time on the _PARTITIONTIME pseudo-column.
            t.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY)
        if cluster:
            t.clustering_fields = cluster
        if labels:
            t.labels = labels
        bq.create_table(t)
        job = bq.load_table_from_json(
            rows, ref,
            job_config=bigquery.LoadJobConfig(schema=schema,
                                              write_disposition="WRITE_APPEND"))
        job.result()

    def _json(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, dt.datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, dt.date):
            return v.isoformat()
        return v

    # dim_agent
    recreate(dm_ds, "dim_agent",
             [SF("agent_id","INT64"), SF("agent_name","STRING"), SF("team","STRING")],
             [{"agent_id": a, "agent_name": n, "team": t} for (a,n,t) in S.AGENTS])

    # dim_customer (NUMERIC(12,2), TIMESTAMP, BOOL)
    cust_schema = [
        SF("cust_key","INT64"), SF("contact_id","INT64"), SF("full_name","STRING"),
        SF("segment","STRING"), SF("balance","NUMERIC", precision=12, scale=2),
        SF("eff_from","TIMESTAMP"), SF("eff_to","TIMESTAMP"),
        SF("is_current","BOOL"), SF("row_hash","STRING"),
    ]
    recreate(dm_ds, "dim_customer", cust_schema,
             [{k: _json(v) for k, v in r.items()} for r in S.customers()])

    # fact_interaction: partition by event_date, cluster by contact_id (collapse)
    fact_schema = [
        SF("interaction_id","INT64"), SF("contact_id","INT64"), SF("channel","STRING"),
        SF("duration_sec","INT64"), SF("amount","NUMERIC", precision=12, scale=2),
        SF("event_ts","TIMESTAMP"), SF("event_date","DATE"),
    ]
    fact_rows = [{
        "interaction_id": r["interaction_id"], "contact_id": r["contact_id"],
        "channel": r["channel"], "duration_sec": r["duration_sec"],
        "amount": _json(r["amount"]), "event_ts": _json(r["event_ts"]),
        "event_date": _json(r["event_date"]),
    } for r in S.interactions()]
    recreate(dm_ds, "fact_interaction", fact_schema, fact_rows,
             partition_field="event_date", cluster=["contact_id"],
             expiration_days=3650, labels={"layer": "dm", "pii": "false"})

    # ods_invoice: issued_ts is the converted TIMESTAMP (millis -> instant)
    inv_schema = [
        SF("invoice_id","INT64"), SF("contact_id","INT64"),
        SF("amount","NUMERIC", precision=12, scale=2),
        SF("issued_ts","TIMESTAMP"), SF("op","STRING"),
    ]
    inv_rows = [{
        "invoice_id": r["invoice_id"], "contact_id": r["contact_id"],
        "amount": _json(r["amount"]), "issued_ts": _json(r["issued_ts"]), "op": r["op"],
    } for r in S.invoices()]
    recreate(ods_ds, "ods_invoice", inv_schema, inv_rows)

    # team_roster: a nested ARRAY<STRUCT<...>> column, so schema_conformance can prove it
    # validates types INSIDE arrays/structs (target-only — no legacy cross-check).
    recreate(dm_ds, "team_roster",
             [SF("team", "STRING"),
              SF("members", "RECORD", mode="REPEATED",
                 fields=[SF("name", "STRING"), SF("level", "INT64")])],
             [{"team": "voice", "members": [{"name": "Grace Ito", "level": 3},
                                            {"name": "Iris Vu", "level": 2}]},
              {"team": "chat", "members": [{"name": "Hugo Park", "level": 1}]}])

    # event_audit: ingestion-time partitioned (no named field -> _PARTITIONTIME), so
    # schema_conformance can prove it detects ingestion-time partitioning.
    recreate(dm_ds, "event_audit",
             [SF("event_id", "INT64"), SF("note", "STRING")],
             [{"event_id": 1, "note": "seed"}, {"event_id": 2, "note": "seed"}],
             ingestion_partition=True)

    print(f"[target] seeded BigQuery canonical dataset {canon_ds}")


def teardown(db: str, canon_ds: str, scratch_ds: str) -> None:
    try:
        HiveEngine(cfg.ImpalaConfig.from_env()).execute(f"DROP DATABASE IF EXISTS {db} CASCADE")
        ImpalaEngine(cfg.ImpalaConfig.from_env()).execute("INVALIDATE METADATA")
    except Exception as e:
        print("legacy teardown warning:", e)
    from google.cloud import bigquery
    c = cfg.BigQueryConfig.from_env()
    bq = BigQueryEngine(c).client
    for ds in (canon_ds, scratch_ds):
        bq.delete_dataset(f"{c.project}.{ds}", delete_contents=True, not_found_ok=True)
    print("[teardown] dropped fixture db + datasets")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true")
    p.add_argument("--legacy", action="store_true")
    p.add_argument("--target", action="store_true")
    p.add_argument("--teardown", action="store_true")
    a = p.parse_args(argv)

    db = cfg.require_env("SOURCE_DATABASE")
    canon_ds = cfg.require_env("BQ_DATASET_1")     # all migrated tables
    scratch_ds = cfg.require_env("BQ_DATASET_2")   # harness working space

    if a.teardown:
        teardown(db, canon_ds, scratch_ds); return 0
    if a.all or a.legacy:
        load_legacy(db)
    if a.all or a.target:
        load_target(canon_ds)
    if a.all:
        from .load_epoch_edge import load as load_edge
        load_edge(db, scratch_ds)
        from .load_merge import load as load_merge
        load_merge(scratch_ds)
        from .load_elt import load as load_elt
        load_elt(scratch_ds)
        from .stage_files import stage
        stage()
    if not any([a.all, a.legacy, a.target]):
        p.error("specify --all / --legacy / --target / --teardown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
