"""Synthetic BigQuery data generator: generates a partitioned/clustered table
in-warehouse and (bonus) drives a perf measure over it. Uses a modest PERF_SCALE so
the test stays fast + ~free; the same spec scales to 1e8/1e9 by setting PERF_SCALE."""
import pathlib

import pytest

HERE = pathlib.Path(__file__).parent


@pytest.mark.live_bq
def test_synth_generates_partitioned_table(bq_engine, monkeypatch):
    monkeypatch.setenv("PERF_SCALE", "50000")          # fast + free for the test
    from lib.synth import load
    from lib import config as cfg

    out = load(str(HERE / "synth.spec.yaml"))
    assert out == [("perf_fact", 50000)]

    ds = cfg.require_env("BQ_DATASET_2")
    # exactly the requested rows landed
    n = bq_engine.scalar(f"SELECT COUNT(*) AS n FROM {bq_engine.qualify(ds, 'perf_fact')}")
    assert int(n) == 50000
    # partitioning + clustering applied
    info = bq_engine.introspect_table(ds, "perf_fact")
    assert info.partition_columns == ["event_date"]
    assert info.cluster_columns == ["customer_id"]


@pytest.mark.live_bq
def test_perf_over_synthetic_table(run_spec_file, bq_engine, monkeypatch):
    # generate, then prove a perf measure/assert runs against the generated table
    monkeypatch.setenv("PERF_SCALE", "50000")
    from lib.synth import load
    load(str(HERE / "synth.spec.yaml"))
    report = run_spec_file(HERE / "perf_synth.mvs.yaml")
    from lib.report import Status
    assert report.status == Status.PASS, "failures: " + "; ".join(
        f"{c.target}: {c.message}" for c in report.failures())
