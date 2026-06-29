"""Synthetic large-table generator for BigQuery (perf testing).

Generates rows entirely IN BigQuery via GENERATE_ARRAY cross-join (no client data, no
egress), scale-tiered, partitioned/clustered, with an auto-expiry so generated tables
clean themselves up. BigQuery-only by design: it's serverless, so "big" is realistic
here — the single-node Impala sandbox (50 GB disk) is not the place to synthesize
billions (see COVERAGE.md).

Spec (YAML, not an MVS — this generates data, it doesn't validate):

    dataset: ${BQ_DATASET_2}
    expire_hours: 24
    max_bytes_billed: 50000000000
    tables:
      - name: perf_fact
        rows: ${PERF_SCALE:-small}        # small=1e6 | medium=1e8 | large=1e9 | <int>
        partition_by: event_date
        cluster_by: [customer_id]
        columns:
          - { name: id,          gen: sequence }
          - { name: customer_id, gen: "MOD(id, 1000000)" }
          - { name: amount, type: NUMERIC, gen: "ROUND(RAND()*1000, 2)" }
          - { name: event_date,  gen: "DATE_ADD(DATE '2026-01-01', INTERVAL MOD(id,365) DAY)" }
          - { name: channel,     gen: "['voice','chat','email'][OFFSET(MOD(id,3))]" }
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

from lib import config as cfg
from lib.engines import BigQueryEngine
from lib.mvs import expand_env

TIERS = {"small": 1_000_000, "medium": 100_000_000, "large": 1_000_000_000}
_CHUNK = 1_000_000   # rows per GENERATE_ARRAY leg (safe array size); cross-join to scale


def _rows(val) -> int:
    if isinstance(val, int):
        return val
    s = str(val).strip()
    return TIERS[s] if s in TIERS else int(float(s))


def _col_sql(col: dict) -> str:
    name = col["name"]
    if col.get("gen") == "sequence":
        return f"id AS {name}"
    expr = col["gen"]
    if col.get("type"):
        expr = f"CAST({expr} AS {col['type']})"
    return f"{expr} AS {name}"


def _table_sql(project: str, dataset: str, t: dict) -> tuple[str, int]:
    rows = _rows(t["rows"])
    n_chunks = max(1, -(-rows // _CHUNK))          # ceil(rows / chunk)
    chunk = min(rows, _CHUNK)
    clauses = ""
    if t.get("partition_by"):
        clauses += f" PARTITION BY {t['partition_by']}"
    if t.get("cluster_by"):
        clauses += f" CLUSTER BY {', '.join(t['cluster_by'])}"
    cols = ",\n         ".join(_col_sql(c) for c in t["columns"])
    sql = (
        f"CREATE OR REPLACE TABLE `{project}.{dataset}.{t['name']}`{clauses} AS\n"
        f"WITH seq AS (\n"
        f"  SELECT (c - 1) * {chunk} + r AS id\n"
        f"  FROM UNNEST(GENERATE_ARRAY(1, {n_chunks})) c, UNNEST(GENERATE_ARRAY(1, {chunk})) r\n"
        f")\n"
        f"SELECT {cols}\nFROM seq WHERE id <= {rows}"
    )
    return sql, rows


def generate(spec: dict) -> list[tuple[str, int]]:
    from google.cloud import bigquery

    c = cfg.BigQueryConfig.from_env()
    eng = BigQueryEngine(c)
    bq = eng.client
    dataset = spec["dataset"]
    bq.create_dataset(bigquery.Dataset(f"{c.project}.{dataset}"), exists_ok=True)
    expire_hours = int(spec.get("expire_hours", 24))
    max_bytes = spec.get("max_bytes_billed")
    job_cfg = bigquery.QueryJobConfig(maximum_bytes_billed=int(max_bytes)) if max_bytes else None

    out = []
    for t in spec["tables"]:
        sql, rows = _table_sql(c.project, dataset, t)
        bq.query(sql, job_config=job_cfg).result()
        # auto-expiry so synthetic tables don't linger (set via API; OPTIONS can't use now())
        ref = bigquery.DatasetReference(c.project, dataset).table(t["name"])
        tbl = bq.get_table(ref)
        tbl.expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=expire_hours)
        bq.update_table(tbl, ["expires"])
        print(f"[synth] {dataset}.{t['name']}: {rows:,} rows (expires in {expire_hours}h)")
        out.append((t["name"], rows))
    return out


def load(spec_path: str) -> list[tuple[str, int]]:
    return generate(yaml.safe_load(expand_env(Path(spec_path).read_text())))


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="dmtemplate-synth")
    p.add_argument("spec", help="path to a synth YAML spec")
    a = p.parse_args(argv)
    load(a.spec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
