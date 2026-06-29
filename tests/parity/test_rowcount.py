"""P0-2 — bulk load (pattern 14) + row-count parity (pattern 1), live fixture.

Requires the format files staged (fixtures.stage_files); the test stages them so it
is self-contained.
"""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "rowcount_parity.mvs.yaml"
NEGATIVE = HERE / "rowcount_parity_negative.mvs.yaml"


@pytest.fixture(scope="module")
def staged(bq_engine):
    from fixtures.stage_files import stage
    return stage()


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_load_and_rowcount(run_spec_file, bq_engine, impala_engine, staged):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    # All four formats actually loaded + both datasets' counts checked.
    targets = {c.target for s in report.suites for c in s.checks}
    assert any("PARQUET" in t for t in targets)
    assert any("partition meta" in t for t in targets)


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_load_and_rowcount_must_fail(run_spec_file, bq_engine, impala_engine, staged):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    failed = {c.target for c in report.failures()}
    assert any("_neg_load_json" in t for t in failed), "wrong landed count not caught"
    assert any("partition meta" in t for t in failed), "wrong partition column not caught"
    assert any("dim_agent" in t for t in failed), "rowcount mismatch not caught"
