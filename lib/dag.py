"""Pattern 19 — Airflow / Cloud Composer DAG structure (Axis D, orchestration).

Read-only describe adapter: validates the DAGs deployed in a *real* Composer env
(via ComposerEngine -> `gcloud composer environments run`, which runs inside the env
so the customer's own providers resolve). Asserts the expected DAGs are present and
import-error-free. No seeding, no mutation — safe in read_only mode.

Behavioral validation (trigger a dag run, poll SLA, failure/callback) is a separate
invoke-observe adapter against a live Airflow — not built here.
"""
from __future__ import annotations

from .harness import Context
from .registry import register
from .report import CheckResult, Status, SuiteResult

SCHEMA = {
    "type": "object",
    "required": ["pattern", "expect_dags"],
    "properties": {
        "pattern": {"const": "dag_structure"},
        "id": {"type": "string"}, "story_id": {"type": "string"},
        "expect_dags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "max_import_errors": {"type": "integer", "minimum": 0},
    },
    "additionalProperties": False,
}


@register("dag_structure", SCHEMA)   # read-only: no mutates
def run(suite: dict, ctx: Context) -> SuiteResult:
    sr = SuiteResult(pattern="dag_structure", suite_id=suite.get("id", "dag_structure"))
    story = suite.get("story_id")
    eng = ctx.composer                       # raises ConfigError if COMPOSER_* unset -> ERROR

    deployed = set(eng.list_dags())
    for dag_id in suite["expect_dags"]:
        present = dag_id in deployed
        sr.add(CheckResult(pattern="dag_structure", target=f"dag:{dag_id}",
            status=Status.PASS if present else Status.FAIL, story_id=story,
            message="present" if present else f"missing (deployed: {sorted(deployed)})"))

    max_err = suite.get("max_import_errors", 0)
    errs = eng.import_errors()
    sr.add(CheckResult(pattern="dag_structure", target="import_errors",
        status=Status.PASS if len(errs) <= max_err else Status.FAIL, story_id=story,
        message=("0 import errors" if not errs
                 else f"{len(errs)} import errors (allowed {max_err}): {errs}"),
        metrics={"import_errors": errs}))
    return sr
