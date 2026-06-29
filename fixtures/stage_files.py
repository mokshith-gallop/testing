"""Stage the fact_interaction rows to GCS in several formats for the pattern-14
bulk-load test: NEWLINE_DELIMITED_JSON, CSV (pipe-delimited), PARQUET, and a
hive-partitioned JSON layout (event_date=YYYY-MM-DD/). Deterministic; overwrites.

    python -m fixtures.stage_files            # stage to gs://$SANDBOX_BUCKET/p0-2/fact/
"""
from __future__ import annotations

import csv
import io
import json

from lib import config as cfg
from . import seed_data as S

PREFIX = "p0-2/fact"
COLS = ["interaction_id", "contact_id", "channel", "duration_sec", "amount", "event_ts", "event_date"]


def _rows():
    for r in S.interactions():
        yield {
            "interaction_id": r["interaction_id"], "contact_id": r["contact_id"],
            "channel": r["channel"], "duration_sec": r["duration_sec"],
            "amount": str(r["amount"]),
            "event_ts": r["event_ts"].strftime("%Y-%m-%d %H:%M:%S"),
            "event_date": r["event_date"].isoformat(),
        }


def _bucket():
    from google.cloud import storage

    from lib.gcp_auth import bigquery_credentials

    name = cfg.require_env("SANDBOX_BUCKET")
    client = storage.Client(project=cfg.require_env("GCP_PROJECT"), credentials=bigquery_credentials())
    return client.bucket(name), name


def stage() -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq

    bucket, name = _bucket()
    rows = list(_rows())

    # JSON (newline-delimited)
    nd = "\n".join(json.dumps(r) for r in rows)
    bucket.blob(f"{PREFIX}/json/data.json").upload_from_string(nd, content_type="application/json")

    # CSV, pipe-delimited, with header
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLS, delimiter="|")
    w.writeheader()
    w.writerows(rows)
    bucket.blob(f"{PREFIX}/csv/data.csv").upload_from_string(buf.getvalue(), content_type="text/csv")

    # PARQUET, explicit types so NUMERIC/TIMESTAMP/DATE round-trip
    schema = pa.schema([
        ("interaction_id", pa.int64()), ("contact_id", pa.int64()), ("channel", pa.string()),
        ("duration_sec", pa.int64()), ("amount", pa.decimal128(12, 2)),
        ("event_ts", pa.timestamp("us", tz="UTC")), ("event_date", pa.date32()),
    ])
    import datetime as dt
    from decimal import Decimal
    pa_rows = {
        "interaction_id": [r["interaction_id"] for r in S.interactions()],
        "contact_id": [r["contact_id"] for r in S.interactions()],
        "channel": [r["channel"] for r in S.interactions()],
        "duration_sec": [r["duration_sec"] for r in S.interactions()],
        "amount": [r["amount"] for r in S.interactions()],
        "event_ts": [r["event_ts"] for r in S.interactions()],
        "event_date": [r["event_date"] for r in S.interactions()],
    }
    table = pa.table(pa_rows, schema=schema)
    pbuf = io.BytesIO()
    pq.write_table(table, pbuf)
    bucket.blob(f"{PREFIX}/parquet/data.parquet").upload_from_string(
        pbuf.getvalue(), content_type="application/octet-stream")

    # Hive-partitioned JSON: event_date as a path partition, dropped from the data.
    by_date: dict[str, list[dict]] = {}
    for r in rows:
        body = {k: v for k, v in r.items() if k != "event_date"}
        by_date.setdefault(r["event_date"], []).append(body)
    for d, body in by_date.items():
        nd = "\n".join(json.dumps(b) for b in body)
        bucket.blob(f"{PREFIX}/hive/event_date={d}/data.json").upload_from_string(nd)

    uri = f"gs://{name}/{PREFIX}"
    print(f"[stage] {sum(1 for _ in rows)} rows -> {uri} (json,csv,parquet,hive)")
    return uri


if __name__ == "__main__":
    stage()
