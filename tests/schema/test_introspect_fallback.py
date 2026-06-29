"""_introspect_source — the schema cross-check reads a legacy source table via Impala (fast),
and falls back to Hive (HS2) when Impala can't read it. Impala has no Java-SerDe support, so
JsonSerDe/RegexSerDe tables raise 'SerDe ... not supported'; Hive's DESCRIBE reads its own
SerDes (and those tables are Hive-only in production too). Offline — fake engines, no live DB.
"""
import pytest

from lib import schema


class _Raises:
    """An engine whose introspect_table fails the way Impala fails on a Java SerDe."""
    def introspect_table(self, db, table):
        raise Exception("AnalysisException: SerDe library "
                        "'org.apache.hive.hcatalog.data.JsonSerDe' is not supported")


class _Returns:
    def __init__(self, info):
        self._info = info

    def introspect_table(self, db, table):
        return self._info


class _Ctx:
    def __init__(self, source, hive):
        self.source, self.hive = source, hive


def test_falls_back_to_hive_when_impala_cannot_read_serde():
    sentinel = object()
    ctx = _Ctx(source=_Raises(), hive=_Returns(sentinel))   # Impala fails -> Hive returns
    assert schema._introspect_source(ctx, "staging", "stg_file_qa_forms") is sentinel


def test_uses_impala_when_it_can_read():
    sentinel = object()
    # Hive raises to prove it is NOT consulted when Impala succeeds (keeps Impala's speed).
    ctx = _Ctx(source=_Returns(sentinel), hive=_Raises())
    assert schema._introspect_source(ctx, "staging", "stg_crm_client") is sentinel


def test_hive_error_propagates_when_both_fail():
    # Fail-fast preserved: a table neither engine can read still ERRORs the suite (no silent skip).
    ctx = _Ctx(source=_Raises(), hive=_Raises())
    with pytest.raises(Exception):
        schema._introspect_source(ctx, "staging", "does_not_exist")
