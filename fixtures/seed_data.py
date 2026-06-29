"""Deterministic seed for the Layer-2 fixture migration (SPEC §10).

ONE canonical model of logical rows; both the legacy (Hive) and target (BigQuery)
representations are derived from it, so cross-engine parity holds *by construction*
for the known-good fixture (and the negative twins deliberately break it).

Hard cases covered (SPEC §10):
  - SCD-2 history (dim_customer: contact 101 has two versions)
  - DECIMAL(12,2) incl. zero / negative / large (balance, amount)
  - lying epoch column (ods_invoice.issued_sec holds MILLIS under a *_sec name)
  - multi-column Hive partition (event_year, event_month) collapsed to a single
    BQ partition (event_date) + clustering (contact_id)
  - FK orphan (fact_interaction 1006 -> contact 999, absent from dim_customer)
"""
from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal

UTC = dt.timezone.utc


def _ts(y, mo, d, h=0, mi=0, s=0) -> dt.datetime:
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def _dec(s: str) -> Decimal:
    return Decimal(s)


# Terminal SCD-2 "open" timestamp.
EFF_INFINITY = _ts(9999, 12, 31)


def scd2_row_hash(contact_id: int, full_name: str, segment: str) -> str:
    """md5(concat_ws('|', contact_id, full_name, segment)) — computed identically on
    both sides so the surrogate hash is byte-identical (pattern 8)."""
    payload = "|".join([str(contact_id), full_name, segment])
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# --- dim_agent: tiny exact-parity control table (no special cases) ---
AGENTS = [
    # agent_id, agent_name, team
    (1, "Grace Ito", "voice"),
    (2, "Hugo Park", "chat"),
    (3, "Iris Vu", "voice"),
]

# --- dim_customer: SCD-2 dimension (contact 101 has two versions) ---
# cust_key, contact_id, full_name, segment, balance, eff_from, eff_to, is_current
_CUSTOMERS_BASE = [
    (1, 101, "Ana Reyes", "gold",     _dec("1500.50"), _ts(2024, 1, 1),  _ts(2025, 6, 1), False),
    (2, 101, "Ana Reyes", "platinum", _dec("1500.50"), _ts(2025, 6, 1),  EFF_INFINITY,    True),
    (3, 102, "Ben Cole",  "silver",   _dec("0.00"),    _ts(2024, 3, 15), EFF_INFINITY,    True),
    (4, 103, "Cy Tan",    "gold",     _dec("250.25"),  _ts(2024, 5, 20), EFF_INFINITY,    True),
    (5, 104, "Dee Fox",   "bronze",   _dec("-10.00"),  _ts(2024, 7, 1),  EFF_INFINITY,    True),
    (6, 105, "Eve Lyn",   "gold",     _dec("99999.99"),_ts(2025, 2, 10), EFF_INFINITY,    True),
]


def customers():
    """Yield dim_customer rows as dicts incl. the derived row_hash."""
    for (cust_key, cid, name, seg, bal, eff_from, eff_to, cur) in _CUSTOMERS_BASE:
        yield {
            "cust_key": cust_key, "contact_id": cid, "full_name": name, "segment": seg,
            "balance": bal, "eff_from": eff_from, "eff_to": eff_to,
            "is_current": cur, "row_hash": scd2_row_hash(cid, name, seg),
        }


# --- fact_interaction: fact table, 2026-05 + 2026-06 partitions ---
# interaction_id, contact_id, channel, duration_sec, amount, event_ts
_INTERACTIONS = [
    (1001, 101, "voice", 320, _dec("12.50"), _ts(2026, 5, 3, 9, 15, 0)),
    (1002, 102, "chat",    0, _dec("0.00"),  _ts(2026, 5, 3, 10, 0, 0)),
    (1003, 101, "email",   0, _dec("5.00"),  _ts(2026, 5, 20, 14, 30, 0)),
    (1004, 103, "voice", 600, _dec("25.00"), _ts(2026, 6, 1, 8, 0, 0)),
    (1005, 104, "voice", 145, _dec("7.25"),  _ts(2026, 6, 2, 16, 45, 0)),
    (1006, 999, "chat",   30, _dec("1.00"),  _ts(2026, 6, 2, 17, 0, 0)),   # FK orphan (contact 999)
    (1007, 105, "email",   0, _dec("0.00"),  _ts(2026, 6, 15, 11, 11, 11)),
    (1008, 102, "voice", 410, _dec("18.75"), _ts(2026, 6, 20, 12, 0, 0)),
]


def interactions():
    for (iid, cid, ch, dur, amt, ts) in _INTERACTIONS:
        yield {
            "interaction_id": iid, "contact_id": cid, "channel": ch,
            "duration_sec": dur, "amount": amt, "event_ts": ts,
            # derived partition keys
            "event_year": ts.year, "event_month": ts.month, "event_date": ts.date(),
        }


# --- ods_invoice: epoch (lying millis) + DECIMAL; op column for MERGE delta ---
# invoice_id, contact_id, amount, issued_ts (the true instant), op
_INVOICES = [
    (5001, 101, _dec("100.00"), _ts(2026, 6, 1, 0, 0, 0),   "I"),
    (5002, 102, _dec("49.99"),  _ts(2026, 6, 1, 12, 0, 0),  "I"),
    (5003, 103, _dec("200.50"), _ts(2026, 6, 2, 6, 30, 0),  "U"),
    (5004, 104, _dec("12.00"),  _ts(2026, 6, 2, 9, 0, 0),   "I"),
    (5005, 105, _dec("75.25"),  _ts(2026, 6, 3, 23, 59, 59),"I"),
]


def _millis(ts: dt.datetime) -> int:
    return int(ts.timestamp() * 1000)


def invoices():
    """issued_sec holds MILLIS (the lie); issued_ts is the true converted instant."""
    for (inv, cid, amt, ts, op) in _INVOICES:
        yield {
            "invoice_id": inv, "contact_id": cid, "amount": amt,
            "issued_sec": _millis(ts), "issued_ts": ts, "op": op,
        }


# Convenience: the logical table catalog (used by loaders + expectations).
PARTITIONS = sorted({(r["event_year"], r["event_month"]) for r in interactions()})

if __name__ == "__main__":
    # Sanity dump.
    for name, gen in [("agents", lambda: iter(AGENTS)), ("customers", customers),
                      ("interactions", interactions), ("invoices", invoices)]:
        rows = list(gen())
        print(f"{name}: {len(rows)} rows")
    print("fact partitions:", PARTITIONS)
    print("101 row_hash:", scd2_row_hash(101, "Ana Reyes", "platinum"))
