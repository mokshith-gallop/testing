"""Cross-engine canonicalization — the one place NULL/float/timestamp/decimal
normalization lives (SPEC §15: drift here is the classic false-mismatch source).

Used by fingerprint parity (pattern 3), aggregate parity (pattern 2), and the
file/nested validator. Both engines' rows pass through the SAME functions, so a
BigQuery row and an Impala row that represent the same logical value produce the
same canonical bytes — and therefore the same hash.

Order-independent table fingerprint: we fold per-row digests with both SUM and XOR
(mod 2^256) plus a row count. SUM is duplicate-sensitive (two identical rows add,
not cancel); XOR catches different multisets that happen to share a SUM. The triple
(count, sum, xor) is the table digest.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

NULL_SENTINEL = "\x00__NULL__\x00"
_FIELD_SEP = "\x1f"   # unit separator — cannot collide with normal text
DEFAULT_FLOAT_DECIMALS = 6


def canon_value(v: Any, *, float_decimals: int = DEFAULT_FLOAT_DECIMALS) -> str:
    """Normalize a single Python value to a canonical string."""
    if v is None:
        return NULL_SENTINEL
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v != v:                       # NaN
            return "\x00NaN\x00"
        if v in (float("inf"), float("-inf")):
            return "\x00Inf\x00" if v > 0 else "\x00-Inf\x00"
        # Round to kill float-repr drift, then normalize through Decimal so a float and
        # a Decimal of the same value canonicalize identically (1500.5 == 1500.50).
        return _norm_decimal(Decimal(str(round(v, float_decimals))))
    if isinstance(v, Decimal):
        return _norm_decimal(v)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    if isinstance(v, _dt.datetime):
        return _norm_datetime(v)
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, _dt.time):
        return v.replace(microsecond=0).isoformat()
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(canon_value(x, float_decimals=float_decimals) for x in v) + "]"
    if isinstance(v, dict):
        items = sorted((str(k), canon_value(val, float_decimals=float_decimals)) for k, val in v.items())
        return "{" + ",".join(f"{k}:{val}" for k, val in items) + "}"
    if isinstance(v, int):
        return str(v)
    # str and everything else: exact string form. Try numeric-string -> Decimal so
    # "1.50" (Impala STRING) and Decimal("1.5") (BQ NUMERIC) canonicalize equal.
    s = str(v)
    return s


def _norm_decimal(d: Decimal) -> str:
    """Canonical decimal: strip trailing zeros but keep value (1.50 == 1.5 == 1.500)."""
    if d != d:
        return "\x00NaN\x00"
    d = d.normalize()
    # normalize() can yield exponent form (e.g. 1E+2); expand it.
    sign, digits, exp = d.as_tuple()
    s = format(d, "f")
    if s in ("-0", "-0.0"):
        s = "0"
    return s


def _norm_datetime(v: _dt.datetime) -> str:
    """UTC, truncated to the second. Naive datetimes are treated as UTC."""
    if v.tzinfo is not None:
        v = v.astimezone(_dt.timezone.utc).replace(tzinfo=None)
    v = v.replace(microsecond=0)
    return v.strftime("%Y-%m-%d %H:%M:%S")


def maybe_numeric(s: str) -> str:
    """Canonicalize a numeric-looking string the way a NUMERIC would canonicalize,
    so a value stored as STRING on one side matches NUMERIC on the other."""
    try:
        return _norm_decimal(Decimal(s))
    except (InvalidOperation, ValueError, TypeError):
        return s


def canon_row(row: dict, columns: Sequence[str], *, float_decimals: int = DEFAULT_FLOAT_DECIMALS,
              coerce_numeric_strings: bool = False) -> str:
    """Fixed-column-order canonical string for one row."""
    parts = []
    for c in columns:
        v = row.get(c)
        cv = canon_value(v, float_decimals=float_decimals)
        if coerce_numeric_strings and isinstance(v, str):
            cv = maybe_numeric(v)
        parts.append(cv)
    return _FIELD_SEP.join(parts)


def row_digest(row: dict, columns: Sequence[str], **opts) -> int:
    h = hashlib.sha256(canon_row(row, columns, **opts).encode("utf-8")).digest()
    return int.from_bytes(h, "big")


_MOD = 1 << 256


def table_fingerprint(rows: Iterable[dict], columns: Sequence[str], **opts) -> dict:
    """Order-independent multiset fingerprint of a table: (count, sum, xor)."""
    count = 0
    fsum = 0
    fxor = 0
    for r in rows:
        d = row_digest(r, columns, **opts)
        count += 1
        fsum = (fsum + d) % _MOD
        fxor ^= d
    return {
        "count": count,
        "sum": format(fsum, "064x"),
        "xor": format(fxor, "064x"),
        "digest": hashlib.sha256(f"{count}:{fsum:x}:{fxor:x}".encode()).hexdigest(),
    }
