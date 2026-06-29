"""Mode-2 build-and-verify: the harness applies the CUT's artifacts against a CLEAN
build dataset, then the suites verify the result, then (optionally) teardown.

Design: DESIGN-MODE2.md. The line that keeps this honest is **apply != author** — the
harness runs the CUT's DDL/ELT SQL *verbatim*; it never writes it, and the suite
expectations are anchored to source ground truth, never to the DDL.

Clean slate is required; a unique name is not. Default = a fixed, well-known dataset
`dmt_build` reset at the START of each run (so a crashed prior run can't poison this
one). `--isolate` (migration.isolate) uses a unique `dmt_build_<id>` for parallel lanes.

A guard makes it structurally impossible to reset/drop anything that isn't a build
dataset (name must be `dmt_build` or `dmt_build_*` AND carry the dmt_ephemeral label).
"""
from __future__ import annotations

import os
import re
from decimal import Decimal
from pathlib import Path

from .mvs import expand_env

BUILD_DS_DEFAULT = "dmt_build"
LABEL_KEY = "dmt_ephemeral"

# --- source_setup: stand up the legacy source from its real DDL, then tear it down -------
# Lets schema_conformance's source cross-check run against a sandbox source built from the
# legacy DDL (the agent lists the files it converted), WITHOUT a live legacy DB. ON BY DEFAULT
# (our source is a sandbox we provision) — opt OUT with DMT_SOURCE_SETUP=0 to point at a REAL
# legacy you must NOT write to (then the existing source is read as-is, never mutated).
SOURCE_SETUP_ENV = "DMT_SOURCE_SETUP"          # default ON; set =0/false to skip (real read-only legacy)
_SOURCE_SETUP_OFF = {"0", "false", "no", "off", ""}
# Rehost legacy DDL onto OUR warehouse so it applies without the original cluster: rewrite any
# off-cluster URI under LOCATION (hdfs://host/…, s3://…) to our warehouse base (table stays
# EXTERNAL), and flip ACID tables to non-ACID.
_LEGACY_URI_RE = re.compile(r"\b[a-z0-9]+://[^/'\s]+/", re.I)
_ACID_RE = re.compile(r"('transactional'\s*=\s*)'true'", re.I)
_SETUP_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*", re.S)   # Hive/Impala comments ('#' is not one)
_CREATE_TABLE_RE = re.compile(r"CREATE\s+(?:EXTERNAL\s+|TEMPORARY\s+)*TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\w.]+)", re.I)
_CREATE_DB_RE = re.compile(r"CREATE\s+(?:DATABASE|SCHEMA)\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\w]+)", re.I)


def _sanitize_source_ddl(sql: str, location_base: str) -> str:
    """Rehost legacy DDL onto OUR warehouse so it applies without the original cluster: rewrite any
    off-cluster URI under LOCATION (hdfs://host/…, s3://…) to our warehouse base, keeping the table
    EXTERNAL. Also flip ACID `'transactional'='true'` to `'false'` (our HS2 client cannot create
    ACID; the schema — columns/types — is identical either way)."""
    sql = _LEGACY_URI_RE.sub(location_base.rstrip("/") + "/", sql)   # repoint LOCATION → our warehouse
    sql = _ACID_RE.sub(r"\1'false'", sql)                            # ACID → non-ACID
    return sql


