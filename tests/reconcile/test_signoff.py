"""P0-8 — end-to-end reconcile + meta-coverage (the meta-CI assertion, SPEC §6/§9).

Runs every golden spec into one sign-off report and asserts: all green, every
registered pattern covered, every flow-group's required patterns present.
"""
import pytest

from lib.registry import PATTERNS, load_all_patterns
from lib.reconcile import coverage_gaps, discover_golden, reconcile
from lib.report import Status


def test_all_patterns_have_a_golden_spec():
    """Static check (no infra): every registered pattern appears in some golden spec,
    except those exempt because their live golden needs external infra the meta sandbox
    doesn't run (e.g. dag_structure -> real Composer; proven by unit tests instead)."""
    import pathlib
    from lib.reconcile import _COVERAGE_EXEMPT
    load_all_patterns()
    text = "\n".join(p.read_text() for p in pathlib.Path("tests").rglob("*.mvs.yaml")
                     if "_negative" not in p.name)
    missing = [name for name in PATTERNS
               if name not in _COVERAGE_EXEMPT and f"pattern: {name}" not in text]
    assert not missing, f"patterns with no golden spec: {missing}"


@pytest.mark.live_bq
@pytest.mark.live_impala
def test_end_to_end_signoff(bq_engine, impala_engine):
    paths = discover_golden("tests")
    report = reconcile(paths)
    assert report.status == Status.PASS, "sign-off failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
    gaps = coverage_gaps(report)
    assert not gaps["missing_patterns"], f"patterns with no green check: {gaps['missing_patterns']}"
    assert not gaps["flow_gaps"], f"flow groups missing required patterns: {gaps['flow_gaps']}"
