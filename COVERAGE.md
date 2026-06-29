# Coverage matrix

Tracks which validation patterns (SPEC §5) have a golden `lib/` module + a green
golden test + a negative twin, proven against the live fixture migration.

Legend: ✅ green on live infra · 🚧 in progress · ⬜ not started

## P0 spine (SPEC §13)

| Order | Item | Patterns | lib module | Golden spec | Negative twin | Status |
|---|---|---|---|---|---|---|
| P0-1 | Schema/DDL conformance | 7 | `lib/schema.py` | `tests/schema/schema_conformance.mvs.yaml` | ✅ | ✅ |
| P0-2 | Bulk load + row-count parity | 14, 1 | `lib/parity.py`, `lib/bqload.py` | `tests/parity/rowcount_parity.mvs.yaml` | ✅ | ✅ |
| P0-3 | Encoding: epoch + DECIMAL | 5, 6 | `lib/epoch.py` | `tests/encoding/encoding.mvs.yaml` | ✅ | ✅ |
| P0-4 | Aggregate + fingerprint parity | 2, 3 | `lib/parity.py` | `tests/parity/value_parity.mvs.yaml` | ✅ | ✅ |
| P0-5 | SCD-2 + MERGE + FK orphan | 8, 9, 10 | `lib/scd2.py`, `lib/merge.py`, `lib/fk.py` | `tests/transform/transform.mvs.yaml` | ✅ | ✅ |
| P0-6 | Query/view parity | 4 | `lib/parity.py` | `tests/query/query_parity.mvs.yaml` | ✅ | ✅ |
| P0-7 | Egress parity | 16 | `lib/egress.py` | `tests/egress/egress.mvs.yaml` | ✅ | ✅ |
| P0-8 | End-to-end reconcile + sign-off | 1–10 | `lib/reconcile.py` | `tests/reconcile/test_signoff.py` | ✅ | ✅ |

## Flow group → required patterns (SPEC §6)

CI asserts every pattern has ≥1 green golden test, and every flow group's required
patterns are present. (Wired up in P0-8 / `ci/run-meta-validation.sh`.)

| Flow group | Required patterns | Covered |
|---|---|---|
| Target Schema | 5, 7 | 5, 7 |
| Transform | 1, 2, 3, 5, 8, 9, 10 | 1,2,3,5,8,9,10 |
| Extract-Load | 6, 12, 13, 14, 15 | 14 |
| Egress | 15, 16 | 16 |
| Consumer Migration | 4 | 4 |
| Historical Backfill | 1, 2, 3, 5, 8, 10 | 1,2,3,5,8,10 |

(High/Low-tier patterns — 11, 12, 13, 15, 17–21 — are deferred per SPEC §13.)

## Beyond the P0 spec (added this iteration)

| Capability | Module | Test | Status |
|---|---|---|---|
| BigQuery→BigQuery in-warehouse transform validation | `lib/harness.py` (engine-agnostic source) | `tests/bq_to_bq/` | ✅ |
| TABLE_OPTIONS (retention / require-filter / labels) | `lib/schema.py` | `tests/schema/` | ✅ |
| Scale: in-warehouse SQL-pushdown parity (no egress), cross-engine | `lib/parity.py` (`scale: pushdown`) | `tests/parity/`, `tests/bq_to_bq/` | ✅ |
| Scale: smart-diff localization (segmented digest → differing keys) | `lib/parity.py` (`_localize_pushdown`) | `tests/parity/value_parity_negative` | ✅ |
| Query performance — measure / assert / compare (A-vs-B) / regression modes (BigQuery Job-API + Impala runtime profile) | `lib/perf.py` (`query_performance`) | `tests/perf/` | ✅ |
| Synthetic large-data generator (in-BQ GENERATE_ARRAY, scale-tiered, auto-expiry) | `lib/synth.py` | `tests/perf/test_synth.py` | ✅ |
| Composer/Airflow DAG validation (real env via `gcloud composer run`) | `lib/dag.py` (`dag_structure`) + `ComposerEngine` | `tests/orchestration/test_dag.py` + live | ✅ |
| Read-only / no-seed mode (safe against real envs; blocks mutating patterns) | `lib/harness.py` (`read_only`) + registry `mutates` | `tests/test_readonly.py`, `examples/assessment_readonly.mvs.yaml` | ✅ |
| Mode-2 build-and-verify (apply CUT artifacts to a clean build dataset, then verify; guarded reset+teardown) | `lib/build.py` + `lib/harness.py` (`migration`) | `tests/build/`, `tests/test_build_guard.py` | ✅ |
| ELT transform unit tier (`given`→T→`expect`, hermetic BQ, canonicalized rows + property asserts) | `lib/transform_unit.py` | `tests/transform_unit/` (golden + negative) | ✅ |
| Cross-engine declarative seeder (`given` → tables in BOTH source HS2 + dest BQ; no Python loader) | `lib/build.py` (`seed_given`) | exercised by `tests/transform_diff/` | ✅ |
| Transform equivalence — `transform_diff` (same `given` → legacy T on Impala vs migrated T on BQ; legacy = oracle) | `lib/transform_diff.py` | `tests/transform_diff/` (golden + negative) | ✅ |

