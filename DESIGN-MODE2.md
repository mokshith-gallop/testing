# Design: Mode-2 build-and-verify (default) + the ELT unit tier

Status: **Phases 1–3 implemented + green on real BigQuery + Impala.**
Spine (build-and-verify) + ELT unit tier (`transform_unit`) + cross-engine declarative
seeder (`given` → both engines) + transform equivalence (`transform_diff`, legacy oracle).
Supersedes the implicit "inspect whatever's deployed" default with an explicit,
attributable, cheat-resistant **build-and-verify** default. Read-only assessment
becomes the opt-out, not the baseline.

## Why

Today the harness *inspects outcomes* (`schema_conformance` reads `INFORMATION_SCHEMA`,
parity reads both sides). The DDL/ELT is applied by the fixture loaders as setup. That
is correct for **assessing an already-deployed target** (Mode 1), but it is *not* a
faithful test of a migration's **code**, because:

- **Stale state ⇒ false pass.** A prior run's tables can satisfy the checker even if
  this run's code is broken. A green is not attributable to *this* run.
- **No clean slate ⇒ unfalsifiable.** Without a fresh target + teardown you can't say
  "this artifact, from nothing, produced the right result."

The fix is to make the harness **apply the CUT's artifacts against a clean ephemeral
target, then read back and judge** — apply-and-read-back. This is *more* scalable for
ELT (transforms are SQL the harness already runs) and *more* cheat-resistant (the
harness owns the slate; the CUT only hands over artifacts, never controls execution).

## The line that makes it honest: **apply ≠ author**

- **Author** the DDL/ELT SQL = the migration's deliverable. The harness NEVER does this.
- **Apply** the CUT's artifact = execute it verbatim. The harness DOES this.

When the harness runs the CUT's `CREATE TABLE`, "schema matches the DDL" is a tautology.
So the **expectation is anchored to the *source* ground truth** (legacy schema → BQ type
mapping; legacy row counts/aggregates), never to the CUT's DDL. Apply the CUT's artifact;
judge against the source. That is what keeps a green unforgeable.

## Two modes

| Mode | Trigger | Lifecycle | Use |
|---|---|---|---|
| **Build-and-verify** (NEW default) | `migration:` block present, no `read_only` | provision ephemeral → apply CUT artifacts verbatim → verify vs source truth → teardown | CI gate; prove the migration *code* |
| **Read-only assessment** (opt-out) | `read_only: true` | inspect in place; apply nothing, clean nothing | certify a live/prod target as-is |

`read_only: true` continues to block any `mutates` pattern (existing guard,
`harness._run_suite`). The two modes are mutually exclusive: `read_only` + `migration:`
is a spec error (you can't build against a target you've declared off-limits).

## The five invariants (apply to every mode/tier)

1. Harness **applies** the CUT's artifacts; never **authors** them.
2. Apply **verbatim** — no harness normalization/"fixing" (it would mask defects).
3. Expectations **anchored to source ground truth**, never to the CUT's DDL/output.
4. **Clean ephemeral slate + guaranteed teardown** (finally-block; runs even on failure).
5. **Multi-axis** — schema *and* source-anchored data parity, so an empty
   correctly-shaped table fails.

## Artifact-bundle contract (the `migration:` block)

The CUT hands the harness **declarative artifacts**, not a runnable deploy script. This
is what makes harness-applies scalable (uniform SQL execution, parallelizable).

```yaml
name: ods_invoice_migration
# no read_only -> build-and-verify mode

migration:
  # Ground truth for expectations: legacy (source) -> migrated (target). Expectations
  # in `suites` resolve against this, NOT against the DDL.
  source_map:
    - { source: "${SOURCE_DATABASE}.ods_invoice_acid", target: ods_invoice }

  # Ordered steps (v1: declared order; v2 may infer a DAG from SQL refs).
  # kind: ddl | load | transform | external
  # Every SQL artifact is written against a DATASET VARIABLE so the harness can point
  # it at the ephemeral build dataset (ref-redirection by default-dataset, NOT by
  # rewriting FROM clauses).
  steps:
    - { kind: ddl,       sql: sql/ddl/ods_invoice.sql,        target: ods_invoice }
    - { kind: load,      from: "gs://.../ods_invoice/*",      format: PARQUET, target: ods_invoice_raw }
    - { kind: transform, sql: sql/transform/ods_invoice.sql,  target: ods_invoice }
    # escape hatch for non-SQL (ETL) transforms — invoke the CUT's external job:
    # - { kind: external, run: { dataflow: "${tmpl}", params: {...} }, target: fact_x }

connections:
  source: { engine: impala }
  target: { engine: bigquery }

suites:                         # existing patterns; expectations anchored to source_map
  - { pattern: schema_conformance, target_dataset: "${BUILD_DATASET}", ... }
  - { pattern: rowcount_parity,    source_database: "${SOURCE_DATABASE}", ... }
  - { pattern: fingerprint_parity, ... }
```

