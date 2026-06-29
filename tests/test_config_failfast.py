"""Lock the fail-fast env contract: a missing required var FAILS (never skips, never
silently defaults). Offline — no live infra.
"""
import pytest

from lib import config as cfg
from lib.harness import run_mvs
from lib.report import Status


def test_require_env_missing_raises(monkeypatch):
    monkeypatch.delenv("DMT_NOT_SET", raising=False)
    with pytest.raises(cfg.ConfigError):
        cfg.require_env("DMT_NOT_SET")


def test_missing_required_var_fails_suite_does_not_skip(monkeypatch):
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    report = run_mvs({
        "name": "t",
        "connections": {"target": {"engine": "bigquery"}},
        "suites": [{
            "pattern": "schema_conformance", "target_dataset": "x",
            "tables": [{"table": "t", "columns": [{"name": "c", "type": "INT64"}]}],
        }],
    })
    assert report.status == Status.ERROR        # not SKIP, not PASS
    assert "GCP_PROJECT" in report.suites[0].checks[0].message
