"""source_setup — stand up the legacy source from its real DDL (ON by default; set
DMT_SOURCE_SETUP=0 to skip for a real read-only legacy), cross-check against it, then tear it down.

Offline tests cover the pure logic (URI sanitize, comment-aware split, the default-on/opt-out
guard, name parsing, teardown order) with a fake engine. The live golden/negative twins (live_bq +
live_impala, source_setup ON by default) prove it end-to-end through the harness: golden PASSes;
the negative — declared source_type disagreeing with the applied legacy — FAILs.
"""
import pathlib

import pytest

from lib import build
from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "source_setup.mvs.yaml"
NEGATIVE = HERE / "source_setup_negative.mvs.yaml"
SETUP_FAILURE = HERE / "source_setup_failure_negative.mvs.yaml"


# --- offline: pure logic, fake engine ---------------------------------------

class _FakeHive:
    def __init__(self):
        self.stmts: list[str] = []

    def execute(self, *stmts):
        self.stmts.extend(stmts)


class _FakeCtx:
    def __init__(self, source_kind="hive"):
        self.source_kind = source_kind     # 'hive' -> no INVALIDATE, so ctx.source is unused
        self.hive = _FakeHive()


def test_sanitize_rewrites_cluster_uri_keeps_external():
    sql = "CREATE EXTERNAL TABLE t (id BIGINT) STORED AS PARQUET LOCATION 'hdfs://nn/data/t';"
    out = build._sanitize_source_ddl(sql, "/tmp/dmt_src")
    assert "hdfs://" not in out
    assert "/tmp/dmt_src/data/t" in out
    assert "EXTERNAL" in out               # stays external -> non-ACID -> Impala can read


def test_split_handles_semicolon_inside_a_comment():
    sql = "-- do not hand-edit; change the manifest\nCREATE TABLE a (x INT);\nCREATE TABLE b (y INT);"
    stmts = build._split_statements(sql)
    assert len(stmts) == 2
    assert all(s.upper().startswith("CREATE TABLE") for s in stmts)


def test_sanitize_flips_acid_to_non_acid():
    # our HS2 client cannot create an ACID table -> flip transactional true to false (schema unchanged)
    sql = "CREATE TABLE t (id BIGINT) STORED AS ORC TBLPROPERTIES ('transactional'='true');"
    out = build._sanitize_source_ddl(sql, "/tmp/dmt_src")
    assert "'transactional'='false'" in out
    assert "'true'" not in out


def test_split_respects_semicolon_inside_a_serde_regex():
    # a RegexSerDe input.regex contains ';' — it must NOT split the CREATE (a naive split would)
    sql = ("CREATE TABLE t (a STRING) "
           "ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.RegexSerDe' "
           "WITH SERDEPROPERTIES ('input.regex'='([^;]*);(.*)');\n"
           "CREATE TABLE u (b INT);")
    stmts = build._split_statements(sql)
    assert len(stmts) == 2                          # the ';' inside the quoted regex is protected
    assert "RegexSerDe" in stmts[0] and stmts[1].upper().startswith("CREATE TABLE U")


def test_setup_source_noop_when_disabled(monkeypatch):
    monkeypatch.setenv(build.SOURCE_SETUP_ENV, "0")
    # opt-OUT: DMT_SOURCE_SETUP=0 -> no-op (never write a REAL read-only legacy), no engine touched
    assert build.setup_source(object(), {"ddl": ["whatever.hql"]}, ".") == []


def test_setup_source_creates_external_and_parses_names(monkeypatch, tmp_path):
    monkeypatch.delenv(build.SOURCE_SETUP_ENV, raising=False)   # default ON -> applies without the env
    f = tmp_path / "legacy.hql"
    f.write_text("CREATE DATABASE legacy LOCATION 'hdfs://nn/legacy';\n"
                 "CREATE EXTERNAL TABLE legacy.t (id BIGINT) STORED AS PARQUET "
                 "LOCATION 'hdfs://nn/legacy/t';")
    ctx = _FakeCtx("hive")
    created = build.setup_source(ctx, {"ddl": [str(f)], "location_base": "/tmp/dmt_src"}, ".")
    assert ("database", "legacy") in created and ("table", "legacy.t") in created
    blob = " ".join(ctx.hive.stmts)
    assert "EXTERNAL" in blob and "/tmp/dmt_src/" in blob and "hdfs://" not in blob


