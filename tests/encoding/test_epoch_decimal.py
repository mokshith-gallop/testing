"""P0-3 — epoch conversion (pattern 5) + DECIMAL roundtrip (pattern 6), live fixture."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "encoding.mvs.yaml"
NEGATIVE = HERE / "encoding_negative.mvs.yaml"


@pytest.fixture(scope="module")
def edge_seeded(bq_engine, impala_engine):
    from lib import config as cfg
    from fixtures.load_epoch_edge import load
    load(cfg.require_env("SOURCE_DATABASE"), cfg.require_env("BQ_DATASET_2"))


def test_epoch_to_instant_unit():
    """Unit: millis vs seconds reading of the same lying column."""
    from lib.epoch import epoch_to_instant
    millis = 1780272000000
    assert epoch_to_instant(millis, "millis").year == 2026
    assert epoch_to_instant(millis, "seconds") is None or epoch_to_instant(millis, "seconds").year > 50000
    assert epoch_to_instant(99999999999999999, "millis") is None  # out of range


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_encoding(run_spec_file, bq_engine, impala_engine, edge_seeded):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    targets = {c.target for s in report.suites for c in s.checks}
    assert any("_dq_audit" in t for t in targets), "out-of-range audit not exercised"


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_encoding_must_fail(run_spec_file, bq_engine, impala_engine, edge_seeded):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    fails = report.failures()
    # Wrong encoding lands the value outside the sane range -> caught either as the
    # lying-column guard or as "should be NULL". Either signal proves the check bites.
    epoch_caught = any(("lying-column guard" in c.message or "should be NULL" in c.message)
                       and "issued_ts" in c.target for c in fails)
    assert epoch_caught, "wrong epoch encoding not caught"
    assert any("decimal mismatch" in c.message for c in fails), "decimal corruption not caught"
