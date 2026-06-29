# Locked Decisions for Story 3b186a25-6c27-4167-ba46-9743c56c1141

## Implementation Approach
## DDL Authoring for 100 BigQuery Tables across 3 Datasets

### Deliverables
**8 BigQuery DDL SQL files** mirroring the source HQL structure, plus 1 dataset-creation file:

| File | Content | Tables |
|------|---------|--------|
| `sql/ddl/01-create-datasets.sql` | `CREATE SCHEMA IF NOT EXISTS` for `nbcs_staging`, `nbcs_ods`, `nbcs_dm` | — |
| `sql/ddl/02-staging-sqoop-mirrors.sql` | 27 Sqoop mirror tables | 27 |
| `sql/ddl/03-staging-delta-feeds.sql` | 8 CDC delta feed tables | 8 |
| `sql/ddl/04-staging-file-feeds.sql` | 10 SFTP/file feed tables | 10 |
| `sql/ddl/05-ods-cleanse.sql` | 15 ODS cleansed entities | 15 |
| `sql/ddl/06-ods-delta-scd2.sql` | 8 delta-merged + 3 SCD-2 tables | 11 |
| `sql/ddl/07-ods-acid.sql` | 4 ACID-to-standard conversions | 4 |
| `sql/ddl/08-dm-tables.sql` | 9 dims + 9 facts + 7 aggregates | 25 |

