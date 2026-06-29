"""transform_unit — the ELT transform unit tier (BQ-only, hermetic). Golden passes;
the buggy twin (wrong epoch unit + missing op filter) must fail."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "transform_unit.mvs.yaml"
NEGATIVE = HERE / "transform_unit_negative.mvs.yaml"


@pytest.mark.live_bq
def test_golden_transform_unit(run_spec_file, bq_engine):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    # exact-rows + all three property asserts all PASS
    statuses = {c.target: c.status for s in report.suites for c in s.checks}
    assert statuses.get("ods_invoice") == Status.PASS
    assert statuses.get("ods_invoice.rowcount") == Status.PASS
    assert statuses.get("ods_invoice.unique(invoice_id)") == Status.PASS


@pytest.mark.negative
@pytest.mark.live_bq
def test_negative_transform_unit_must_fail(run_spec_file, bq_engine):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    msgs = " ".join(c.message for c in report.failures())
    assert "mismatch" in msgs, "wrong epoch unit not caught by row compare"
    assert any("rowcount" in c.target for c in report.failures()), "kept delete row not caught"
