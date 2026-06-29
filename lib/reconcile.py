"""P0-8 — end-to-end reconcile + sign-off.

Composes every golden MVS spec (P0-1..7) into a single sign-off Report over the full
fixture table set, and provides the meta-coverage check: every registered pattern has
a green golden spec, and every flow group's required patterns (SPEC §6) are present.
"""
from __future__ import annotations

from pathlib import Path

from .harness import run_spec
from .registry import PATTERNS, load_all_patterns
from .report import Report, Status

# Flow group -> required patterns (SPEC §6), restricted to the P0 spine we cover.
FLOW_REQUIRED = {
    "Target Schema": {"epoch_conversion", "schema_conformance"},
    "Extract-Load": {"decimal_roundtrip", "bulk_load"},
    "Transform": {"rowcount_parity", "aggregate_parity", "fingerprint_parity",
                  "epoch_conversion", "scd2_continuity", "merge_idempotency", "fk_orphan"},
    "Consumer Migration": {"query_parity"},
    "Egress": {"egress_parity"},
    "Historical Backfill": {"rowcount_parity", "aggregate_parity", "fingerprint_parity",
                            "epoch_conversion", "scd2_continuity", "fk_orphan"},
}


def discover_golden(root: str | Path = "tests") -> list[Path]:
    """All golden MVS specs (excludes *_negative twins). Note: a .mvs.yaml file's
    Path.stem is 'name.mvs', so match the substring, not endswith."""
    return sorted(p for p in Path(root).rglob("*.mvs.yaml") if "_negative" not in p.name)


def reconcile(paths: list[Path]) -> Report:
    """Run every spec; merge all suites into one sign-off Report."""
    signoff = Report(spec_name="end-to-end-signoff")
    for p in paths:
        rep = run_spec(p)
        for s in rep.suites:
            s.suite_id = f"{p.stem}:{s.suite_id}"
            signoff.suites.append(s)
    return signoff


def covered_patterns(report: Report) -> set[str]:
    """Patterns that produced at least one PASS check in the report."""
    out = set()
    for s in report.suites:
        if any(c.status == Status.PASS for c in s.checks):
            out.add(s.pattern)
    return out


# Patterns proven by unit tests (against real captured output + stubs) rather than a
# live golden in the meta sandbox, because their live golden needs external infra the
# sandbox doesn't run (e.g. dag_structure needs a real Cloud Composer). Exempt from the
# live-coverage gate; see tests/orchestration/test_dag.py + examples/.
_COVERAGE_EXEMPT = {"dag_structure"}


def coverage_gaps(report: Report) -> dict:
    """Return {missing_patterns, flow_gaps} — empty means full coverage."""
    load_all_patterns()
    covered = covered_patterns(report) | _COVERAGE_EXEMPT
    missing = sorted(set(PATTERNS) - covered)
    flow_gaps = {fg: sorted(req - covered) for fg, req in FLOW_REQUIRED.items() if req - covered}
    return {"missing_patterns": missing, "flow_gaps": flow_gaps}
