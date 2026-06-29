"""P0-1 — Schema/DDL conformance (pattern 7), proven against the live fixture.

Golden spec must pass green; the negative twin must fail (proving the assertions
actually bite, not just no-op to PASS).
"""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "schema_conformance.mvs.yaml"
NEGATIVE = HERE / "schema_conformance_negative.mvs.yaml"


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_golden_schema_conformance(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(GOLDEN)
    failures = report.failures()
    assert report.status == Status.PASS, "golden failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in failures)
    # Sanity: the suites actually ran real checks (not silently skipped).
    total = sum(len(s.checks) for s in report.suites)
    assert total >= 30
    assert all(s.status in (Status.PASS,) for s in report.suites)


@pytest.mark.negative
@pytest.mark.live_bq
@pytest.mark.live_impala
def test_negative_schema_conformance_must_fail(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL, "negative twin unexpectedly passed"
    failed = {c.target for c in report.failures()}

    # Each deliberately-wrong assertion must surface as a failure.
    def has(substr):
        return any(substr in t for t in failed)

    assert has("dim_customer.balance"), "wrong type (NUMERIC->INT64) not caught"
    assert has("dim_customer.is_current"), "wrong type (BOOL->STRING) not caught"
    assert has("nonexistent_col"), "missing column not caught"
    assert has("ghost_table"), "missing table not caught"
    assert has("(partition)"), "wrong partition column not caught"
    assert has("(cluster)"), "wrong clustering not caught"
    assert has("(source map)"), "type-map break not caught"
    assert has("team_roster.members"), "wrong type INSIDE the array/struct not caught"
    assert has("fact_interaction.amount"), "NUMERIC column with no declared scale not caught"
    assert has("fact_interaction (object_type)"), "object-type flip (table declared as VIEW) not caught"
    assert any("(partition)" not in t and "balance" in t for t in failed) or has("count")
