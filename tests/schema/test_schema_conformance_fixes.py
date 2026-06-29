"""DDL / schema_conformance correctness fixes.

Run via a STUB BigQuery engine (no live DB), driving lib.schema.run end to end:
  #1 a NUMERIC column must declare its scale, or a precision change passes silently
  #2 ARRAY/STRUCT inner types are compared — corruption INSIDE a complex type is caught
  #3 the declared target type is normalized (INTEGER == INT64) — no spurious failure
  #4 the declared source_type is normalized (BIGINT == INT64) — building block tested
Plus the pure helpers: type_signature() and BigQueryEngine._field_signature().
"""
from __future__ import annotations

from dataclasses import dataclass

from lib.engines import (BigQueryEngine, ColumnInfo, LogicalType, TableInfo,
                         normalize_type, type_signature)
from lib.harness import Context
from lib.report import Status
from lib.schema import run as schema_run


@dataclass
class _SF:                       # minimal google.cloud.bigquery.SchemaField stand-in
    name: str
    field_type: str
    mode: str = "NULLABLE"
    scale: int | None = None
    fields: tuple = ()


class _StubBQ:
    name = "bigquery"
    dialect = "bigquery"

    def __init__(self, tables):
        self._tables = tables                # {table_name: TableInfo}

    def list_tables(self, dataset):
        return sorted(self._tables)

    def introspect_table(self, dataset, table):
        return self._tables[table]


def _ctx(tables):
    c = Context(connections={"target": {"engine": "bigquery"}})
    c._cache["bigquery"] = _StubBQ(tables)   # inject stub; require()/ctx.target reuse it
    return c


def _suite(col):
    return {"pattern": "schema_conformance", "target_dataset": "ds",
            "tables": [{"table": "t", "columns": [col]}]}


def _status(sr, target):
    return next(c.status for c in sr.checks if c.target == target)


# --- pure helpers (#2 building blocks) --------------------------------------

def test_type_signature_parses_array_of_struct():
    assert type_signature("ARRAY<STRUCT<id INT64, qty INT64>>") == "ARRAY<STRUCT<id:INT64,qty:INT64>>"


def test_type_signature_normalizes_inner_scalars():
    assert type_signature("ARRAY<INTEGER>") == "ARRAY<INT64>"
    assert type_signature("STRUCT<a STRING, b BIGINT>") == "STRUCT<a:STRING,b:INT64>"


def test_field_signature_from_live_metadata():
    f = _SF("items", "RECORD", mode="REPEATED",
            fields=(_SF("id", "INTEGER"), _SF("qty", "INTEGER")))
    assert BigQueryEngine._field_signature(f) == "ARRAY<STRUCT<id:INT64,qty:INT64>>"


# --- #3: declared target type is normalized ---------------------------------

def test_declared_integer_matches_int64_target():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo("id", LogicalType("INT64"))])
    sr = schema_run(_suite({"name": "id", "type": "INTEGER"}), _ctx({"t": tbl}))
    assert _status(sr, "ds.t.id") == Status.PASS


# --- #1: NUMERIC scale must be declared --------------------------------------

def test_numeric_without_declared_scale_fails():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo("price", LogicalType("NUMERIC", scale=0))])
    sr = schema_run(_suite({"name": "price", "type": "NUMERIC"}), _ctx({"t": tbl}))
    assert _status(sr, "ds.t.price") == Status.FAIL


def test_numeric_with_matching_scale_passes():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo("price", LogicalType("NUMERIC", scale=2))])
    sr = schema_run(_suite({"name": "price", "type": "NUMERIC", "scale": 2}), _ctx({"t": tbl}))
    assert _status(sr, "ds.t.price") == Status.PASS


def test_numeric_scale_mismatch_fails():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo("price", LogicalType("NUMERIC", scale=0))])
    sr = schema_run(_suite({"name": "price", "type": "NUMERIC", "scale": 2}), _ctx({"t": tbl}))
    assert _status(sr, "ds.t.price") == Status.FAIL


# --- #2: corruption INSIDE arrays/structs is caught --------------------------

def test_array_of_struct_inner_corruption_fails():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo(
        "items", LogicalType("ARRAY"), type_signature="ARRAY<STRUCT<id:STRING,qty:INT64>>")])
    sr = schema_run(_suite({"name": "items", "type": "ARRAY<STRUCT<id INT64, qty INT64>>"}),
                    _ctx({"t": tbl}))
    assert _status(sr, "ds.t.items") == Status.FAIL


def test_array_of_struct_match_passes():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo(
        "items", LogicalType("ARRAY"), type_signature="ARRAY<STRUCT<id:INT64,qty:INT64>>")])
    sr = schema_run(_suite({"name": "items", "type": "ARRAY<STRUCT<id INT64, qty INT64>>"}),
                    _ctx({"t": tbl}))
    assert _status(sr, "ds.t.items") == Status.PASS


# --- #4: declared source_type is normalized (building block) -----------------

def test_normalize_type_synonyms():
    assert normalize_type("BIGINT").name == "INT64"
    assert normalize_type("DECIMAL(10,2)").name == "NUMERIC"
    assert normalize_type("FLOAT").name == "FLOAT64"


# --- object-type fidelity (table vs view) ------------------------------------

def _obj_suite(object_type):
    return {"pattern": "schema_conformance", "target_dataset": "ds",
            "tables": [{"table": "t", "expect_object_type": object_type,
                        "columns": [{"name": "id", "type": "INT64"}]}]}


def test_object_type_match_passes():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo("id", LogicalType("INT64"))],
                    options={"table_type": "TABLE"})
    sr = schema_run(_obj_suite("TABLE"), _ctx({"t": tbl}))
    assert _status(sr, "ds.t (object_type)") == Status.PASS


def test_object_type_flip_to_view_fails():
    tbl = TableInfo("ds", "t", columns=[ColumnInfo("id", LogicalType("INT64"))],
                    options={"table_type": "VIEW"})   # declared TABLE, landed as VIEW
    sr = schema_run(_obj_suite("TABLE"), _ctx({"t": tbl}))
    assert _status(sr, "ds.t (object_type)") == Status.FAIL


def test_object_type_external_and_snapshot_normalize_to_table():
    # BQ external tables / snapshots / clones are table variants — expect_object_type: TABLE matches.
    for raw in ("EXTERNAL", "SNAPSHOT", "CLONE"):
        tbl = TableInfo("ds", "t", columns=[ColumnInfo("id", LogicalType("INT64"))],
                        options={"table_type": raw})
        sr = schema_run(_obj_suite("TABLE"), _ctx({"t": tbl}))
        assert _status(sr, "ds.t (object_type)") == Status.PASS, f"{raw} should match TABLE"
