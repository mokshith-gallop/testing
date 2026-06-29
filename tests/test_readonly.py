"""read_only mode: a spec marked read_only is safe against a real (production) env —
the harness BLOCKS any mutating pattern (seed/load/merge/export). Offline."""
from lib.harness import run_mvs
from lib.report import Status


def test_readonly_blocks_a_mutating_pattern():
    report = run_mvs({
        "name": "ro", "read_only": True,
        "suites": [{"pattern": "bulk_load", "target_dataset": "x",
                    "loads": [{"table": "t", "uri": "gs://b/*", "format": "CSV",
                               "expected_count": 1}]}],
    })
    assert report.status == Status.ERROR
    assert "mutates" in report.suites[0].checks[0].message


def test_readonly_allows_a_readonly_pattern(monkeypatch):
    # dag_structure is read-only -> NOT blocked by the guard; it then errors only
    # because COMPOSER_* is unset (a config error), proving the guard let it through.
    monkeypatch.delenv("COMPOSER_ENV", raising=False)
    report = run_mvs({
        "name": "ro", "read_only": True,
        "suites": [{"pattern": "dag_structure", "expect_dags": ["x"]}],
    })
    assert "mutates" not in report.suites[0].checks[0].message
