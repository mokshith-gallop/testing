# Data-Migration Test Template Repository — Specification

**Status:** Draft for review
**Author:** generated from project `3dc1722c` (NBCS CDH → GCP migration)
**Date:** 2026-06-24

---

## 1. Problem & Goal

When the platform generates user stories for a data-migration project and an execution
agent works the acceptance criteria, **the agent writes a lot of weak/flaky validation
tests and we end up fixing them by hand.** The acceptance criteria themselves are
precise (row-count parity, fingerprint parity, epoch correctness, IAM allow/deny
matrices, Terraform idempotency, DAG topology, etc.) — but there is no *golden reference*
for "how a good data-migration validation test is written and run."

**Goal:** build a **test-template repository** that is two things at once:

1. **A teaching/exemplar library** — one canonical, well-documented, parameterized test
   per validation pattern, so the execution agent few-shots from (or scaffolds onto) a
   proven shape instead of inventing one each time.
2. **A self-proving suite** — every template test is itself exercised against **real
   Impala/Hive and real BigQuery instances** using a tiny deterministic fixture
   migration in a **dedicated sandbox GCP project**, so we *know* every template passes
   green before we hold it up as the standard.

The repo is parameterized entirely by **environment variables** for connectivity
(Impala, BigQuery, source RDBMS, egress targets, GCS, Secret Manager, Composer) and uses
`gcloud auth` + a sandbox GCP project to spin up whatever ephemeral infra a given test
family needs (Dataproc/Impala, Composer, Cloud Run, Cloud Functions).

---

## 2. Reference project (the corpus we must cover)

The driving example is the **NBCS Contact-Center CDH → GCP** migration. Scale and shape:

- **41 user stories**, **13 flow groups**, **218 acceptance criteria**.
- Legacy stack: HDFS, Hive (ORC/Parquet/text), **Impala**, Oozie, Sentry, Kerberos,
  LDAP, edge-node cron, Sqoop, ADLS Gen2, Azure VMs, PGP/SFTP feeds.
- Target stack: **BigQuery** (3 datasets: `nbcs_staging` 45 tbl, `nbcs_ods` 31 tbl,
  `nbcs_dm` 25 tbl + views), **Cloud Composer** (7 DAGs), **Cloud Functions**,
  Cloud Monitoring + Teams webhook, **Secret Manager**, GCS, federated `EXTERNAL_QUERY`
  to Oracle/SQL Server/MySQL/Postgres.

### Flow groups (and what they assert)

| Flow group | Stories | ACs | Dominant validation patterns |
|---|---|---|---|
| Target Schema | 1 | 10 | DDL/schema introspection (types, partition, cluster), epoch column rename |
| Infrastructure | 7 | 30 | Terraform apply + idempotency, resource existence, IAM bindings |
| Extract-Load | 3 | 23 | Federated connectivity, watermark/incremental, bulk load per-format, DECIMAL roundtrip, file/PGP intake |
| Transform | 2 | 12 | Row + fingerprint parity, epoch conversion, SCD-2, MERGE idempotency, dialect lint |
| Orchestration | 1 | 9 | Airflow DAG topology, task counts, TaskGroups, operators |
| Egress | 4 | 16 | SQL Server MERGE / Postgres upsert / GCS EXPORT parity, control totals, PGP roundtrip |
| Access & Security | 1 | 9 | IAM access-matrix (allow/deny), authorized views, column-level PII masking |
| Observability | 5 | 17 | Alert fire-on-FAIL, retention policy, DAG SLA/retry/callback, dashboards |
| Acceptance & Rollout | 4 | 21 | Parallel-run parity (Impala↔BQ), DQ-check parity, UAT sign-off |
| Cutover & Rollback | 4 | 21 | Cross-flow parity gate, rollback-restores-legacy, watermark snapshot |
| Consumer Migration | 1 | 9 | Per-query Impala↔BQ parity (NDV tolerance, regex, cross-join, hints) |
| Historical Backfill | 3 | 20 | Full reconcile (count + aggregate + fingerprint + epoch + SCD-2 + FK orphan) |
| Decommissioning | 5 | 21 | Absence assertions (VM/disk/NIC gone, oozie killed, crontab empty, keytab/DNS/LDAP gone) |

---

## 3. Goals / Non-goals

**Goals**
- A reusable **assertion library** covering every recurring validation pattern below.
- One **golden test per pattern**, parameterized, documented, copy-pasteable.
- **Env-var-only connectivity** — no hardcoded hosts/credentials; a single `.env.example` is the contract.
- A **sandbox harness** that provisions ephemeral Impala + BigQuery (+ Composer/Cloud Run/Functions as needed) in a separate GCP project and tears it down.
- A **meta-CI** proving 100% of template tests run green against the fixture migration.
- A **flow→pattern coverage matrix** so we can claim "all combinations / all AC types covered."

**Non-goals**
- Not a migration tool — it does not *perform* the migration; it *validates* one.
- Not project-specific — the NBCS corpus is the proving ground, but the library is generic.
- Not a replacement for the platform's `cuj_validation_results` flow — it *feeds* it (see §11).

---

## 4. Architecture: two layers

