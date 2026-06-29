"""P0-6 — cross-engine query/view parity (pattern 4), live fixture."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "query_parity.mvs.yaml"
NEGATIVE = HERE / "query_parity_negative.mvs.yaml"


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_query_parity(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    ids = {c.target for s in report.suites for c in s.checks}
    assert {"channel_rollup", "approx_distinct_contacts", "regex_classification",
            "cross_join_preservation"} <= ids


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_query_parity_must_fail(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    failed = {c.target for c in report.failures()}
    assert "rowset_broken" in failed, "rowset divergence not caught"
    assert "scalar_broken" in failed, "scalar divergence not caught"