**`${BUILD_DATASET}`** is injected by the orchestrator at runtime = the ephemeral
dataset it provisioned. The CUT's SQL uses the same variable (or runs with default
dataset set to it), so refs resolve to seeded/built tables with zero SQL rewriting.

## Build-and-verify orchestrator (lifecycle)

```
run_mvs(data):
  if data.migration and not data.read_only:        # build-and-verify
    build_ds = provision_build_dataset()           # reset-at-START -> clean slate
    try:
      for step in data.migration.steps:
        apply(step, build_ds)                       # verbatim; fail loud + abort on error
      report = run_suites(data.suites, ctx(build_ds))
    finally:
      teardown(build_ds)                            # guarded (see below); OPTIONAL
    return report
  else:                                             # read-only assessment (unchanged)
    return <existing path>
```

**Clean slate is required; a unique name is not.** Attribution ("this run, from
nothing, produced this") needs the dataset wiped *before* the build — not a fresh name.
Uniqueness buys exactly one thing: **parallel-run isolation**.

- **provision_build_dataset():** default = a **fixed, well-known dataset `dmt_build`,
  reset at the START of each run** — drop+recreate (or `CREATE OR REPLACE` each artifact
  + drop orphans not in this bundle). Labeled `dmt_ephemeral=true`. Reset-at-start is
  *more* robust than relying on teardown-at-end: a crashed prior run can't poison this
  one, because we wipe before we build. No run-id needed.
  - **`--isolate` (opt-in):** use a unique per-run dataset `dmt_build_<run-id>` for
    parallel lanes (CI matrix; the shared-BQ-across-Conductor-workspaces hazard). Only
    then is a run-id needed — derive from an injected CI build id, fallback to a content
    hash of the bundle; **no `Date.now()`/random in scripts**. With `--isolate`, set an
    `expires` on the dataset as a safety net if teardown is skipped (process killed).
- **apply(step):** dispatch by `kind` to the relevant engine, run the CUT's artifact
  **verbatim**. Any error ⇒ ERROR + abort remaining steps (a half-built target must not
  be judged as if complete).
- **teardown():** with reset-at-start, teardown is **optional** — leaving `dmt_build`
  resident aids post-failure inspection, and the next run resets it anyway. Under
  `--isolate`, teardown `delete_dataset(delete_contents=True)` so per-run datasets don't
  accumulate (expiry is the backstop).

### Teardown guard (invariant #4, safety)

Both the start-of-run reset and the optional teardown refuse to drop/wipe a dataset
unless it carries the `dmt_ephemeral=true` label AND its name is `dmt_build`
(or matches the `dmt_build_*` prefix under `--isolate`). This makes it **structurally
impossible** for build-and-verify to wipe a real/named target if a spec is
misconfigured. A named (non-ephemeral) target ⇒ reset/teardown is a no-op + a loud
warning (build-and-verify must own its slate; it never builds into someone's real
dataset).

## New patterns: the ELT unit tier

The smallest apply-and-read-back. Hermetic (BQ-only), seconds, ~$0, CI-native — the
**most numerous** tier and where transform bugs should be caught earliest.

### `transform_unit` — given → T → expect

```yaml
pattern: transform_unit
given:                          # seed tiny known inputs into the ephemeral dataset
  ods_invoice_raw:
    columns: [ { name: invoice_id, type: INT64 }, { name: issued_ms, type: INT64 },
               { name: amount, type: NUMERIC, scale: 2 }, { name: op, type: STRING } ]
    rows:
      - { invoice_id: 5001, issued_ms: 1780272000000, amount: "100.00", op: "I" }
      - { invoice_id: 5003, issued_ms: 1780374600000, amount: "200.50", op: "U" }
transform: sql/transform/ods_invoice.sql       # the CUT's T, applied VERBATIM
expect:                         # author-declared truth (NOT a snapshot of the run)
  table: ods_invoice
  rows:                         # set-equality, order-independent, CANONICALIZED
    - { invoice_id: 5001, amount: "100.00", issued_ts: "2026-06-01T00:00:00Z" }
    - { invoice_id: 5003, amount: "200.50", issued_ts: "2026-06-02T06:30:00Z" }
  # OR property assertions instead of exact rows:
  # assert: [ { rowcount: 2 }, { unique: [invoice_id] }, { no_nulls: [issued_ts] } ]
```

- **Compare** via `lib/canonicalize.py` so float/timestamp/decimal/null compare cleanly.
- **Two expectation styles:** exact `rows` (small deterministic Ts) or `assert`
  properties (large/derived outputs). Support both.
- **Correctness vs regression:** `expect` authored *independently* = a correctness test;
  a captured snapshot of a past run = a regression test (pins behavior, doesn't prove
  correctness). Specs SHOULD declare which; the differential variant below sidesteps it.

### `transform_diff` — equivalence of the rewrite (the killer variant)

Feed the **same controlled `given`** to both transforms; assert canonicalized outputs
are identical. Proves the rewrite is behaviorally equivalent to the original on
controlled input — stronger than production parity (no input drift to confound it),
and it answers the #1 migration risk directly.

```yaml
pattern: transform_diff
given: { ods_invoice_raw: { columns: [...], rows: [...] } }
legacy_transform:   hql/ods_invoice.hql            # runs on Impala/Hive
migrated_transform: sql/transform/ods_invoice.sql  # runs on BigQuery
# assert: canonicalized(legacy_out) == canonicalized(migrated_out)
```

Tiny data, both engines. The legacy T is the independent oracle, so no hand-authored
`expect` is needed.

## Where each pattern is `mutates`

- `transform_unit`, `transform_diff`, and any build-and-verify run **seed + apply** ⇒
  `mutates=true` ⇒ blocked under `read_only` (can't run against prod; they need a
  throwaway slate). This is correct and automatic via the existing registry flag.

## Test pyramid (what this completes)

| Tier | Pattern(s) | Needs | Count |
|---|---|---|---|
| **Unit** | `transform_unit`, `transform_diff` | BQ only (diff: + Impala), ¢ | **many** |
| **Integration** | rowcount/aggregate/fingerprint parity | both engines, real data | fewer |
| **System** | E2E pipeline (deferred) | Composer/Dataflow/Run | very few |

We are currently heavy on integration and missing the cheap, numerous base. This adds it.

## Reuse map (so the build is small)

- `tests/bq_to_bq/` already does BQ→BQ — `transform_unit` generalizes it (+ `given`
  seeding, + `expect` assertion).
- `lib/canonicalize.py` — cross-dialect-safe compare (unit + diff).
- `lib/bqload.py` — `kind: load` steps.
- Ephemeral provision/teardown — new, small; shared by build-and-verify + unit tier.
- Registry `mutates` flag + `read_only` guard — already exist; new patterns just set it.
- `lib/mvs.py` `expand_env` / envelope schema — add `migration` + `${BUILD_DATASET}`.

## Open decisions (with proposed defaults)

1. **Step ordering** — declared order (v1, simple, scales to hundreds) vs DAG inferred
   from SQL refs (v2, dbt-style, thousands but needs the parser). → **Start declared.**
2. **Build-dataset strategy** — fixed `dmt_build` reset-at-start (simple, inspectable,
   no run-id) vs unique per run (parallel-safe). → **Fixed + reset-at-start by default;
   `--isolate` for parallel lanes.** (Decided — see orchestrator section.)
3. **Ref-redirection** — default-dataset / `${BUILD_DATASET}` variable (clean) vs
   rewriting `FROM` clauses (fragile, even with our parser). → **Variable; never rewrite.**
4. **Run-id source (only under `--isolate`)** — injected (CI build id / env) or
   content-hash of the bundle (no `Date.now()`/random in scripts). → **Injected,
   fallback to bundle hash.** Not needed in the default fixed-dataset mode.
5. **Big-table build cost** — the build itself costs $ at scale; lean on existing
   pushdown/smart-diff for *verification*, and partition-subset/sampling for the *build*
   in CI tiers. → **CI builds a bounded subset; full build is an opt-in tier.**

## Non-goals (deferred; design captured elsewhere in the thread)

- **E2E behavioral pipeline** (Composer/Dataflow/Cloud Run/array jobs): build only at a
  real cutover rehearsal, only the adapters that pipeline uses. `kind: external` is the
  seam when needed.
- **Terraform plan validation:** weakest tier; if anything, post-apply structural
  *describe* (read-only) — not now.

## Build plan (proven the usual way: golden + negative, green on real BQ)

- **Phase 1 — spine:** ephemeral provision + teardown (+ guard) + `migration:` envelope
  + the build-and-verify branch in `run_mvs`. Re-express one fixture transform as a CUT
  bundle; prove a clean build → verify → teardown cycle green.
- **Phase 2 — unit tier:** `transform_unit` (given→T→expect, canonicalized) + a negative
  twin (buggy T fails). Riding on Phase-1 ephemeral.
- **Phase 3 — equivalence:** `transform_diff` (legacy vs migrated, identical input).
- **Self-test convergence:** the fixture loader becomes a CUT bundle; negative twins
  become a "buggy CUT" the harness must catch. One code path, proven by good + bad CUTs.
- Update `COVERAGE.md` + meta sign-off to include the new tier.