```
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 1 — Template / exemplar tests  (the standard we teach)      │
│   lib/<pattern>.py        reusable assertions                     │
│   tests/<pattern>/...     one canonical golden test per pattern   │
│   Parameterized by .env; engine-agnostic where possible           │
└─────────────────────────────────────────────────────────────────┘
                              │ proven by
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ LAYER 2 — Meta-validation  (tests for the test template)          │
│   fixtures/  tiny deterministic "known-good" migration            │
│   infra/     gcloud+terraform: sandbox BQ + Dataproc/Impala + ... │
│   ci/        spin up → load fixtures → run ALL Layer-1 tests →     │
│              assert every one PASSES → tear down                   │
└─────────────────────────────────────────────────────────────────┘
```

- **Layer 1** is what the platform agent imitates / imports when generating a real
  project's tests. It must be readable and exemplary.
- **Layer 2** is what *we* run in CI to guarantee Layer 1 actually works against live
  Impala + BigQuery. It is the answer to "we should be able to create tests for the test
  template itself so we know every single test can run and pass."

### 4.1 Headline decision: declarative harness, not code imitation

The agent must **not** copy a golden test and write a similar one — that is precisely
where it fails today, because it re-implements the *invariant, error-prone* parts each
time (cross-engine canonicalization, dialect SQL, tolerance math, hashing). Those are
written **once, in golden tested code (`lib/`), and never regenerated.**

Instead, a fixed **harness** reads a declarative **Migration Validation Spec (MVS)** —
a YAML/JSON file the agent produces. The agent supplies only three things:

- **Inputs** — connections + objects under test (table pairs, columns, queries, expected
  resource inventory).
- **Expected outputs** — expected counts / golden fingerprints / schema / access grid; or
  in *live dual-engine* mode, nothing — the harness derives the legacy side itself.
- **Validation criteria** — tolerances, encodings, parity modes, thresholds.

```yaml
# the agent emits THIS, not Python
connections: { source: {engine: impala}, target: {engine: bigquery} }
suites:
  - pattern: rowcount_parity
    tables:
      - { source: dm.fact_interaction, target: nbcs_dm.fact_interaction, tolerance: 0.1% }
  - pattern: epoch_conversion
    columns:
      - { table: nbcs_ods.ods_invoice_acid, column: issued_ts,
          source_encoding: millis_lying, expect_year_between: [2020, 2030] }
```

Consequences: the exemplar `tests/` become **example MVS files + expected results**, not
example code; bad input fails loudly against a JSON Schema instead of silently producing
a wrong test; the harness is reused across projects with zero code changes. See §5.1 for
the per-pattern declarative review.

### 4.2 Six validation axes (the organizing frame)

DVT answers exactly **one** question — *post-migration parity*: does live target table B
equal live source table A? Our corpus spans **six axes**; DVT is the engine for one of
them. This is the top-level structure of the framework; the 21 patterns of §5 are grouped
under it.

| Axis | Question it answers | Shape | Engine |
|---|---|---|---|
| **A · Parity** | target B == source A? | A↔B compare, both live | **DVT** (verification: pass/fail + metrics; small/medium, schema, custom-query) · **reladiff** (localization + scale: emits differing PKs + `+/-` values via bisection, no full scan) · **our** validator for files / manifest-mode / nested STRUCT-ARRAY-JSON · **Datafold Cloud** only for commercial support/UI/lineage |
| **B · Intrinsic correctness** | is the target internally valid (no source needed)? | single-side SQL invariant | ours (SQL assertions) |
| **C · Behavioral / temporal** | does an *operation* behave right when run? | execute → observe | ours (orchestrate + observers) |
| **D · Structural / artifact** | is the *infra / config* correct? | introspect artifact | ours (gcloud / airflow / terraform introspection) |
| **E · Input / in-flight** | is the data correct *before* it is a table? | pre-load on raw inputs | ours (checksum / PGP / format / connectivity) |
| **F · Absence** | did the old thing get *removed*? | negative probe | ours (probe catalog) |

Pattern → axis: **A** = {1,2,3,4}; **B** = {5 range-guard, 6, 7, 8, 10}; **C** = {9, 13, 20};
**D** = {11, 17, 18, 19}; **E** = {12, 14, 15}; **F** = {21}. Several ACs straddle two
axes — a timestamp column is parity (A) **plus** a year-range sanity guard (B); egress is
parity (A) **plus** upsert-semantics (C). DVT covers ~4 of 21 patterns fully (Axis A) and
~a quarter of the ACs; the other five axes are ours and are where the agent fails most.

### 4.3 Covering Cloud Composer, Cloud Run, and GCP-service artifacts

GCP services are both **validated by** and **run on** the framework — two distinct senses.

**(1) As validation *targets* (the migration produces them; we check them).** Each service
gets two adapters:

- **Describe adapter (Axis D — cheap, no execution).** Introspect config without running
  anything. DAGs via a local Airflow `DagBag` or `gcloud composer environments run dags
  list/show` → task count, topology, operators, retries/SLA/callbacks, 0 import errors.
  Cloud Run / Functions via `gcloud run services describe` / `functions describe` → state
  ACTIVE, env vars reference the right Secret Manager entries, trigger prefix, service
  account. Terraform via `terraform plan -detailed-exitcode` (idempotency) + state/`gcloud
  list` (existence). **Most Composer/Run validation is this — no live env needed.**
