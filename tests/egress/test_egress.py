"""P0-7 — egress parity (pattern 16): GCS EXPORT byte-identity + control totals."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "egress.mvs.yaml"
NEGATIVE = HERE / "egress_negative.mvs.yaml"


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_egress(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    checks = [c for s in report.suites for c in s.checks]
    assert any("byte-identity" in c.target and c.status == Status.PASS for c in checks)
    assert any("control_total" in c.target and c.status == Status.PASS for c in checks)


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_egress_must_fail(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    failed = {c.target for c in report.failures()}
    assert any("byte-identity" in t for t in failed), "lossy export not caught"
    assert any("row_cnt" in t for t in failed), "row count drop not caught"
    assert any("control_total" in t for t in failed), "control total variance not caught"