def _split_statements(sql: str) -> list[str]:
    """Strip comments, then split on TOP-LEVEL ';' — RESPECTING string literals, because a SerDe
    property (e.g. a RegexSerDe `input.regex`) can itself contain ';'. A naive split shreds it."""
    clean = _SETUP_COMMENT_RE.sub("", sql)
    stmts: list[str] = []
    buf: list[str] = []
    quote = None
    prev = ""
    for ch in clean:
        if quote:
            buf.append(ch)
            if ch == quote and prev != "\\":
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            s = "".join(buf).strip()
            if s:
                stmts.append(s)
            buf = []
        else:
            buf.append(ch)
        prev = ch
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def setup_source(ctx, source_setup: dict, base_dir: str = ".") -> list[tuple[str, str]]:
    """Apply the legacy source DDL files to a sandbox source so the source cross-check has a
    live source to read. ON BY DEFAULT (our source is a sandbox we provision from the legacy
    DDL); opt OUT with DMT_SOURCE_SETUP=0 when pointed at a REAL legacy you must not write to
    (then it's a no-op and the existing source is read as-is). Writes via the Hive engine;
    INVALIDATE METADATA so an Impala read engine sees the tables. Returns the created
    ('database'|'table', name) objects for teardown."""
    if os.environ.get(SOURCE_SETUP_ENV, "1").strip().lower() in _SOURCE_SETUP_OFF:
        return []
    location_base = (source_setup.get("location_base")
                     or os.environ.get("DMT_SOURCE_LOCATION_BASE")
                     or "file:/user/hive/warehouse/external")   # our Hive warehouse (externals live here)
    created: list[tuple[str, str]] = []
    try:
        for path in source_setup.get("ddl", []):
            # pin encoding: legacy DDL is UTF-8, not the platform default (cp1252 on Windows)
            sql = _sanitize_source_ddl(expand_env((Path(base_dir) / path).read_text(encoding="utf-8")), location_base)
            # Record what this file creates BEFORE executing — so a mid-file failure still leaves the
            # names to tear down (teardown uses DROP IF EXISTS, safe for any not-yet-made). Parse the
            # COMMENT-STRIPPED sql so a commented-out `-- CREATE TABLE …` is never tracked (and dropped).
            clean_sql = _SETUP_COMMENT_RE.sub("", sql)
            created += [("database", m.group(1).strip("`")) for m in _CREATE_DB_RE.finditer(clean_sql)]
            created += [("table", m.group(1).strip("`")) for m in _CREATE_TABLE_RE.finditer(clean_sql)]
            for stmt in _split_statements(sql):
                ctx.hive.execute(stmt)
        if ctx.source_kind == "impala":
            ctx.source.query("INVALIDATE METADATA")
    except Exception:
        # partial failure (bad DDL, lost connection): drop whatever we already made so the
        # sandbox isn't left polluted, then propagate — the caller never sees a half-built source
        teardown_source(ctx, created)
        raise
    return created


def teardown_source(ctx, created: list[tuple[str, str]]) -> None:
    """Drop everything setup_source created (best-effort, in a finally) — tables first, then
    the databases (CASCADE). Nothing is left in the sandbox source, pass or fail."""
    for _, name in [c for c in created if c[0] == "table"]:
        try:
            ctx.hive.execute(f"DROP TABLE IF EXISTS {name}")
        except Exception:  # noqa: BLE001 — teardown is best-effort; never mask the result
            pass
    for _, name in [c for c in created if c[0] == "database"]:
        try:
            ctx.hive.execute(f"DROP DATABASE IF EXISTS {name} CASCADE")
        except Exception:  # noqa: BLE001
            pass
    if ctx.source_kind == "impala":
        try:
            ctx.source.query("INVALIDATE METADATA")
        except Exception:  # noqa: BLE001
            pass
LABEL_VAL = "true"

# GCS bulk-load source formats accepted in a `kind: load` step.
_LOAD_FORMATS = {"CSV": "CSV", "PARQUET": "PARQUET", "JSON": "NEWLINE_DELIMITED_JSON",
                 "AVRO": "AVRO", "ORC": "ORC"}


class BuildError(RuntimeError):
    pass


class BuildGuardError(BuildError):
    """Raised when something asks to reset/drop a dataset that is not a build dataset."""


def _guard(name: str) -> None:
    if name != BUILD_DS_DEFAULT and not name.startswith(BUILD_DS_DEFAULT + "_"):
        raise BuildGuardError(
            f"refusing to create/reset/drop '{name}': build-and-verify only owns "
            f"'{BUILD_DS_DEFAULT}' or '{BUILD_DS_DEFAULT}_*' datasets (it must never "
            f"build into or wipe a real/named target)")


