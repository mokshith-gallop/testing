"""build-and-verify guard — OFFLINE (no BQ). The reset/teardown guard must refuse any
dataset name that isn't a build dataset, so a misconfigured spec can never wipe a real
target. Also: read_only + migration is a contradiction the harness rejects."""
import pytest

from lib import build
from lib.harness import run_mvs
from lib.report import Status


@pytest.mark.parametrize("bad", ["prod_dm", "ods", "dmt_buildx", "analytics"])
def test_guard_rejects_non_build_names(bad):
    with pytest.raises(build.BuildGuardError):
        build._guard(bad)


@pytest.mark.parametrize("ok", ["dmt_build", "dmt_build_unit", "dmt_build_abc123"])
def test_guard_allows_build_names(ok):
    build._guard(ok)        # must not raise


def test_readonly_plus_migration_is_rejected():
    report = run_mvs({
        "name": "contradiction", "read_only": True,
        "migration": {"steps": [{"kind": "ddl", "sql_text": "SELECT 1"}]},
        "suites": [{"pattern": "schema_conformance", "target_dataset": "x", "tables": []}],
    })
    assert report.status == Status.ERROR
    assert "read_only" in report.suites[0].checks[0].message
