"""BigQuery -> BigQuery in-warehouse transform validation (source.engine=bigquery).

Proves the harness honors a declared BigQuery source — the parity patterns compare
a seeded BQ staging table against a BQ mart built by the transform SQL. No Impala.
"""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "bq_to_bq.mvs.yaml"
NEGATIVE = HERE / "bq_to_bq_negative.mvs.yaml"


@pytest.fixture(scope="module")
def elt(bq_engine):
    from fixtures.load_elt import load
    from lib import config as cfg
    load(cfg.require_env("BQ_DATASET_2"))


@pytest.mark.live_bq
def test_golden_bq_to_bq(run_spec_file, bq_engine, elt):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    # The patterns actually ran against a BigQuery source (not skipped).
    assert sum(len(s.checks) for s in report.suites) >= 4


@pytest.mark.negative
@pytest.mark.live_bq
def test_negative_bq_to_bq_must_fail(run_spec_file, bq_engine, elt):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    failed = {c.target for c in report.failures()}
    assert any("fingerprint" in t.lower() or "elt_mart_bad" in t for t in failed)
    assert any("amount" in t and "sum" in t for t in failed), "corrupted SUM not caught"