def test_setup_source_ignores_commented_out_create(monkeypatch, tmp_path):
    monkeypatch.delenv(build.SOURCE_SETUP_ENV, raising=False)
    f = tmp_path / "legacy.hql"
    # a commented-out CREATE must NOT be tracked — else teardown would drop a real, pre-existing table
    f.write_text("-- CREATE TABLE old_ghost (x INT);\nCREATE TABLE real_t (id BIGINT);")
    created = build.setup_source(_FakeCtx("hive"), {"ddl": [str(f)], "location_base": "/tmp/dmt_src"}, ".")
    names = [n for _, n in created]
    assert "real_t" in names and "old_ghost" not in names


def test_setup_source_tears_down_on_partial_failure(monkeypatch, tmp_path):
    monkeypatch.delenv(build.SOURCE_SETUP_ENV, raising=False)
    f = tmp_path / "legacy.hql"
    f.write_text("CREATE DATABASE legacy;\nCREATE TABLE legacy.t (id BIGINT);")

    class _Boom(_FakeHive):
        def execute(self, *stmts):           # blow up on the CREATE TABLE, mid-setup
            for s in stmts:
                self.stmts.append(s)
                if "CREATE TABLE" in s.upper():
                    raise RuntimeError("ddl error mid-setup")
    ctx = _FakeCtx("hive")
    ctx.hive = _Boom()
    with pytest.raises(RuntimeError):
        build.setup_source(ctx, {"ddl": [str(f)], "location_base": "/tmp/dmt_src"}, ".")
    # the database made before the failure is torn down (best-effort), not leaked
    assert "DROP DATABASE IF EXISTS legacy CASCADE" in " ".join(ctx.hive.stmts)


def test_teardown_drops_tables_before_databases():
    ctx = _FakeCtx("hive")
    build.teardown_source(ctx, [("database", "legacy"), ("table", "legacy.t")])
    blob = " ".join(ctx.hive.stmts)
    assert "DROP TABLE IF EXISTS legacy.t" in blob
    assert "DROP DATABASE IF EXISTS legacy CASCADE" in blob
    assert blob.index("DROP TABLE") < blob.index("DROP DATABASE")   # tables first


# --- live: end-to-end through the harness (source_setup is ON by default) -----

@pytest.fixture
def _default_source_setup(monkeypatch):
    # source_setup applies BY DEFAULT — assert the golden runs with DMT_SOURCE_SETUP UNSET
    # (no opt-in needed). Opt-out (=0) is covered by the offline test_setup_source_noop_when_disabled.
    monkeypatch.delenv(build.SOURCE_SETUP_ENV, raising=False)


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_source_setup_golden(_default_source_setup, run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    assert any("(source map)" in c.target for s in report.suites for c in s.checks)


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_source_setup_negative_must_fail(_default_source_setup, run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL, "negative twin unexpectedly passed"
    assert any("(source map)" in c.target for c in report.failures()), "type-map break not caught"


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_source_setup_failure_does_not_silently_pass(_default_source_setup, run_spec_file, bq_engine, impala_engine):
    # A source_setup whose DDL can't apply must SURFACE — never a silent PASS. The harness either
    # raises (failure propagated) or reports non-PASS; both are acceptable, a PASS is not. The
    # build dataset provisioned before source_setup is still torn down (see the build.py/harness fix).
    try:
        report = run_spec_file(SETUP_FAILURE)
    except Exception:
        return  # propagated as a raise — loud failure, acceptable
    assert report.status != Status.PASS, "a failed source_setup silently passed"
