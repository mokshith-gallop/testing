"""Session fixtures: engine clients (fail-fast on missing env).

Every live test depends on `bq_engine` / `impala_engine` / `hive_engine`; a missing
env var raises ConfigError and the test FAILS (it does not skip). Run a subset by
selecting tests — each fixture asserts only the vars its engine uses.
"""
from __future__ import annotations

import pytest

from lib import config as cfg
from lib.engines import BigQueryEngine, HiveEngine, ImpalaEngine
from lib.harness import run_mvs, run_spec
from lib.mvs import load_mvs


@pytest.fixture(scope="session")
def bq_cfg():
    # Fail-fast: a missing env var raises ConfigError (the test fails, not skips).
    # To run only the BigQuery tests, select them by path/-k; they assert just the
    # vars they use.
    return cfg.BigQueryConfig.from_env()


@pytest.fixture(scope="session")
def impala_cfg():
    return cfg.ImpalaConfig.from_env()


@pytest.fixture(scope="session")
def bq_engine(bq_cfg):
    return BigQueryEngine(bq_cfg)


@pytest.fixture(scope="session")
def impala_engine(impala_cfg):
    return ImpalaEngine(impala_cfg)


@pytest.fixture(scope="session")
def hive_engine(impala_cfg):
    return HiveEngine(impala_cfg)


@pytest.fixture
def run_spec_file():
    """Return a callable: path -> Report. Used by golden-spec tests."""
    return run_spec