def _ref(bq, name: str):
    return bq._bq.DatasetReference(bq.cfg.project, name)


def provision_build_dataset(bq, name: str = BUILD_DS_DEFAULT) -> str:
    """Reset-at-start: drop the build dataset if present, recreate it empty + labeled.
    Refuses to touch a same-named dataset that lacks the ephemeral label (it could be a
    real dataset someone happened to name `dmt_build`)."""
    from google.api_core.exceptions import NotFound

    _guard(name)
    ref = _ref(bq, name)
    try:
        existing = bq.client.get_dataset(ref)
        if (existing.labels or {}).get(LABEL_KEY) != LABEL_VAL:
            raise BuildGuardError(
                f"refusing to reset '{name}': it exists without the {LABEL_KEY}={LABEL_VAL} "
                f"label, so it is not a build dataset (it may be real). Drop it manually "
                f"or pick another build dataset name.")
        bq.client.delete_dataset(ref, delete_contents=True, not_found_ok=True)
    except NotFound:
        pass
    ds = bq._bq.Dataset(ref)
    ds.location = bq.cfg.location
    ds.labels = {LABEL_KEY: LABEL_VAL}
    bq.client.create_dataset(ds, exists_ok=False)
    return name


def teardown(bq, name: str) -> None:
    """Drop a build dataset. Guarded by name; no-op-safe if already gone."""
    _guard(name)
    bq.client.delete_dataset(_ref(bq, name), delete_contents=True, not_found_ok=True)


def run_sql(bq, build_ds: str, sql: str):
    """Run SQL with default dataset = build_ds, so the CUT's unqualified table refs
    redirect to the build dataset (ref-redirection by default-dataset, NOT by rewriting
    FROM clauses)."""
    job_cfg = bq._bq.QueryJobConfig(default_dataset=_ref(bq, build_ds))
    return bq.client.query(sql, job_config=job_cfg).result()


def query_sql(bq, build_ds: str, sql: str) -> list[dict]:
    """run_sql but return rows (for a SELECT whose results we compare)."""
    job_cfg = bq._bq.QueryJobConfig(default_dataset=_ref(bq, build_ds))
    return [dict(r.items()) for r in bq.client.query(sql, job_config=job_cfg).result()]


# ---------------------------------------------------------------------------
# Declarative cross-engine seeding: one `given` (columns + rows) -> tables in
# either BigQuery (target) or an HS2 engine (Impala/Hive source). This is what
# lets a spec stand up source AND destination tables itself (no Python loader).
# ---------------------------------------------------------------------------

_BQ_TYPE = {"INT64": "INTEGER", "FLOAT64": "FLOAT", "BOOL": "BOOLEAN"}
_HS2_TYPE = {"INT64": "BIGINT", "FLOAT64": "DOUBLE", "BOOL": "BOOLEAN",
             "STRING": "STRING", "TIMESTAMP": "TIMESTAMP", "DATE": "DATE", "INT": "INT"}
_DECIMALS = {"NUMERIC", "DECIMAL", "BIGNUMERIC"}


def _hs2_type(col: dict) -> str:
    t = col["type"].upper()
    if t in _DECIMALS:
        return f"DECIMAL(38,{col.get('scale', 9)})"
    return _HS2_TYPE.get(t, t)


