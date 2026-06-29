# Authoring guide — writing MVS specs

The execution agent emits a **Migration Validation Spec (MVS)**; the fixed harness runs
it (SPEC §4.1). The agent never writes assertion / canonicalization / dialect-SQL /
hashing code — it only declares inputs, expected, and which patterns to run. This guide
is the reference for what to emit.

A worked end-to-end example lives in [`examples/reference_migration/`](examples/reference_migration/).

## Pick a mode

| You want to… | Mode | How |
|---|---|---|
| Prove the migration **code** produces the right target, from a clean slate | **build-and-verify** (default) | add a `migration:` block |
| Certify an **already-deployed** target (incl. prod), changing nothing | **read-only assessment** | set `read_only: true` |

`read_only: true` blocks every mutating pattern (it can't seed/build), so it is safe
against production. `read_only` + `migration:` is rejected (a contradiction).

## Envelope

```yaml
name: <spec name>
read_only: false              # default; true = assessment mode
connections:
  source: { engine: impala }  # impala | hive | bigquery
  target: { engine: bigquery }
migration: { ... }            # optional; presence => build-and-verify
suites: [ ... ]               # the validations to run
```

Env vars interpolate as `${VAR}` or `${VAR:-default}` (SPEC §8 — never hardcode
dataset/host names). `${BUILD_DATASET}` resolves to the clean build dataset.

## The build-and-verify block

```yaml
migration:
  source_map:                 # ground truth: legacy -> migrated (expectations anchor here)
    - { source: "${SOURCE_DATABASE}.ods_invoice_acid", target: ods_invoice }
  build_dataset: dmt_build    # optional; default dmt_build (reset at START of each run)
  isolate: false              # optional; true = unique per-run dataset for parallel lanes
  steps:                      # applied VERBATIM, in order; abort on first error
    - { kind: ddl,       target: ods_invoice,     sql: sql/ddl/ods_invoice.sql }
    - { kind: load,      target: ods_invoice_raw, from: "gs://…/*", format: PARQUET }
    - { kind: transform, target: ods_invoice,     sql: sql/transform/ods_invoice.sql }
```

- `kind`: `ddl` | `load` | `transform` | `external` (external = non-SQL ETL, not yet
  implemented — use SQL or the E2E adapters when built).
- Unqualified table names in the SQL resolve to the build dataset (default-dataset
  redirection — do **not** hardcode the dataset in your SQL).

## Source cross-check — stand up the legacy source by applying its DDL (`source_setup`)

`schema_conformance` can cross-check each migrated column's type against the **real legacy
type**, read from a live source — not a type list you re-typed. The harness stands up the
legacy source from its own `CREATE TABLE` DDL and reads it back live (Impala/Hive).

Declare a top-level `source_setup` block. It applies **by default** (our source is a sandbox we
provision from the legacy DDL); set `DMT_SOURCE_SETUP=0` to skip it when pointed at a **real**
legacy you must not write to (then the existing source is read as-is):

```yaml
source_setup:
  location_base: ${SOURCE_WAREHOUSE:-/tmp/dmt_src}   # off-cluster hdfs:// LOCATIONs rehosted here
  ddl:                                               # the legacy CREATE TABLE files (tables, not views)
    - sql/legacy/01-create-databases.hql
    - sql/legacy/02-staging.hql
```

The harness applies the DDL faithfully, adapting only what our environment cannot host — the
schema (columns/types) is unchanged: off-cluster `hdfs://` LOCATIONs are rehosted to
`location_base`; ACID (`transactional='true'`) tables are created non-ACID so a non-ACID read
engine can read them; statements are split quote-aware (a `;` inside a SerDe regex won't shred
them). The source is torn down after the run.

Then anchor each `schema_conformance` suite to the live source:

```yaml
- pattern: schema_conformance
  target_dataset: ${BUILD_DATASET}
  source_database: staging              # the REAL legacy database/layer (one suite per layer)
  tables:
    - table: ods_invoice
      source_table: invoice             # BARE legacy table name (no db prefix)
      columns:
        - { name: amount, type: NUMERIC, scale: 2, source_type: DECIMAL }  # legacy DECIMAL -> NUMERIC
```

- Set `source_database` to the real layer (`staging` / `ods` / `dm`) — one suite per layer; the
  harness reads `source_database.source_table` live and checks the legacy→target type mapping.
- **Views are not applied** (a recursive-CTE view can't be created in Hive) — cross-check a
  view's *shape* with `expect_object_type: VIEW` + its columns; the apply path is tables only.

## SQL inputs — the one convention

Everywhere a pattern takes SQL, it comes in two forms:

| Form | Key |
|---|---|
| **path** to a `.sql` file (the CUT's artifact — preferred) | `sql:` |
| **inline** SQL | `sql_text:` |

Role-based patterns use a prefix: `transform_diff` → `legacy_sql`/`legacy_transform`
(inline/path) and `migrated_sql`/`migrated_transform`; `query_parity` → `source_sql`/
`source_sql_path` and `target_sql`/`target_sql_path`. Prefer the **path** form so the
spec validates the CUT's actual checked-in SQL.

## Declaring data with `given`

`given` stands up tables — in BigQuery for unit tests, in **both** engines for
`transform_diff`. No Python loader needed.

```yaml
given:
  ods_invoice_raw:
    columns:
      - { name: invoice_id, type: INT64 }
      - { name: amount,     type: NUMERIC, scale: 2 }   # scale matters for decimals
      - { name: op,         type: STRING }
    rows:
      - { invoice_id: 5001, amount: "100.00", op: "I" }
```
Types: `INT64`, `NUMERIC` (+ `scale`), `TIMESTAMP`, `DATE`, `STRING`, `BOOL`, `FLOAT64`.
Decimals/timestamps may be written as strings; they canonicalize correctly on compare.

## Choosing a validation tier

| Tier | Pattern | When | Cost |
|---|---|---|---|
| **Unit** | `transform_unit` | test one transform's logic on controlled input | BQ only, ¢ |
| **Equivalence** | `transform_diff` | prove a rewrite matches the legacy transform | both engines, ¢ |
| **Integration** | `rowcount_parity`, `aggregate_parity`, `fingerprint_parity`, `query_parity`, `scd2_continuity`, `merge_idempotency`, `fk_orphan`, `epoch_conversion`, `decimal_roundtrip` | compare migrated vs legacy on real data | both engines |
| **Schema** | `schema_conformance` | target types/partition/cluster/options correct | BQ |
| **Egress / Orchestration / Perf** | `egress_parity`, `dag_structure`, `query_performance` | exports, Composer DAGs, performance | varies |

Prefer **many unit tests** (cheap, precise) + **diff** for rewrites; reserve full
parity for real-data acceptance.

### `transform_unit` — exact rows or properties

```yaml
- pattern: transform_unit
  sql: sql/transform/ods_invoice.sql      # the CUT's artifact (reuse it!)
  given: { ods_invoice_raw: { columns: [...], rows: [...] } }
  expect:
    table: ods_invoice
    rows: [ { invoice_id: 5001, op: "I", issued_ts: "2026-06-01T00:00:00Z" } ]  # canonicalized set-equality
    assert: [ { rowcount: 2 }, { unique: [invoice_id] }, { no_nulls: [issued_ts] } ]
```
`expect.rows` authored from *requirements* = a correctness test; a captured snapshot =
a regression test. Use `transform_diff` to avoid hand-authoring the answer entirely.

### `transform_diff` — equivalence (legacy = oracle)

```yaml
- pattern: transform_diff
  given: { ods_invoice_raw: { columns: [...], rows: [...] } }
  legacy_sql:   "SELECT op, COUNT(*) n, SUM(amount) total FROM ods_invoice_raw GROUP BY op"
  migrated_sql: "SELECT op, COUNT(*) n, SUM(amount) total FROM ods_invoice_raw GROUP BY op"
```
Both transforms must be **SELECTs** (the pattern compares result sets). Keep them
dialect-safe where engines differ (e.g. avoid epoch/timezone functions in a diff).

## Lint every spec before you commit

Schema-check a spec **without a DB**:

```
python -m lib.cli validate <spec>.mvs.yaml
```

It validates the envelope + every suite against its pattern's JSON Schema and prints each problem
(missing required field, unexpected field, unknown pattern, bad YAML). **Fix everything it flags
before committing** — these are the errors that otherwise only surface mid-run. Two common traps it
catches:

- **Quote complex types.** A `STRUCT`/`ARRAY` type string contains `<`, `>`, `:`, `,` — all YAML
  metacharacters — so it MUST be quoted, or the file won't parse:
  ```yaml
  - { name: address, type: "STRUCT<street:STRING, zip:STRING>" }    # quoted ✅
  - { name: tags,    type: "ARRAY<STRING>" }                         # quoted ✅
  # - { name: address, type: STRUCT<street:STRING, zip:STRING> }    # unquoted → YAML error ❌
  ```
- **`query_performance` fields per `mode`.** Every query needs `id` + `mode`; then by mode:
  `assert` requires `thresholds` (a `max_*` map: `max_bytes_scanned` / `max_elapsed_ms` / …);
  `measure` takes **no** `thresholds`; `compare` needs `a` / `b` / `compare`; `regression` needs
  `baseline` / `tolerances`. There is **no `expect_error`** field — don't invent one.

## The invariants (what keeps a green meaningful)

1. The harness **applies** the CUT's SQL; it never **authors** it.
2. Apply **verbatim** — no normalization (that would mask defects).
3. Expectations anchor to **source ground truth** (`source_map` / legacy T), never the DDL.
4. **Clean slate + guarded build dataset** (can't touch a real/named dataset).
5. **Multi-axis** — schema *and* data, so an empty/faked target fails.

## Output

Every run produces a `Report` (PASS / FAIL / ERROR per check) that flattens to the
platform's `cuj_validation_results` shape (SPEC §11). No SKIP: unsupported paths are
removed, a missing env var errors (fail-fast).
