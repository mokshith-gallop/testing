"""P0-4 — aggregate-checksum parity (pattern 2) + fingerprint parity (pattern 3)."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "value_parity.mvs.yaml"
NEGATIVE = HERE / "value_parity_negative.mvs.yaml"


@pytest.fixture(scope="module")
def edge_seeded(bq_engine, impala_engine):
    from lib import config as cfg
    from fixtures.load_epoch_edge import load
    load(cfg.require_env("SOURCE_DATABASE"), cfg.require_env("BQ_DATASET_2"))


def test_canonicalize_float_decimal_unify():
    """A float and a Decimal of the same value must canonicalize identically."""
    from decimal import Decimal
    from lib import canonicalize as C
    assert C.canon_value(1500.5) == C.canon_value(Decimal("1500.50"))
    assert C.canon_value(0.0) == C.canon_value(-0.0) == C.canon_value(Decimal("0.00"))


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_value_parity(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    # Every fingerprint matched — incl. the cross-engine in-warehouse (pushdown) ones.
    fps = [c for s in report.suites for c in s.checks if c.pattern == "fingerprint_parity"]
    assert len(fps) >= 3 and all(c.status == Status.PASS for c in fps)
    assert any("pushdown" in c.target for c in fps), "cross-engine pushdown parity not exercised"


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_value_parity_must_fail(run_spec_file, bq_engine, impala_engine, edge_seeded):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    fails = report.failures()
    assert any(c.pattern == "aggregate_parity" for c in fails), "aggregate corruption not caught"
    assert any(c.pattern == "fingerprint_parity" and "MISMATCH" in c.message for c in fails)
    # Localization must pinpoint the corrupted key (5001).
    loc = [c for c in fails if "localized" in c.target]
    assert loc and "5001" in loc[0].message, "differing key not localized"
