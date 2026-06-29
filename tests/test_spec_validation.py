"""OFFLINE: every committed .mvs.yaml (tests/ + examples/) loads and validates against
its envelope AND each suite against its pattern's JSON Schema. Catches malformed specs
(incl. the reference bundle + examples) without touching any engine."""
import pathlib

import pytest
from jsonschema import Draft202012Validator

from lib.mvs import load_mvs
from lib.registry import PATTERNS, load_all_patterns

# Placeholder env so ${VAR} interpolation resolves (we validate shape, not values).
_ENV = {
    "GCP_PROJECT": "proj", "BQ_LOCATION": "US",
    "BQ_DATASET_1": "bq_1", "BQ_DATASET_2": "bq_2", "BUILD_DATASET": "dmt_build",
    "SOURCE_DATABASE": "src_db", "SANDBOX_BUCKET": "bucket",
    "PERF_BASELINE": ".report/perf_baseline.json", "PERF_SCALE": "small",
}

SPECS = sorted(str(p) for root in ("tests", "examples")
               for p in pathlib.Path(root).rglob("*.mvs.yaml")
               if "validate" not in p.parts)   # tests/validate/ holds INTENTIONALLY-invalid fixtures (the `lib.cli validate` linter's negatives)


@pytest.mark.parametrize("spec_path", SPECS)
def test_spec_validates(spec_path, monkeypatch):
    for k, v in _ENV.items():
        monkeypatch.setenv(k, v)
    load_all_patterns()
    data = load_mvs(spec_path)                       # validates the envelope (raises on bad)
    for i, suite in enumerate(data.get("suites", [])):
        pat = suite.get("pattern")
        assert pat in PATTERNS, f"{spec_path} suite[{i}]: unknown pattern {pat!r}"
        errs = sorted(Draft202012Validator(PATTERNS[pat].schema).iter_errors(suite),
                      key=lambda e: list(e.path))
        assert not errs, f"{spec_path} [{pat}]: " + "; ".join(
            f"{'/'.join(map(str, e.path)) or '<suite>'}: {e.message}" for e in errs[:5])


def test_specs_were_discovered():
    assert len(SPECS) >= 10, f"expected to find committed specs, found {SPECS}"
