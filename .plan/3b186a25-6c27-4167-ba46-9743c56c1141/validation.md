# Validation

## Validation Strategy — 9 Acceptance Criteria via MVS Specs

### MVS Spec Structure

Three MVS spec files, each covering a subset of the 9 ACs, all using Mode-2 build-and-verify:

| MVS Spec File | ACs Covered | Patterns Used |
|---------------|-------------|---------------|
| `specs/schema_staging.mvs.yaml` | AC 1-4, 7-8 (staging layer) | `schema_conformance` |
| `specs/schema_ods_dm.mvs.yaml` | AC 1-4, 7-8 (ods + dm layers) | `schema_conformance` |
| `specs/schema_integration.mvs.yaml` | AC 5-6, 9 | `schema_conformance` (FK checks), custom SQL queries, `query_performance` |

All three share a common `migration:` block and `source_setup:` block.

### migration block — applies DDL verbatim

```yaml
migration:
  steps:
    - { kind: ddl, sql: sql/ddl/01-create-datasets.sql }
    - { kind: ddl, sql: sql/ddl/02-staging-sqoop-mirrors.sql }
    - { kind: ddl, sql: sql/ddl/03-staging-delta-feeds.sql }
    - { kind: ddl, sql: sql/ddl/04-staging-file-feeds.sql }
    - { kind: ddl, sql: sql/ddl/05-ods-cleanse.sql }
    - { kind: ddl, sql: sql/ddl/06-ods-delta-scd2.sql }
    - { kind: ddl, sql: sql/ddl/07-ods-acid.sql }
    - { kind: ddl, sql: sql/ddl/08-dm-tables.sql }
```

### source_setup block — stands up legacy for cross-check

```yaml
source_setup:
  location_base: ${SOURCE_WAREHOUSE:-/tmp/dmt_src}
  ddl:
    - sql/legacy/01-create-databases.hql
    - sql/legacy/02-staging-sqoop-mirrors.hql
    - sql/legacy/03-staging-delta-feeds.hql
    - sql/legacy/04-staging-file-feeds.hql
    - sql/legacy/05-ods-cleanse.hql
    - sql/legacy/06-ods-delta-scd2.hql
    - sql/legacy/07-ods-acid.hql
    - sql/legacy/08-dm-tables.hql
```

The legacy HQL files are copied verbatim from `hive/ddl/` into `sql/legacy/` for the harness. The harness rewrites HDFS LOCATIONs and strips ACID properties automatically.

### AC-by-AC Validation Mapping

**AC 1 — Table creation, 0 DDL errors across 100 tables in 3 datasets:**
- The `migration:` block applies all DDL verbatim. Any DDL error aborts the harness run as a HARD FAIL with the BigQuery error message.
- `schema_conformance` suite asserts `expect_table_count: 45` for staging, `30` for ods, `25` for dm (total=100).

**AC 2 — Per-column fidelity (921 columns):**
- Each `schema_conformance` suite declares every column with `name`, `type`, `source_type`, `source_name`, and `description` where applicable.
- `source_database` + `source_table` fields anchor each check to the LIVE legacy schema (via `source_setup`), not re-typed expectations.
- Precision/scale checked: every `NUMERIC(p,s)` column declares exact type like `NUMERIC(14,2)`.
- Nested columns checked via sub-field type declarations in the `type` string (the harness parses `ARRAY<STRUCT<...>>` recursively).
- Column `description` field asserts the 71 BigQuery descriptions match, including the `issued_ts_sec` / `due_ts_sec` lie documentation.

**AC 3 — Object-type fidelity (all BASE TABLE, no silent view flips):**
- Every table entry includes `expect_object_type: TABLE`.
- A separate negative assertion confirms none of the 15 view names (`vw_org_hierarchy`, `vw_active_agents_ndv`, etc.) exist as BASE TABLEs in the build dataset.
- The DDL files contain no CREATE VIEW statements.

