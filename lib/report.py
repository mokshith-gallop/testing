"""Result + report objects.

Per SPEC §11 these serialize to the same shape the platform already tracks in
`cuj_validation_results` (per story, per criteria_index, PASS/FAIL/pending) so a
generated test's output drops straight into the existing CUJ-validation flow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Status(str, Enum):
    # No SKIP: a check passes, fails, or errors. Unsupported paths are removed, not
    # skipped; a missing env var errors (fail-fast). See lib/config.require_env.
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return self.value


@dataclass
class CheckResult:
    """One atomic assertion outcome (e.g. one table, one column, one resource)."""
    pattern: str                       # MVS pattern name, e.g. "schema_conformance"
    target: str                        # what was checked, e.g. "nbcs_dm.dim_customer.issued_ts"
    status: Status
    message: str = ""
    expected: Any = None
    actual: Any = None
    metrics: dict[str, Any] = field(default_factory=dict)
    # measurement=True marks a recorded number that did NOT gate (perf measure mode):
    # it's a successful observation, not an assertion — kept out of the pass/fail claim.
    measurement: bool = False
    # Optional platform linkage (SPEC §11).
    story_id: str | None = None
    criteria_index: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == Status.PASS

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = str(self.status)
        return d


@dataclass
class SuiteResult:
    """All checks emitted by one MVS suite."""
    pattern: str
    suite_id: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if any(c.status == Status.ERROR for c in self.checks):
            return Status.ERROR
        if any(c.status == Status.FAIL for c in self.checks):
            return Status.FAIL
        return Status.PASS

    @property
    def ok(self) -> bool:
        return self.status == Status.PASS

    def add(self, c: CheckResult) -> CheckResult:
        self.checks.append(c)
        return c

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "suite_id": self.suite_id,
            "status": str(self.status),
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary(),
        }

    def summary(self) -> dict:
        out = {s.value: 0 for s in Status}
        for c in self.checks:
            out[str(c.status)] += 1
        return out


@dataclass
class Report:
    """Full run report across all suites in an MVS file."""
    spec_name: str = ""
    suites: list[SuiteResult] = field(default_factory=list)

    @property
    def status(self) -> Status:
        if any(s.status == Status.ERROR for s in self.suites):
            return Status.ERROR
        if any(s.status == Status.FAIL for s in self.suites):
            return Status.FAIL
        return Status.PASS

    @property
    def ok(self) -> bool:
        return self.status == Status.PASS

    def to_dict(self) -> dict:
        return {
            "spec_name": self.spec_name,
            "status": str(self.status),
            "suites": [s.to_dict() for s in self.suites],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_cuj_rows(self) -> list[dict]:
        """Flatten to per-criteria rows matching `cuj_validation_results` (SPEC §11)."""
        rows = []
        for s in self.suites:
            for c in s.checks:
                rows.append({
                    "story_id": c.story_id,
                    "criteria_index": c.criteria_index,
                    "pattern": c.pattern,
                    "target": c.target,
                    "status": str(c.status).lower(),
                    "detail": c.message,
                })
        return rows

    def failures(self) -> list[CheckResult]:
        return [c for s in self.suites for c in s.checks if c.status in (Status.FAIL, Status.ERROR)]
