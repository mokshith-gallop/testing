"""`dmtemplate run <spec.yaml>` — run an MVS and print the JSON report.

Exit code 0 if the report passed, 1 if any suite failed/errored. Lets CI and the
execution agent invoke the harness without writing Python.
"""
from __future__ import annotations

import argparse
import sys

from .harness import run_spec, validate_spec
from .registry import PATTERNS, load_all_patterns


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dmtemplate")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run an MVS spec file")
    r.add_argument("spec")
    r.add_argument("--quiet", action="store_true", help="print only the final status line")

    v = sub.add_parser("validate", help="schema-validate an MVS spec WITHOUT running it (no DB)")
    v.add_argument("spec")

    sub.add_parser("patterns", help="list registered patterns")

    rec = sub.add_parser("reconcile", help="run all golden specs -> one sign-off report")
    rec.add_argument("--specs-dir", default="tests")
    rec.add_argument("--quiet", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "patterns":
        load_all_patterns()
        for name in sorted(PATTERNS):
            print(name)
        return 0

    if args.cmd == "run":
        report = run_spec(args.spec)
        if not args.quiet:
            print(report.to_json())
        print(f"[{report.status}] {report.spec_name}: "
              f"{sum(len(s.checks) for s in report.suites)} checks across {len(report.suites)} suites",
              file=sys.stderr)
        return 0 if report.ok else 1

    if args.cmd == "validate":
        problems = validate_spec(args.spec)
        if problems:
            print(f"[INVALID] {args.spec}", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(f"[OK] {args.spec}: valid (schema-checked, not run)", file=sys.stderr)
        return 0

    if args.cmd == "reconcile":
        from .reconcile import coverage_gaps, discover_golden, reconcile
        paths = discover_golden(args.specs_dir)
        report = reconcile(paths)
        gaps = coverage_gaps(report)
        if not args.quiet:
            print(report.to_json())
        all_checks = [c for s in report.suites for c in s.checks]
        n_meas = sum(1 for c in all_checks if c.measurement)
        n_assert = len(all_checks) - n_meas
        print(f"[{report.status}] sign-off: {len(paths)} specs, {len(report.suites)} suites, "
              f"{n_assert} assertions ({n_meas} measurements)", file=sys.stderr)
        if gaps["missing_patterns"] or gaps["flow_gaps"]:
            print(f"COVERAGE GAPS: {gaps}", file=sys.stderr)
        return 0 if (report.ok and not gaps["missing_patterns"] and not gaps["flow_gaps"]) else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
