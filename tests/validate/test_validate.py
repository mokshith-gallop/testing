"""`validate_spec` / `lib.cli validate` — schema-lint a spec WITHOUT a DB, so the agent catches
authoring errors (missing required fields, unexpected fields, unknown patterns, bad YAML) at
authoring time instead of at run time. Offline — no live engines.
"""
import pathlib

from lib.harness import validate_spec

HERE = pathlib.Path(__file__).parent
BAD_PERF = HERE / "bad_query_performance_negative.mvs.yaml"
GOOD = HERE.parent / "source_setup" / "source_setup.mvs.yaml"


def test_validate_passes_a_good_spec():
    assert validate_spec(GOOD) == []


def test_validate_flags_query_performance_schema_errors():
    # AC9 class: query_performance `assert` query missing `thresholds` + a non-existent `expect_error`
    blob = " ".join(validate_spec(BAD_PERF))
    assert "thresholds" in blob          # required field missing
    assert "expect_error" in blob        # unexpected field


def test_validate_reports_bad_yaml_without_crashing(tmp_path):
    # AC7 class: an UNQUOTED complex type breaks YAML — validate must REPORT it, never raise
    f = tmp_path / "bad.mvs.yaml"
    f.write_text("name: x\nsuites:\n  - pattern: schema_conformance\n"
                 "    tables: [ { table: t, columns: [ { name: c, type: STRUCT< bad ] } ] }\n")
    problems = validate_spec(f)
    assert problems and any("Error" in p for p in problems)   # surfaced, not crashed


def test_validate_flags_unknown_pattern(tmp_path):
    f = tmp_path / "unk.mvs.yaml"
    f.write_text("name: x\nconnections: { target: { engine: bigquery } }\n"
                 "suites:\n  - { pattern: not_a_pattern, id: u }\n")
    assert any("unknown pattern" in p for p in validate_spec(f))