- **Invoke-observe adapter (Axis C — needs the live resource).** Apply a stimulus, observe
  a side effect. Trigger a DAG run (Airflow REST `POST /dags/{id}/dagRuns`, poll task states
  → all SUCCESS within SLA; force a failure → retries-with-backoff + on_failure_callback
  posts to Teams). Invoke Cloud Run (HTTP) or fire a Function (drop a GCS trigger file) →
  assert the effect. Insert a `_dq_audit` FAIL row → assert the Monitoring alert fires to
  the webhook within SLA; insert PASS → assert no fire.

Side effects are checked by a small reusable set of **observers**: GCS (object exists /
sha256), BigQuery (row landed / count), Cloud Logging (error absent, **no secret leaked**),
HTTP **webhook-receiver** (alert delivered). The receiver is a tiny disposable Cloud Run
"echo" service the harness deploys so alert delivery is deterministic.

```yaml
- pattern: dag_structure            # Axis D — no live env
  dag_id: nbcs_ods_dm_build
  expect: { task_count: 49, import_errors: 0, retries_min: 3, on_failure_callback: required,
            topology: ["cleanse(15)>>scd2(3)>>acid(4)>>dims(9)>>facts(9)>>aggs(7)"] }
- pattern: dag_run_success          # Axis C — live Composer
  dag_id: nbcs_master_daily
  trigger: { conf: { run_date: "2026-06-01" } }
  expect: { all_tasks: SUCCESS, within_seconds: 14400 }
- pattern: cloud_run_invoke_observe # Axis C — live Cloud Run / Function
  service: nbcs-sftp-decrypt-validate
  stimulus: { gcs_put: gs://nbcs-ingest/sftp/acmehealth/2026-06-01/x.dat.pgp }
  observe: { gcs_exists: gs://.../x.dat.gz, sha256_matches_manifest: true,
             logs_contain_secret: false }
```