def _hs2_lit(v, coltype: str) -> str:
    if v is None:
        return "NULL"
    t = coltype.upper()
    if t in ("BOOL", "BOOLEAN"):
        return "true" if v else "false"
    if t in _DECIMALS:
        return str(Decimal(str(v)))
    if t == "TIMESTAMP":
        s = str(v).replace("T", " ").replace("Z", "").split("+")[0].strip()
        return f"CAST('{s}' AS TIMESTAMP)"
    if t == "DATE":
        return f"CAST('{v}' AS DATE)"
    if t in ("INT64", "INT", "BIGINT", "FLOAT64", "DOUBLE"):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def seed_bigquery(bq, dataset: str, name: str, table: dict) -> None:
    fields = [bq._bq.SchemaField(c["name"], _BQ_TYPE.get(c["type"].upper(), c["type"].upper()),
                                 mode=c.get("mode", "NULLABLE")) for c in table["columns"]]
    ref = _ref(bq, dataset).table(name)
    job_cfg = bq._bq.LoadJobConfig(schema=fields, write_disposition="WRITE_TRUNCATE")
    bq.client.load_table_from_json(table["rows"], ref, job_config=job_cfg).result()


def seed_hs2(hs2, db: str, name: str, table: dict) -> None:
    cols = table["columns"]
    ddl = ", ".join(f"{c['name']} {_hs2_type(c)}" for c in cols)
    types = {c["name"]: c["type"] for c in cols}
    stmts = [f"DROP TABLE IF EXISTS {db}.{name}",
             f"CREATE TABLE {db}.{name} ({ddl}) STORED AS PARQUET"]
    if table["rows"]:
        vals = ",\n".join("(" + ",".join(_hs2_lit(r.get(c["name"]), types[c["name"]]) for c in cols) + ")"
                          for r in table["rows"])
        stmts.append(f"INSERT INTO {db}.{name} VALUES\n{vals}")
    hs2.execute(*stmts)


def seed_given(engine, dataset: str, given: dict) -> None:
    """Seed every table in `given` into `dataset` on `engine` (BigQuery or HS2)."""
    for name, table in given.items():
        if getattr(engine, "name", "") == "bigquery":
            seed_bigquery(engine, dataset, name, table)
        else:
            seed_hs2(engine, dataset, name, table)


def sql_from(spec: dict, path_key: str, text_key: str, base_dir: str = ".") -> str | None:
    """Canonical SQL resolution shared by every pattern: a `<text_key>` inline string,
    or a `<path_key>` path to a .sql file (the CUT's artifact). env-expanded. Returns
    None if neither is set. Keeps SQL-passing uniform across patterns."""
    if spec.get(text_key):
        return expand_env(spec[text_key])
    if spec.get(path_key):
        return expand_env((Path(base_dir) / spec[path_key]).read_text())
    return None


def _step_sql(step: dict, base_dir: str) -> str:
    sql = sql_from(step, "sql", "sql_text", base_dir)
    if sql is None:
        raise BuildError(f"{step.get('kind')} step needs 'sql' (path) or 'sql_text' (inline)")
    return sql


def _load(bq, build_ds: str, step: dict) -> None:
    fmt = str(step.get("format", "CSV")).upper()
    if fmt not in _LOAD_FORMATS:
        raise BuildError(f"unsupported load format '{fmt}' (one of {sorted(_LOAD_FORMATS)})")
    job_cfg = bq._bq.LoadJobConfig(
        source_format=getattr(bq._bq.SourceFormat, _LOAD_FORMATS[fmt]),
        autodetect=True,
        write_disposition="WRITE_TRUNCATE",
    )
    ref = _ref(bq, build_ds).table(step["target"])
    bq.client.load_table_from_uri(step["from"], ref, job_config=job_cfg).result()


def apply_step(bq, build_ds: str, step: dict, base_dir: str = ".") -> None:
    """Apply one migration step VERBATIM. Any error propagates (the orchestrator aborts
    the remaining steps — a half-built target must not be judged as complete)."""
    kind = step.get("kind")
    if kind in ("ddl", "transform"):
        run_sql(bq, build_ds, _step_sql(step, base_dir))
    elif kind == "load":
        _load(bq, build_ds, step)
    elif kind == "external":
        raise BuildError("kind 'external' (non-SQL/ETL transforms) is not supported yet "
                         "— use the E2E adapters when built (see DESIGN-MODE2 non-goals)")
    else:
        raise BuildError(f"unknown migration step kind: {kind!r}")
