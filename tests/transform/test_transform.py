"""P0-5 — SCD-2 continuity (8) + MERGE idempotency (9) + FK orphan (10), live."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "transform.mvs.yaml"
NEGATIVE = HERE / "transform_negative.mvs.yaml"


@pytest.fixture(scope="module")
def merge_seeded(bq_engine):
    from lib import config as cfg
    from fixtures.load_merge import load
    load(cfg.require_env("BQ_DATASET_2"))


@pytest.fixture(scope="module")
def negative_seeded(bq_engine):
    from lib import config as cfg
    from fixtures.load_negative import load
    from fixtures.load_merge import load as load_merge   # mrg_delta needed by the bad merge
    scratch = cfg.require_env("BQ_DATASET_2")
    load_merge(scratch)
    load(scratch)


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_transform(run_spec_file, bq_engine, impala_engine, merge_seeded):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    patterns = {c.pattern for s in report.suites for c in s.checks}
    assert patterns == {"scd2_continuity", "merge_idempotency", "fk_orphan"}


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_transform_must_fail(run_spec_file, bq_engine, impala_engine, negative_seeded):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    msgs = " ".join(c.message for c in report.failures())
    assert "is_current" in msgs, "two-current SCD-2 not caught"
    assert "gap" in msgs, "SCD-2 timeline gap not caught"
    assert "row_hash mismatch" in msgs, "wrong surrogate hash not caught"
    assert "NOT idempotent" in msgs, "non-idempotent merge not caught"
    assert any(c.pattern == "fk_orphan" for c in report.failures()), "orphan cap not enforced"
