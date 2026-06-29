"""transform_diff — cross-engine equivalence of a rewritten transform. Seeds the same
given into Impala + BigQuery, runs legacy T vs migrated T, asserts identical output.
Golden: equivalent -> PASS. Negative: migrated drops a group -> FAIL."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "transform_diff.mvs.yaml"
NEGATIVE = HERE / "transform_diff_negative.mvs.yaml"


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_transform_diff(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "diff failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    msg = " ".join(c.message for s in report.suites for c in s.checks)
    assert "identical" in msg


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_transform_diff_must_fail(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    assert any("NOT equivalent" in c.message for c in report.failures())