**(2) As the execution *substrate* (the suite runs on Composer / Cloud Run).** The library
is a plain Python package, so the same code runs three ways: **CI/local** (pytest — one-time
sign-off + meta-validation); **Cloud Run Job** (one-shot, partitioned, for large-table
parity — DVT supports Cloud Run/K8s scaling — and for the sandbox meta-CI); **Composer DAG
task** (embedded in the migrated pipeline so the *recurring* ACs — 5-day parallel-run
parity, the DQ-gate ShortCircuitOperator, soak-window monitoring — run continuously in prod,
not just once; DVT ships an Airflow sample DAG for this). Cost control: Axis D needs no live
env; only Axis C needs the slow resources — pooled, gated behind `--with-composer` /
`--with-cloudrun`. (Extends §9 and §14 #5.)

---

## 5. Validation taxonomy (the core deliverable)

Twenty patterns extracted from the 218 ACs. Each becomes one `lib/` module + one or more
golden tests. Each entry lists: **what it asserts**, **inputs**, **tolerance model**.

### Axis A — Parity family (two engines: DVT verify + reladiff localize; + our file/manifest/nested validator)

> **Engine routing (declarative, agent never picks code).** The MVS declares parity intent;
> the harness routes: **DVT** by default (verification → pass/fail, aggregate metrics,
> result-handler tables; also schema name+type and custom-query); **reladiff** when
> `scale: large` or `mode: localize` (bisection segment-checksum → returns the *differing
> primary keys and `+/-` values* without full-scanning, so a red parity test becomes an
> actionable fix signal — e.g. "rows pk={…} differ on `issued_ts`"); **our validator** for
> files, manifest-mode (legacy engine already decommissioned), and nested STRUCT/ARRAY/JSON
> that neither tool supports. Both DVT and reladiff require **primary keys** for row-level work.
1. **Row-count parity** — per-table `COUNT(*)` legacy(Impala/Hive manifest) vs BigQuery.
   Tolerance is configurable per table class: `0` for dims/staging, `≤0.1%` for
   partitioned facts/aggs. Output = per-table report (name, src, tgt, delta, pass/fail).
2. **Aggregate-checksum parity** — `SUM` of numerics, `COUNT(DISTINCT)` of strings,
   `MIN/MAX` of timestamps, per column. Tolerances: numeric sums within ε (e.g. 0.01 or
   canonical-rounded DECIMAL), counts exact, timestamps ±1s.
3. **Order-independent full-row fingerprint** — canonicalize identically on both engines
   (NULL→sentinel `__NULL__`/`\x00`, float→ROUND to fixed decimals, TIMESTAMP→truncate to
   second, fixed column order), hash per row, XOR/sum-aggregate per table. Compare digests.
   Covers the "0 mismatches" high-risk-table checks.
4. **Cross-engine query parity** — run a legacy query on Impala and its converted form on
   BigQuery against the *same seed*, then apply (1)+(2)+(3). Handles special cases:
   `APPROX_COUNT_DISTINCT` (±5% tolerance, not exact), regex classification equality,
   CROSS JOIN row preservation, dropped `STRAIGHT_JOIN` hint.

### Axis B — Intrinsic correctness: type / encoding family (ours)
5. **Epoch & timestamp conversion** — assert seconds→`TIMESTAMP_SECONDS`,
   millis→`TIMESTAMP_MILLIS`, string→`PARSE_TIMESTAMP` match legacy `from_unixtime()` in
   UTC; **lying-column** guard (epoch-millis stored under a `_sec` name → year must be
   2020–2030, not ~57000); out-of-range epochs → NULL + `_dq_audit` row, not error.
6. **DECIMAL precision roundtrip** — load→read-back exact string match after `CAST AS
   STRING` on both sides, across all `NUMERIC(p,s)` columns.

### Axis B — Intrinsic correctness: schema family (ours; DVT covers name+type only)
7. **DDL / schema introspection** — `INFORMATION_SCHEMA.{TABLES,COLUMNS,TABLE_OPTIONS}`:
   exact table counts per dataset, type mapping (BIGINT→INT64 …), partition column + type,
   clustering columns, complex types (ARRAY/STRUCT/JSON), absence of Hive-only directives
   (LOCATION/SERDE/EXTERNAL/TBLPROPERTIES/ACID/bucketing), column rename + description.

### Axis B + C — Stateful-transform family (intrinsic invariants + run-twice behavior; ours)
8. **SCD-2 continuity** — per entity key: no eff_from/eff_to gaps, exactly 1
   `is_current=TRUE`, terminal `eff_to = 9999-12-31`, surrogate hash byte-identical to
   legacy `md5(concat_ws(...))`.
9. **MERGE / delta idempotency** — run the MERGE (and companion DELETE/UPDATE) twice with
   same input; assert row count + per-column checksums unchanged; assert `op='D'` keys
   absent.
10. **FK orphan rate** — `LEFT JOIN … IS NULL` counts per documented join path; orphan
    rate within ±0.05pp of legacy.

### Axis D — Access-control family (structural / artifact; ours)
11. **IAM access-matrix** — for each principal × dataset/table/column, assert exact
    allow/deny; authorized-views return data without underlying table grant;
    column-level policy tags mask PII for non-privileged principals, unmask for
    `etl_writer`/`dq`. Output = N×M allow/deny grid with 0 unexpected.

### Axis E — Input / in-flight: ingestion / file family (ours)
12. **Federated connectivity** — `EXTERNAL_QUERY(conn, 'SELECT 1')` per connection
    (Oracle `FROM DUAL`, others bare) returns 1 row, 0 connection errors.
13. **Watermark / incremental ingestion** — run with initial then advanced watermark;
    second run appends only `check_column > watermark`; total = full seed, no dupes;
    `_ingestion_audit` records both runs with `SUCCESS`.
14. **Bulk load per-format** — `bq load` for PARQUET / CSV(`|`) / NEWLINE_DELIMITED_JSON
    with hive partitioning; landed counts match `expected-counts.json`; partition metadata
    present.
15. **File intake: checksum / gzip / PGP / header-drift** — SHA-256 of plaintext matches
    manifest; gzip integrity; PGP decrypt→re-encrypt roundtrip SHA match; CSV header-drift
    detection; bad files quarantined; **no secret material in logs**.

### Axis A + C — Egress family (parity + upsert-semantics / file byte-identity; DVT partial)
16. **Egress parity** — converted SQL produces field-for-field match vs legacy export;
    SQL Server `MERGE` and Postgres `INSERT…ON CONFLICT` upsert semantics (exact
    update/insert split, no dup composite keys); GCS `EXPORT DATA` byte-identical after
    canonical sort; control totals (`row_cnt`, `control_total`) reconcile to 0 variance.

### Axis D — Infra-as-code family (structural / artifact; ours)
17. **Terraform resource existence** — after `apply`, assert the exact set of created
    resources (datasets, connections, Composer env RUNNING, service accounts + bindings,
    buckets + lifecycle, N secrets, M functions ACTIVE).
18. **Terraform idempotency** — second `apply` (or `plan`) shows 0 add / 0 change /
    0 destroy.

### Axis C + D — Orchestration / observability family (describe + invoke-observe; ours, see §4.3)
19. **Airflow DAG structure** — parse DAGs (0 import errors); exact total task count;
    TaskGroup topology / dependency order; operator types; `retries`, `exponential_backoff`,
    `on_failure_callback`, `sla_miss_callback`, schedule_interval; no deprecated operators.
20. **Monitoring / alerting behavior** — insert a `status='FAIL'` row → Teams webhook fires
    within SLA with correct payload; insert `PASS` → no fire; retention
    `partition_expiration_days` enforced; dashboard panels return non-empty data.

### Axis F — Decommissioning family (absence assertions; ours)
21. *(bonus pattern)* **Resource-absence** — `az vm/disk/nic list` empty, `oozie jobs
    -filter` empty, `crontab -l` clean, `kinit` fails, `nslookup` NXDOMAIN, `ldapsearch`
    empty, endpoint TCP refused, KMS/keytab/PGP files shredded. Asserts *negative* state,
    with safe read-only probes.

> These 21 patterns × the 13 flow groups define **"all combinations."** §6 maps which
> patterns are required per flow group so coverage is provable, not aspirational.

---

### 5.1 Declarative review — can each pattern be config-only?

How much the agent must supply, and whether any non-data "escape hatch" is unavoidable.
**16 of 21 are fully declarative.** The other 5 are still declarative — their only code
input is SQL the migration already produced, a parse-regex, or expected-structure-as-data.

| # | Pattern | Declarative | Agent supplies | Escape hatch |
|---|---|---|---|---|
| 7 | Schema/DDL conformance | Full | expected table set, type map, partition/cluster/desc | — |
| 1 | Row-count parity | Full | table pairs + per-table tolerance | — |
| 14 | Bulk load per-format | Full | file→table map, format, expected counts | — |
| 5 | Epoch/timestamp conversion | Full | column→encoding map, expected year-range | — |
| 6 | DECIMAL roundtrip | Full | decimal columns | — |
| 2 | Aggregate-checksum parity | Full | columns+agg+tolerance (auto from schema) | — |
| 3 | Full-row fingerprint parity | Full | table pairs, key cols, canonicalization | — |
| 8 | SCD-2 continuity | Full | table, entity key, eff-date cols, current flag | — |
| 9 | MERGE/delta idempotency | Full | merge-target tables (run-twice automatic) | — |
| 10 | FK orphan rate | Full | join paths + tolerance | — |
| 12 | Federated connectivity | Full | connection ids + per-dialect probe | — |
| 13 | Watermark/incremental | Full | table, check_column, two watermarks | — |
| 17 | Terraform existence | Full | expected resource inventory | — |
| 18 | Terraform idempotency | Full | nothing (re-apply) | — |
| 11 | IAM access-matrix | Full | principals × objects × allow/deny grid, PII cols | — |
| 20 | Monitoring/alerting | Full | alert policy + inject FAIL/PASS + expected | — |
| 4 | Query/view parity | Decl+hatch | legacy SQL text + converted SQL text + mode | query text (already an artifact) |
| 16 | Egress parity | Decl+hatch | source→sink, upsert keys, comparison spec | sink connector + file layout |
| 15 | File/PGP/SFTP intake | Decl+hatch | file manifest (sha, format) | parse-regex for custom formats |
| 19 | DAG topology | Semi | expected task list, edges, operator/retry/SLA | — (harness diffs actual) |
| 21 | Decommission absence | Probe catalog | probe type + target + expected state | new probe types |

## 6. Coverage matrix (flow group → required patterns)

| Flow group | Required patterns (by number) |
|---|---|
| Target Schema | 5, 7 |
| Infrastructure | 11, 12, 17, 18, 19(smoke) |
| Extract-Load | 6, 12, 13, 14, 15 |
| Transform | 1, 2, 3, 5, 8, 9, 10 |
| Orchestration | 19 |
| Egress | 15, 16 |
| Access & Security | 11 |
| Observability | 18, 19, 20 |
| Acceptance & Rollout | 1, 2, 3, 4, 11 |
| Cutover & Rollback | 1, 2, 4, 16, 13(watermark snapshot) |
| Consumer Migration | 4 |
| Historical Backfill | 1, 2, 3, 5, 8, 10 |
| Decommissioning | 21 |

CI asserts: every pattern has ≥1 green golden test, and every flow group's required
patterns are present.

---

## 7. Repository layout

```
data-migration-test-template/
├── README.md                      # how to use as a template + how to self-validate
├── pyproject.toml                 # deps: pytest, google-cloud-bigquery, impyla/pyhive,
│                                  #   oracledb, pyodbc, pymysql, psycopg2, paramiko, python-gnupg,
│                                  #   google-cloud-* (composer, monitoring, secretmanager, storage)
├── .env.example                   # the connectivity contract (see §8)
├── conftest.py                    # session fixtures: clients, scratch-dataset lifecycle, skip-if-unconfigured
├── lib/                           # LAYER 1 assertion library (one module per pattern §5)
│   ├── parity.py  epoch.py  schema.py  scd2.py  merge.py  fk.py
│   ├── iam.py  federated.py  watermark.py  bqload.py  fileio.py  egress.py
│   ├── terraform.py  dag.py  monitoring.py  decommission.py
│   ├── canonicalize.py            # the shared NULL/float/timestamp canonicalization (used by both engines)
│   └── engines.py                 # BigQueryEngine / ImpalaEngine adapters (uniform query/introspect API)
├── tests/                         # LAYER 1 golden tests, grouped by pattern
│   ├── schema/test_ddl_introspection.py
│   ├── parity/test_rowcount.py  test_aggregate.py  test_fingerprint.py  test_cross_engine.py
│   ├── encoding/test_epoch.py  test_decimal_roundtrip.py
│   ├── transform/test_scd2.py  test_merge_idempotency.py  test_fk_orphan.py
│   ├── access/test_iam_matrix.py
│   ├── extract/test_federated.py  test_watermark.py  test_bqload.py  test_file_intake.py
│   ├── egress/test_egress_parity.py
│   ├── infra/test_tf_existence.py  test_tf_idempotency.py
│   ├── orchestration/test_dag_structure.py
│   ├── observability/test_alerting.py  test_retention.py
│   └── decommission/test_absence.py
├── fixtures/                      # LAYER 2 deterministic "known-good" mini-migration
│   ├── legacy/                    # Hive DDL + tiny seed CSV/Parquet → loaded into Dataproc/Impala
│   ├── target/                    # BigQuery DDL + converted transform SQL
│   ├── dags/                      # 1-2 sample Composer DAGs
│   ├── terraform/                 # minimal TF module the infra tests assert against
│   └── expected/                  # expected-counts.json, golden fingerprints, access-matrix.yaml
├── infra/                         # LAYER 2 sandbox provisioning
│   ├── bootstrap.sh               # gcloud auth, set project, enable APIs
│   ├── impala.{tf|sh}             # Dataproc cluster (Hive/Impala) OR containerized Impala on GCE/Cloud Run
│   ├── bigquery.tf  composer.tf  functions.tf
│   └── teardown.sh
└── ci/
    └── run-meta-validation.sh     # provision → seed → pytest (all Layer-1) → assert green → teardown
```

---

## 8. Connectivity contract (`.env.example`)

Every test reads connection details from env. Tests **skip with a clear reason** when
their required vars are unset (so a partial environment still runs the subset it can).

```bash
# --- GCP / BigQuery ---
GCP_PROJECT=                       # sandbox project for meta-validation
BQ_LOCATION=US
BQ_STAGING_DATASET=nbcs_staging
BQ_ODS_DATASET=nbcs_ods
BQ_DM_DATASET=nbcs_dm
GOOGLE_APPLICATION_CREDENTIALS=    # or rely on `gcloud auth application-default login`

# --- Legacy Impala / Hive (per-agent VM ext IPs in §9.0) ---
IMPALA_HOST=                       # e.g. 34.122.57.156 (impala-claude) / 35.223.227.9 (impala-codex)
IMPALA_PORT=21050                  # Impala HS2 — NoSASL, analytic reads
HIVE_HOST=                         # same VM as IMPALA_HOST (bundled)
HIVE_PORT=10000                    # Hive HS2 — NoSASL, writes/seeding (bucketed/SERDE/managed)
IMPALA_AUTH=NONE                   # golden image is NoSASL (NONE); KERBEROS|LDAP for real legacy
KRB5_KEYTAB= / KRB5_PRINCIPAL=     # if KERBEROS

# --- Source RDBMS (federated query sources) ---
ORACLE_DSN= ORACLE_USER= ORACLE_PASSWORD=
MSSQL_DSN=  MSSQL_USER=  MSSQL_PASSWORD=
MYSQL_DSN=  MYSQL_USER=  MYSQL_PASSWORD=
PG_DSN=     PG_USER=     PG_PASSWORD=
BQ_CONNECTIONS=nbcs-crm,nbcs-hr,nbcs-wfm,nbcs-telephony,nbcs-ticketing,nbcs-finance

# --- Egress targets ---
EGRESS_MSSQL_DSN= ...              # reporting mart
EGRESS_PG_DSN= ...                 # CRM reverse-ETL
EGRESS_GCS_BUCKET=gs://nbcs-egress
SFTP_HOST= SFTP_USER= SFTP_KEY_SECRET=
PGP_PRIVATE_KEY_SECRET= PGP_PASSPHRASE_SECRET=

# --- GCP services ---
COMPOSER_ENV= COMPOSER_REGION=
SECRET_MANAGER_PREFIX=nbcs-
MONITORING_NOTIFICATION_CHANNEL=
TEAMS_WEBHOOK_SECRET=

# --- Decommission probes (read-only) ---
AZURE_RESOURCE_GROUP= OOZIE_URL= EDGE_NODE_SSH= LDAP_BASE_DN=
```

---

## 9. Sandbox infra & self-validation harness (Layer 2)

### 9.0 Concrete sandbox: `platform-playground-test1` (provisioned)

This initiative is split across **two repos / two agents**, each with its own isolated
sandbox so they never collide. Auth = the operator's own `gcloud` credentials
(`abhis@gallopintelligence.ai`) — **not** the migration-qa SA.

| Repo (org `Gallop-Inc`) | Agent | BigQuery home (project `platform-playground-test1`) | Impala/Hive VM (zone `us-central1-a`) |
|---|---|---|---|
| `dmtemplate-claude` | Claude | dataset `dmtemplate_claude` ✅ | `impala-claude` ✅ RUNNING — ext `34.122.57.156`, int `10.128.0.4` |
| `dmtemplate-codex` | Codex | dataset `dmtemplate_codex` ✅ | `impala-codex` ✅ RUNNING — ext `35.223.227.9`, int `10.128.0.5` |

- **BigQuery (2 instances, done):** one home dataset per agent, `US`. The harness creates
  per-run scratch datasets `<agent>_nbcs_staging/ods/dm` under each.
- **Impala+Hive (2 instances, done):** GCP has no managed Impala, so each agent gets a GCE
  VM (`n2-standard-4`) booted from our **`impala-base` Packer image
  `impala-base-1782069926`** (project `gallop-platform-dev`). The image's baked
  `impala-compose.service` systemd unit auto-starts a docker-compose bundle exposing **both**
  engines on a shared metastore: **Impala HS2 `:21050`** (`apache/impala 4.5.0`, NoSASL,
  analytic *reads*) **+ Hive HS2 `:10000`** (`apache/hive 3.1.3`, NoSASL, *writes* incl.
  bucketed/SERDE/managed tables Impala refuses). This is the bundle the platform's
  `ImpalaInfraProvider` provisions (see `server/scripts/test-impala-gce.ts`,
  `packer/impala/compose.yml`). Seed via Hive `:10000`, validate via Impala `:21050`.
  - **Image choice note:** the *released* image (`gallop-platform-release/impala-base-1781243468`,
    06-11) predates the `:10000` Hive endpoint (added in #565, 06-13) and exposes **only**
    `:21050` — do not use it. We pin the newer dev build `1782069926` (06-21) which has the
    Hive endpoint. It is ~7 min older than the stats-autogather fix (#81c7b8cfe, 06-21 12:34),
    so **managed-table `INSERT`s via Hive may report a false StatsTask failure** even though
    rows land — workaround: `SET hive.stats.autogather=false;` per session, or use
    `EXTERNAL` + `LOCATION` tables, until we rebuild the image past that commit.
- **Permissions:** firewall `allow-impala-hs2` (tcp:21050,10000) on the VM tag; SSH via the
  default rule. NoSASL + open source-range is the platform dev convention — acceptable for
  **fixture-only** sandbox data; tighten source-ranges before any real data.
- **Cost control:** VMs are `n2-standard-4`, **stopped when idle** (`gcloud compute instances
  stop impala-claude impala-codex --zone us-central1-a`); BigQuery is serverless.
- **Readiness:** after a cold boot the bundle takes ~3–5 min before `:21050`/`:10000` accept
  connections — agents should poll the ports.

A **dedicated GCP project** (separate from dev/prod) holds the ephemeral resources. The
harness uses `gcloud auth` (ADC or a CI service account) and `terraform`/`gcloud` to
provision only what a test family needs, then tears down.

**The legacy "Impala" side — recommendation:** use **Dataproc** (managed
Hadoop/Hive; Impala available via optional component or Hive as the SQL engine). It is
the GCP-native way to stand up a real legacy-shaped engine on demand, supports Kerberos,
and dies cleanly. *Alternative:* a single GCE VM (or Cloud Run job) running a
containerized Apache Hive/Impala for cheaper, lighter runs. **Open decision §14.**

**Provisioning per family:**

| Test family | Ephemeral infra spun up |
|---|---|
| schema, parity, epoch, scd2, merge, fk | BigQuery datasets + Dataproc/Impala + seed |
| federated, watermark | BQ connections + tiny source RDBMS (Cloud SQL or containerized) |
| bqload, file_intake | GCS bucket + Cloud Functions (or local function emulation) |
| egress | Cloud SQL (MSSQL/PG) + GCS + SFTP container |
| infra (tf) | a throwaway TF state against the sandbox project |
| orchestration, dag, observability | Cloud Composer env (the slow/expensive one — pooled, see risks) + Cloud Monitoring |
| decommission | a disposable "to-be-decommissioned" set of resources to delete + re-probe |

**`ci/run-meta-validation.sh` flow:**
1. `gcloud auth` + select sandbox project + enable APIs.
2. `terraform apply` the fixture infra (idempotent — also exercises patterns 17/18).
3. Load `fixtures/legacy/*` into Dataproc/Impala, `fixtures/target/*` into BigQuery.
4. `pytest` the **entire Layer-1 suite** against the fixtures.
5. Assert **every test passed** (this is the meta-assertion).
6. `terraform destroy` / `teardown.sh` (always, via trap).

The fixture migration is *intentionally correct*, so a green run proves the templates
themselves are valid. We additionally ship a small set of **negative fixtures** (a
deliberately broken migration) to prove the assertions actually *fail* when they should —
otherwise a no-op test would also pass.

---

## 10. Fixture strategy

- **Tiny but representative:** ~5–10 tables per layer, each exercising the hard cases
  (a lying epoch column, a SCD-2 table, an ACID/MERGE table, a nested-JSON file feed, a
  DECIMAL-heavy table, a multi-column Hive partition collapsed to partition+cluster).
- **Deterministic seed** committed to the repo (`fixtures/legacy/seed/`,
  `expected-counts.json`, golden fingerprints) so parity is reproducible offline.
- **Dual-loadable:** same logical rows load into Impala/Hive (legacy) and BigQuery
  (target) so cross-engine parity (pattern 4) is real, not mocked.
- **Negative twins:** for each pattern, a broken variant under `fixtures/negative/` that
  the meta-CI asserts *fails*.

---

## 11. Integration with the platform

- The platform already tracks validation outcomes in `cuj_validation_results`
  (per story, per criteria_index, PASS/FAIL/pending) and reconciliation in
  `reconciliation_runs`/`reconciliation_items`. The template's report objects should
  serialize to the **same shape** so a generated test's output drops straight into the
  existing CUJ-validation flow.
- **Consumption model (recommended, per §4.1):** the execution agent emits a declarative
  **MVS** (YAML/JSON) of inputs/expected/criteria; the fixed harness (`lib/`) runs it. The
  agent never authors assertion code or dialect SQL. The `tests/` golden files are example
  specs + expected results, used as few-shot material.
- Map each acceptance-criterion phrasing ("row-count difference is 0", "0 mismatches",
  "±5% of NDV", "0 additions/changes/destructions") to the pattern that satisfies it, so
  the agent picks the right `lib/` call from AC text.

---

## 12. Tech stack recommendation

- **Python + pytest** as the primary stack. Rationale: BigQuery, Impala (`impyla`),
  Oracle/MSSQL/MySQL/PG drivers, Composer/Airflow (DAGs *are* Python), `paramiko`,
  `python-gnupg`, and all `google-cloud-*` SDKs are first-class in Python; the migrated
  artifacts (DAGs, Cloud Functions) are Python, so tests live next to what they test.
- Pytest gives parameterization (`@pytest.mark.parametrize` over table lists),
  skip-if-unconfigured, fixtures for resource lifecycle, and machine-readable reports
  (JSON/junit) for platform ingestion.
- Terraform for infra patterns (17/18) and sandbox provisioning; `gcloud` for auth and
  the few imperative steps (Dataproc, Composer triggers, decommission probes).
- **DVT (`google-pso-data-validator`) — license confirmed Apache-2.0** (repo `LICENSE` +
  PyPI, latest ~8.7.0). Adopted as the **Axis-A *verification* engine for the live-table
  case**, embedded in-process and invoked from `lib/parity.py`. It is *not* the framework:
  Axes B–F, and the file / manifest-mode / nested STRUCT-ARRAY-JSON parts of Axis A, are ours.
- **reladiff (`reladiff` on PyPI) — Axis-A *localization + scale* engine.** The actively
  maintained fork (by data-diff's original author) of the archived `data-diff`; same
  cross-DB bisection segment-checksum algorithm; supports BigQuery/Oracle/Postgres/MySQL/etc.;
  emits differing PKs + `+/-` values and JSON output. Used for data-transfer / large-table /
  divergence-hunting (P0-2, P0-4, P0-8, soak-window, cutover). **License confirmed MIT**
  (repo `LICENSE`) — compatible with embedding/shipping.
- **Datafold Cloud** — fallback *only* if commercial support, a UI, or column-level lineage
  is wanted; reladiff already provides the OSS diff technique, so this is no longer needed
  for capability.

> If the platform must generate **TypeScript** tests to match its own codebase, flag it —
> but the *target systems* are GCP/Python, so Python is the natural fit. **Open decision §14.**

---

## 13. Priorities & build order

Priority follows **data-correctness first**. The P0 spine is the core of a migration
(DDL → load → transfer → transform → query → egress → end-to-end reconcile). Everything
*around* it — provisioning, orchestration, permissions, observability, decommissioning —
is High or Low and can come later. **We build one P0 item at a time, detail first.**

### P0 — data-correctness spine (ordered; one at a time)

| Order | Item | Axis | Patterns | Primary flow group |
|---|---|---|---|---|
| P0-1 | Schema/DDL conformance — target exists correctly first | B (+A name/type via DVT) | 7 | Target Schema |
| P0-2 | Bulk load + row-count parity — data in, counts proven | E + A | 14, 1 | Extract-Load |
| P0-3 | Encoding correctness: epoch + DECIMAL (silent-corruption killer) | B | 5, 6 | Transform |
| P0-4 | Value parity: aggregate checksums + full-row fingerprint | A (DVT; ours for nested) | 2, 3 | Transform / Backfill |
| P0-5 | Transform correctness: SCD-2 + MERGE idempotency + FK orphan | B + C | 8, 9, 10 | Transform |
| P0-6 | Query/view parity — converted SQL identical cross-engine | A (DVT custom-query) | 4 | Consumer Migration |
| P0-7 | Egress parity — outbound movement matches legacy | A + C | 16 | Egress |
| P0-8 | End-to-end reconcile + sign-off report — composes P0-1..7 over full table set | A+B+C composite | 1–10 | Backfill / Acceptance / Cutover |

### High — operability & connectivity (makes a migration runnable, not data-correctness)

Federated connectivity (12) · Watermark/incremental ingestion (13) · File/PGP/SFTP intake
(15) · Terraform existence + idempotency (17, 18) · Airflow DAG structure (19).

### Low — surrounding concerns (defer)

IAM/permissions matrix (11) · Monitoring/alerting (20) · Retention policies · Dashboards ·
Decommissioning absence (21) · Runbook docs.

### Per-item workflow (applies to each P0 item)

For each item, in order: (a) define its MVS schema slice; (b) implement the `lib/` harness
module + canonicalization; (c) write the golden example spec(s) + a negative twin;
(d) prove green against the fixture migration on live BQ + Impala/Dataproc; (e) update the
§6 coverage matrix. Item N+1 starts only after item N is green in meta-CI.

---

## 14. Open decisions (need confirmation)

1. **Legacy-engine substrate — RESOLVED.** GCE VM per agent booted from our golden
   `impala-base` image (bundled Impala `:21050` + Hive `:10000`, NoSASL); see §9.0.
   Manifest mode remains available as the fast offline / post-decommission fallback.
2. **Test language:** Python/pytest (recommended) vs TypeScript to match the platform.
3. **Repo home:** standalone GitHub repo (cloned as scaffolding per project) vs
   `packages/` in this monorepo. *Recommendation: standalone, zero platform deps, mirrors
   `packages/scm-mcp-server` philosophy.*
4. **Sandbox GCP project:** which project ID? Reuse `platform-playground-test1`, or a
   dedicated `*-migration-qa` project? CI auth = service account key vs WIF.
5. **Composer cost control:** keep one long-lived pooled Composer env for M4 tests vs
   create/destroy per run (slow, ~20+ min spin-up). *Recommendation: pooled, gated behind
   a `--with-composer` flag.*
6. **Scope of v1 corpus:** build the generic library proven only on the NBCS fixture, or
   also ship NBCS-specific golden tests as a second exemplar set?

---

## 15. Risks

- **Composer/Dataproc spin-up time & cost** dominate CI wall-clock — mitigate with
  pooling, `--with-<infra>` flags, and manifest mode for the fast path.
- **Cross-engine canonicalization drift** (float/timestamp/NULL) is the classic source of
  false fingerprint mismatches — centralize it in `lib/canonicalize.py`, prove with
  negative twins.
- **Secret handling** in file/PGP/IAM tests — never log secret material; pull from Secret
  Manager; assert logs are clean (pattern 15 already requires this).
- **Decommission tests are destructive** — confine to disposable sandbox resources;
  read-only probes by default; never point at real legacy infra.
- **Partial environments** — every test must `skip` (not error) when its env vars are
  absent, so contributors can run subsets.
```
