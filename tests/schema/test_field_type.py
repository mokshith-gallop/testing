"""Unit tests for BigQueryEngine._field_type — the BQ SchemaField → LogicalType map.

Regression guard: an ARRAY<STRUCT<…>> is reported by BigQuery as mode=REPEATED with
field_type=RECORD. The classifier must check REPEATED (→ ARRAY) BEFORE RECORD (→ STRUCT),
or the array-ness is lost and the column is misclassified as STRUCT.
"""
from __future__ import annotations

from dataclasses import dataclass

from lib.engines import BigQueryEngine


@dataclass
class _Field:
    field_type: str
    mode: str = "NULLABLE"
    precision: int | None = None
    scale: int | None = None


def test_array_of_struct_resolves_to_array():
    # mode=REPEATED, field_type=RECORD  → ARRAY (not STRUCT)
    assert BigQueryEngine._field_type(_Field("RECORD", "REPEATED")).name == "ARRAY"


def test_array_of_scalar_resolves_to_array():
    assert BigQueryEngine._field_type(_Field("STRING", "REPEATED")).name == "ARRAY"


def test_plain_record_is_struct():
    assert BigQueryEngine._field_type(_Field("RECORD", "NULLABLE")).name == "STRUCT"


def test_numeric_scale_preserved():
    t = BigQueryEngine._field_type(_Field("NUMERIC", "NULLABLE", precision=12, scale=2))
    assert t.name == "NUMERIC" and t.scale == 2


def test_plain_scalar_normalized():
    assert BigQueryEngine._field_type(_Field("INTEGER", "NULLABLE")).name == "INT64"
