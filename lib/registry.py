"""Pattern registry. Each lib/<pattern>.py registers a runner + a JSON Schema for
its MVS slice. The harness validates a suite against the matching schema (loud
failure on bad input, SPEC §4.1) and dispatches to the runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from .harness import Context
    from .report import SuiteResult

Runner = Callable[[dict, "Context"], "SuiteResult"]


@dataclass
class PatternSpec:
    name: str
    schema: dict
    runner: Runner
    # mutates=True: the pattern creates/loads tables, runs MERGE/EXPORT, triggers runs,
    # etc. Such patterns need a sandbox/seed and are BLOCKED in read_only mode so a spec
    # can be pointed safely at a real (production) environment. See harness read_only.
    mutates: bool = False


PATTERNS: dict[str, PatternSpec] = {}


def register(name: str, schema: dict, mutates: bool = False):
    """Decorator: register a pattern runner under `name` with its MVS JSON Schema."""
    def deco(func: Runner) -> Runner:
        if name in PATTERNS:
            raise ValueError(f"pattern already registered: {name}")
        PATTERNS[name] = PatternSpec(name=name, schema=schema, runner=func, mutates=mutates)
        return func
    return deco


def load_all_patterns() -> None:
    """Import every pattern module so it self-registers. Import-time side effect only."""
    from . import (schema, parity, bqload, epoch, scd2, merge, fk, egress, perf, dag,  # noqa: F401
                   transform_unit, transform_diff)