File `09-dm-views.sql` is **out of scope** — the 15 analyst-facing views are authored by the Transform flow (AC #3 confirms none are silently materialized as tables).

All DDL uses **unqualified table names** (no dataset prefix) so the harness can redirect to the ephemeral build dataset via default-dataset. For production deployment, each file targets its respective dataset.

### Type Mapping Rules (per locked Schema and Data Type Mapping decision)

| Hive Type | BigQuery Type |
|-----------|--------------|
| `BIGINT` | `INT64` |
| `INT` / `SMALLINT` | `INT64` |
| `STRING` | `STRING` |
| `BOOLEAN` | `BOOL` |
| `DOUBLE` | `FLOAT64` |
| `DECIMAL(p,s)` | `NUMERIC(p,s)` — all 52 DECIMAL columns have p≤38 |
| `TIMESTAMP` | `TIMESTAMP` |
| `DATE` | `DATE` |
| `ARRAY<STRUCT<...>>` | `ARRAY<STRUCT<...>>` — INT inside STRUCT becomes INT64 |
| `MAP<STRING,STRING>` | `ARRAY<STRUCT<key STRING, value STRING>>` |

### Partition Column Handling

Hive partition columns are virtual (outside the column list). In BigQuery they must be real columns in the DDL:

**STRING to DATE cast partitions** (Sqoop mirrors, ODS cleanses):
- `load_date STRING` becomes `load_date DATE` — partition column type changes from STRING to DATE
- `snapshot_date STRING`, `event_date STRING`, `sched_date STRING`, `call_date STRING` all cast to `DATE`

**STRING month to DATE partitions** (ODS delta-merged, DM aggs):
- `work_month STRING`, `period_month STRING`, `swap_month STRING`, `event_month STRING` become `DATE` (first-of-month values like `2024-01-01`)

**Multi-column partition collapse** (3 affected patterns):
- `stg_wfm_schedule (load_date, site_code)` — partition on `load_date DATE` only; `site_code STRING` becomes a regular column + clustering column
- 10 `stg_file_*` tables `(client_code, feed_date)` — partition on `feed_date DATE`; `client_code STRING` becomes a regular column + clustering column
- `fact_interaction (date_key, channel)` — RANGE partition on `date_key` only; `channel STRING` becomes first clustering column

**Delta feed partitions** (8 tables):
- `extract_ts STRING` becomes `extract_ts DATE` — partition column cast to DATE

### RANGE Partition Bounds

| Partition Column | Tables | Start | End | Interval |
|-----------------|--------|-------|-----|----------|
| `date_key` (YYYYMMDD) | 7 facts + 4 aggs | 20150101 | 20351231 | 1 |
| `week_start_key` (YYYYMMDD) | `agg_agent_weekly` | 20150101 | 20351231 | 1 |
| `eff_from_year` | 3 SCD-2 tables | 2010 | 2040 | 1 |

### Clustering Configuration (per locked decision + resolved discrepancies)

**6 Named clustering configs:**

| Table | Clustering Columns | Notes |
|-------|-------------------|-------|
| `fact_interaction` | `channel, agent_sk, client_sk` | `channel` demoted from 2nd partition column |
| `fact_agent_activity` | `agent_sk` | **Single column** — locked spec's `program_sk` ABSENT from source DDL; cluster on existing only |
| `fact_billing_line` | `client_sk, program_sk, invoice_status` | Per locked spec |
| `fact_queue_interval` | `queue_sk` | **Single column** — locked spec's `site_code` ABSENT from source DDL; cluster on existing only |
| `agg_agent_daily` | `agent_sk, site_code` | Per locked spec |
| `agg_billing_monthly` | `client_sk, program_sk` | Per locked spec |

**45 staging tables**: clustered by PK column(s) per locked spec.

**9 dimensions, 4 ACID tables**: no partition, no clustering.

**Hive directives dropped:**
- 6 `CLUSTERED BY ... INTO N BUCKETS` directives dropped (stg_tel_call, fact_interaction, ods_client_acid, ods_agent_acid, ods_ticket_acid, ods_invoice_acid)
- All `STORED AS`, `LOCATION`, `TBLPROPERTIES`, `ROW FORMAT`, `SERDE` directives dropped (BigQuery managed tables)
- All `EXTERNAL` keywords dropped

### Column Descriptions

71 source COMMENT annotations (all in staging DDL) carried as BigQuery column `OPTIONS(description=...)`:

- Epoch columns carry their encoding: `'epoch SECONDS (legacy)'` or `'epoch MILLISECONDS (legacy)'`
- `stg_fin_invoice.issued_ts_sec` and `due_ts_sec` descriptions MUST include: `'name says seconds, VALUES ARE MILLIS'` per `docs/EPOCH-POLICY.md`
- Oracle string columns (contract dates): `'Oracle string YYYYMMDDHH24MISS (legacy)'`

### 4 Complex/Nested Column Specs

1. `stg_file_qa_forms.sections` — `ARRAY<STRUCT<section_code STRING, max_points INT64, scored_points INT64>>` (INT to INT64 inside STRUCT)
2. `stg_file_chat_transcripts.messages` — `ARRAY<STRUCT<sender STRING, ts_ms INT64, text STRING>>` (BIGINT to INT64 inside STRUCT)
3. `stg_file_chat_transcripts.metadata` — `ARRAY<STRUCT<key STRING, value STRING>>` (MAP converted)
4. `stg_file_speech_analytics.keywords` — `ARRAY<STRING>` (REPEATED STRING, no sub-fields)

### DDL Template Patterns

**Standard partitioned table (staging):**
```sql
CREATE TABLE IF NOT EXISTS stg_crm_client (
  client_id    INT64 OPTIONS(description='pk'),
  client_code  STRING,
  created_ts   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  updated_ts   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date    DATE
)
PARTITION BY load_date
CLUSTER BY client_id;
```

**RANGE partitioned fact table:**
```sql
CREATE TABLE IF NOT EXISTS fact_interaction (
  interaction_id STRING,
  client_sk      INT64,
  agent_sk       INT64,
  channel        STRING,
  date_key       INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20351231, 1))
CLUSTER BY channel, agent_sk, client_sk;
```

**Unpartitioned dimension:**
```sql
CREATE TABLE IF NOT EXISTS dim_agent (
  agent_sk         INT64,
  agent_id         INT64,
  full_name        STRING,
  is_current       BOOL
);
```

**SCD-2 with yearly RANGE:**
```sql
CREATE TABLE IF NOT EXISTS ods_agent_scd2 (
  agent_history_id STRING,
  agent_id         INT64,
  eff_from_ts      TIMESTAMP,
  eff_to_ts        TIMESTAMP,
  is_current       BOOL,
  eff_from_year    INT64
)
PARTITION BY RANGE_BUCKET(eff_from_year, GENERATE_ARRAY(2010, 2040, 1));
```

**Collapsed multi-column partition (file feeds):**
```sql
CREATE TABLE IF NOT EXISTS stg_file_interaction_export (
  interaction_ref  STRING,
  channel          STRING,
  start_ms         INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  end_ms           INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  client_code      STRING,
  feed_date        DATE
)
PARTITION BY feed_date
CLUSTER BY interaction_ref, client_code;
```

### Source Cross-Check via source_setup

The MVS spec will include a `source_setup` block pointing at the 8 legacy HQL files (01-08). The harness applies these to stand up the legacy schema on a sandbox Impala/Hive, then cross-checks each BigQuery column's type against the live legacy type — ensuring the type mapping is verified from ground truth, not re-typed expectations.

## Validation
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
