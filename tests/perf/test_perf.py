"""BigQuery query-performance pattern: measure mode records numbers (no gate),
assert mode gates on a free dry-run bytes budget. BigQuery-only, no Impala."""
import pathlib

import pytest

from lib.report import Status

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "perf.mvs.yaml"
NEGATIVE = HERE / "perf_negative.mvs.yaml"
IMPALA = HERE / "perf_impala.mvs.yaml"


@pytest.mark.live_bq
def test_golden_perf(run_spec_file, bq_engine):
    report = run_spec_file(GOLDEN)
    assert report.status == Status.PASS, "failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    checks = [c for s in report.suites for c in s.checks]
    # measure mode produced a measurement (recorded, not a gate) with real metrics
    meas = [c for c in checks if c.measurement]
    assert meas and meas[0].metrics.get("runs") == 3
    # assert mode gated on dry-run bytes and passed
    assert any(c.target == "fact-scan-bytes-budget" and c.status == Status.PASS for c in checks)


@pytest.mark.negative
@pytest.mark.live_bq
def test_negative_perf_must_fail(run_spec_file, bq_engine):
    report = run_spec_file(NEGATIVE)
    assert report.status == Status.FAIL
    assert any("bytes_scanned" in c.message for c in report.failures())


@pytest.mark.live_bq
def test_compare_same_engine(run_spec_file, bq_engine):
    report = run_spec_file(HERE / "perf_compare.mvs.yaml")
    assert report.status == Status.PASS, "failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    assert any("bytes_scanned" in c.target and c.status == Status.PASS
               for s in report.suites for c in s.checks)


@pytest.mark.negative
@pytest.mark.live_bq
def test_compare_negative_must_fail(run_spec_file, bq_engine):
    report = run_spec_file(HERE / "perf_compare_negative.mvs.yaml")
    assert report.status == Status.FAIL


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_compare_cross_engine_report(run_spec_file, bq_engine, impala_engine):
    report = run_spec_file(HERE / "perf_compare_xengine.mvs.yaml")
    # report mode never gates -> PASS, and the checks are recorded as measurements
    assert report.status == Status.PASS
    meas = [c for s in report.suites for c in s.checks if c.measurement]
    assert len(meas) == 2 and all("b/a=" in c.message for c in meas)


@pytest.mark.live_bq
def test_regression_establish_then_compare(run_spec_file, bq_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("PERF_BASELINE", str(tmp_path / "baseline.json"))
    spec = HERE / "perf_regression.mvs.yaml"
    r1 = run_spec_file(spec)          # first run establishes the baseline
    assert r1.status == Status.PASS
    assert any(c.measurement and "baseline established" in c.message
               for s in r1.suites for c in s.checks)
    r2 = run_spec_file(spec)          # second run gates against it (same bytes -> PASS)
    assert r2.status == Status.PASS
    assert any("within baseline" in c.message for s in r2.suites for c in s.checks)


@pytest.mark.negative
@pytest.mark.live_bq
def test_regression_negative_must_fail(run_spec_file, bq_engine):
    report = run_spec_file(HERE / "perf_regression_negative.mvs.yaml")
    assert report.status == Status.FAIL
    assert any("baseline" in c.message for c in report.failures())


@pytest.mark.live_impala
def test_golden_perf_impala(run_spec_file, impala_engine):
    report = run_spec_file(IMPALA)
    assert report.status == Status.PASS, "failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    checks = [c for s in report.suites for c in s.checks]
    # measure mode recorded Impala profile metrics (cpu_ms is Impala-specific)
    meas = [c for c in checks if c.measurement]
    assert meas and "cpu_ms" in meas[0].metrics
    # assert mode gated on bytes_scanned + elapsed_ms and passed
    assert any(c.target == "fact-bytes-budget-impala" and c.status == Status.PASS for c in checks)
