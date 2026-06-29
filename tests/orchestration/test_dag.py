"""Pattern 19 (dag_structure) — parsers verified against REAL captured Composer output
(from a live env this session), and the pattern verified with a stub engine. Offline:
no live Composer needed (the gcloud path was already proven live)."""
from lib.engines import parse_dags_list, parse_import_errors
from lib.dag import run
from lib.harness import Context
from lib.report import Status

# --- real output captured from `gcloud composer environments run ... dags list` ---
REAL_DAGS_LIST = """\
[2026-06-26T08:37:36.494+0000] {plugins.py:37} INFO - setup plugin alembic.autogenerate.schemas
dag_id               | fileloc                                      | owners  | is_paused
=====================+==============================================+=========+==========
airflow_monitoring   | /home/airflow/gcs/dags/airflow_monitoring.py | airflow | False
nbcs_sample_pipeline | /home/airflow/gcs/dags/sample_pipeline.py    | airflow | False
"""

REAL_IMPORT_ERRORS = """\
filepath                             | error
=====================================+=========================================================
/home/airflow/gcs/dags/broken_dag.py | Traceback (most recent call last):
|   File "/home/airflow/gcs/dags/broken_dag.py", line 6, in <module>
|     import nonexistent_module_xyz
| ModuleNotFoundError: No module named 'nonexistent_module_xyz'
|
"""


def test_parse_dags_list_filters_noise_and_builtin():
    assert parse_dags_list(REAL_DAGS_LIST) == ["nbcs_sample_pipeline"]


def test_parse_import_errors_extracts_failing_file_once():
    assert parse_import_errors(REAL_IMPORT_ERRORS) == ["/home/airflow/gcs/dags/broken_dag.py"]


class _StubComposer:
    name = "composer"
    def __init__(self, dags, errors):
        self._dags, self._errors = dags, errors
    def list_dags(self):
        return self._dags
    def import_errors(self):
        return self._errors


def _ctx(stub):
    ctx = Context()
    ctx._cache["composer"] = stub
    return ctx


def test_dag_structure_clean():
    ctx = _ctx(_StubComposer(["nbcs_sample_pipeline"], []))
    sr = run({"pattern": "dag_structure", "expect_dags": ["nbcs_sample_pipeline"],
              "max_import_errors": 0}, ctx)
    assert sr.status == Status.PASS


def test_dag_structure_catches_missing_and_import_errors():
    ctx = _ctx(_StubComposer(["nbcs_sample_pipeline"],
                             ["/home/airflow/gcs/dags/broken_dag.py"]))
    sr = run({"pattern": "dag_structure",
              "expect_dags": ["nbcs_sample_pipeline", "nbcs_not_deployed"],
              "max_import_errors": 0}, ctx)
    assert sr.status == Status.FAIL
    failed = {c.target for c in sr.checks if c.status == Status.FAIL}
    assert "dag:nbcs_not_deployed" in failed       # missing DAG caught
    assert "import_errors" in failed               # broken DAG caught