**AC 4 — Partition + cluster + key intent matches locked matrix:**
- Every table entry includes `partition_by` and `cluster_by` fields matching the locked Performance Optimization decision.
- Specific assertions:
  - 26 Sqoop mirrors: `partition_by: "load_date"` (DATE)
  - 1 stg_wfm_schedule: `partition_by: "load_date"`, `cluster_by: [schedule_id, site_code]`
  - 8 delta feeds: `partition_by: "extract_ts"` (DATE)
  - 10 file feeds: `partition_by: "feed_date"` (DATE), clustering includes `client_code`
  - 15 ODS cleanses: `partition_by` on respective date column (DATE)
  - 8 ODS delta-merged: `partition_by` on month/date column (DATE)
  - 3 SCD-2: RANGE partition on eff_from_year
  - 4 ACID tables: no partition, no cluster
  - 9 dimensions: no partition
  - Fact/agg: RANGE partition on date_key/week_start_key
  - 6 named clustering configs as decided (including the 2 single-column resolutions)

**AC 5 — Cross-dataset FK/PK type consistency:**
- A dedicated suite with `schema_conformance` entries for every FK column across all 3 datasets, asserting identical types.
- Key join paths verified: `agent_id` (INT64 in staging, ods, dm), `client_id`, `program_id`, `queue_id`, `ticket_id`, `invoice_id`, `shift_id`, `contract_id` — all INT64 everywhere.
- Surrogate keys: `agent_sk`, `client_sk`, `program_sk`, `queue_sk`, `disposition_sk` — all INT64 in every fact/agg/dim.
- `disposition_code` (STRING) cross-checked between `ods.ods_call` and `dm.dim_disposition`.
- `interaction_id` / `interaction_ref` (STRING) cross-checked between `ods.ods_interaction` and `dm.fact_interaction`.

**AC 6 — Queryability smoke tests:**
- `SELECT * FROM table LIMIT 0` for each of 100 tables — executed as inline SQL queries in the MVS spec.
- 3 cross-dataset representative queries executed as `sql_text` assertions:
  1. staging-to-ods: `stg_fin_invoice JOIN ods_invoice_acid ON invoice_id`
  2. ods-to-dm: `ods_interaction JOIN fact_interaction ON interaction_id`
  3. dm fact-to-dim: `fact_interaction JOIN dim_agent, dim_program, dim_queue`
- These run against the build dataset after DDL apply. They succeed on empty tables (LIMIT 0) — the point is zero type-coercion errors.

**AC 7 — Integrity guards (no silent failures):**
- Mode-2 build-and-verify inherently guards against this: DDL errors abort the run; INFORMATION_SCHEMA reads that return 0 rows for an expected table are flagged as HARD FAIL by `schema_conformance`.
- `expect_table_count` prevents false-pass-by-absence.
- Column count per table is checked: fewer columns than expected is a FAIL, never treated as columns match by absence.

**AC 8 — No-silent-skip (all checks against LIVE BigQuery):**
- Mode-2 build-and-verify guarantees all checks read INFORMATION_SCHEMA and execute queries on the LIVE scratch BigQuery datasets. The harness rejects offline or parse-only validation.
- State coverage is reported by the harness for each criterion.

**AC 9 — Physical-access performance (partition/cluster pruning with sample data):**
- A separate MVS spec loads sample data from `data/parquet/` and `data/text/` into the build dataset via `migration.steps` of `kind: load`.
- Uses the `query_performance` pattern (from `lib/perf.py`) to measure `bytes_scanned` from BigQuery job statistics.
- Benchmark queries for minimum 9 hot-path objects:
  - `fact_interaction`: `WHERE date_key = 20240115` vs unfiltered — assert partition pruning
  - `fact_billing_line`: partition-filtered on `period_month`
  - `agg_agent_daily`: cluster-filtered `WHERE agent_sk = <value>`
  - `agg_billing_monthly`: partition-filtered
  - 3 SCD-2 tables: `WHERE eff_from_year = 2024` — RANGE pruning
  - Additional fact tables as needed to reach 6+ named clustered benchmarks
- All `bytes_scanned` figures from BigQuery Job API statistics — never invented.

### Edge Cases and Error Handling

- **Reserved words**: No source column names collide with BigQuery reserved words (verified — names like `date_key`, `status`, `type` are NOT reserved in BigQuery Standard SQL).
- **Nullability**: BigQuery columns are NULLABLE by default, matching Hive default. No NOT NULL in source DDL.
- **Empty datasets**: Harness creates datasets before DDL. Dataset creation failure is HARD FAIL.
- **Idempotency**: All DDL uses `CREATE TABLE IF NOT EXISTS`. Harness always starts from clean slate.
