"""Engine adapters: a uniform query + introspect API over BigQuery and Impala/Hive.

Pattern modules talk only to this interface, never to a driver directly, so dialect
differences (introspection, type spelling, write path) live in one place.

  - BigQueryEngine  — target; reads + introspection via google-cloud-bigquery.
  - ImpalaEngine    — legacy; analytic READS over Impala HS2 :21050 (NoSASL).
  - HiveEngine      — legacy; WRITES/seeding over Hive HS2 :10000 (NoSASL), incl.
                      bucketed/SERDE/managed tables Impala refuses.

Type spellings are normalized to a small logical-type vocabulary (LogicalType) so
schema conformance compares logical types, not raw dialect strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .config import BigQueryConfig, ImpalaConfig

# ---------------------------------------------------------------------------
# Logical type vocabulary — the cross-dialect comparison currency.
# ---------------------------------------------------------------------------

# Map raw dialect type names (lowercased, params stripped) -> logical type.
_LOGICAL_MAP = {
    # integers (Hive TINYINT/SMALLINT/INT/BIGINT, BQ INT64/INTEGER)
    "tinyint": "INT64", "smallint": "INT64", "int": "INT64", "integer": "INT64",
    "bigint": "INT64", "int64": "INT64",
    # floats
    "float": "FLOAT64", "double": "FLOAT64", "double precision": "FLOAT64",
    "real": "FLOAT64", "float64": "FLOAT64",
    # exact decimal
    "decimal": "NUMERIC", "numeric": "NUMERIC", "bignumeric": "NUMERIC",
    # strings
    "string": "STRING", "varchar": "STRING", "char": "STRING", "text": "STRING",
    # bytes
    "binary": "BYTES", "bytes": "BYTES",
    # bool
    "boolean": "BOOL", "bool": "BOOL",
    # temporal
    "timestamp": "TIMESTAMP", "datetime": "DATETIME", "date": "DATE", "time": "TIME",
    # complex
    "array": "ARRAY", "struct": "STRUCT", "record": "STRUCT", "map": "MAP",
    "json": "JSON",
}

_DECIMAL_RE = re.compile(r"(?:decimal|numeric|bignumeric)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", re.I)


def normalize_type(raw: str) -> "LogicalType":
    """Parse a dialect type string into a LogicalType (logical name + decimal p/s)."""
    raw = (raw or "").strip()
    low = raw.lower()
    m = _DECIMAL_RE.search(low)
    if m:
        return LogicalType("NUMERIC", precision=int(m.group(1)), scale=int(m.group(2)), raw=raw)
    # strip any "(...)" params and trailing "<...>" for complex types
    base = re.split(r"[(<]", low, 1)[0].strip()
    logical = _LOGICAL_MAP.get(base, base.upper())
    return LogicalType(logical, raw=raw)


def _split_top_level(body: str) -> list[str]:
    """Split a comma list at angle/paren depth 0 (so STRUCT<a INT64, b STRUCT<...>> splits
    into its top-level fields, not on the commas nested inside)."""
    out, depth, cur = [], 0, []
    for ch in body:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def type_signature(decl: str) -> str:
    """Canonical nested signature for a declared type string, e.g.
    'ARRAY<STRUCT<id INT64, qty INT64>>' -> 'ARRAY<STRUCT<id:INT64,qty:INT64>>'.
    Scalars normalize to their logical name (INTEGER->INT64); field names lowercase.
    This is the SAME shape BigQueryEngine._field_signature builds from live metadata,
    so the two compare directly — catching corruption INSIDE arrays/structs."""
    s = decl.strip()
    low = s.lower()
    if low.startswith("array<") and s.endswith(">"):
        return f"ARRAY<{type_signature(s[s.index('<') + 1:-1])}>"
    if (low.startswith("struct<") or low.startswith("record<")) and s.endswith(">"):
        parts = []
        for fld in _split_top_level(s[s.index("<") + 1:-1]):
            fld = fld.strip()
            if ":" in fld and " " not in fld.split(":", 1)[0].strip():
                name, ftype = fld.split(":", 1)
            else:
                name, _, ftype = fld.partition(" ")
            parts.append(f"{name.strip().lower()}:{type_signature(ftype.strip())}")
        return f"STRUCT<{','.join(parts)}>"
    return normalize_type(s).name


@dataclass(frozen=True)
class LogicalType:
    name: str                 # logical type, e.g. INT64 / NUMERIC / TIMESTAMP / STRUCT
    precision: int | None = None
    scale: int | None = None
    raw: str = ""

    def matches(self, other: "LogicalType", check_decimal_scale: bool = True) -> bool:
        if self.name != other.name:
            return False
        if self.name == "NUMERIC" and check_decimal_scale:
            # Compare scale (the corruption-prone dimension); precision widening is OK.
            if self.scale is not None and other.scale is not None and self.scale != other.scale:
                return False
        return True

    def __str__(self) -> str:
        if self.name == "NUMERIC" and self.precision is not None:
            return f"NUMERIC({self.precision},{self.scale})"
        return self.name


@dataclass
class ColumnInfo:
    name: str
    type: LogicalType
    nullable: bool = True
    description: str | None = None
    ordinal: int = 0
    # Canonical nested signature (e.g. "ARRAY<STRUCT<id:INT64>>") for complex types;
    # empty for scalars. Lets schema_conformance compare INSIDE arrays/structs.
    type_signature: str = ""


@dataclass
class TableInfo:
    dataset: str
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    partition_columns: list[str] = field(default_factory=list)
    cluster_columns: list[str] = field(default_factory=list)
    is_external: bool = False
    # Raw introspection markers used to detect Hive-only leftovers (SERDE/LOCATION/...).
    options: dict[str, Any] = field(default_factory=dict)

    def column(self, name: str) -> ColumnInfo | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


# ---------------------------------------------------------------------------
# Engine interface
# ---------------------------------------------------------------------------


class Engine:
    name: str = "engine"
    dialect: str = "generic"

    def query(self, sql: str) -> list[dict]:
        raise NotImplementedError

    def scalar(self, sql: str) -> Any:
        rows = self.query(sql)
        if not rows:
            return None
        return next(iter(rows[0].values()))

    def introspect_table(self, dataset: str, table: str) -> TableInfo:
        raise NotImplementedError

    def list_tables(self, dataset: str) -> list[str]:
        raise NotImplementedError

    def qualify(self, dataset: str, table: str) -> str:
        return f"{dataset}.{table}"

    # --- SQL-pushdown digest (the scale path) ---------------------------------
    # Compute an order-independent table digest ENTIRELY in the engine — one
    # aggregate query, no row egress. The per-row hash is md5(canonical row string),
    # which is byte-identical across Impala and BigQuery (verified), so two tables on
    # *different* engines are equal iff (count, sum) match. Subclasses supply the
    # dialect hooks below. This is what DVT does server-side, reimplemented (no dep).
    _BIGDEC = "DECIMAL(38,0)"
    _NULL = "__NULL__"
    _SEP = "~|~"

    def _md5_hex(self, expr: str) -> str:
        raise NotImplementedError

    def _hex15_to_int(self, hex_expr: str) -> str:
        raise NotImplementedError

    def _mod(self, a: str, n: int) -> str:
        raise NotImplementedError

    def _canon_col(self, name: str, ltype: "LogicalType | None") -> str:
        raise NotImplementedError

    def _canon_concat(self, dataset: str, table: str, columns) -> str:
        """The per-row canonical string expr (md5 input), built from introspected types."""
        tmap = {c.name.lower(): c.type for c in self.introspect_table(dataset, table).columns}
        parts = [self._canon_col(c, tmap.get(c.lower())) for c in columns]
        return parts[0] if len(parts) == 1 else "CONCAT(" + f", '{self._SEP}', ".join(parts) + ")"

    def fingerprint_pushdown(self, dataset: str, table: str, columns, where: str = "") -> dict:
        row_int = self._hex15_to_int(self._md5_hex(self._canon_concat(dataset, table, columns)))
        w = f" WHERE {where}" if where else ""
        sql = (f"SELECT COUNT(*) AS n, "
               f"CAST(SUM(CAST({row_int} AS {self._BIGDEC})) AS STRING) AS s "
               f"FROM {self.qualify(dataset, table)}{w}")
        r = self.query(sql)[0]
        return {"count": int(r["n"]), "sum": str(r["s"]) if r["s"] is not None else "0"}

    # --- segmented localization (smart diff at scale) -------------------------
    # Bucket rows by hash(key) % n, fold a per-bucket (count, sum) IN the engine.
    # Comparing buckets across engines narrows mismatches to a few buckets without
    # egress; only those buckets are drilled for the exact differing keys.
    def bucket_digests(self, dataset: str, table: str, columns, key: str, n: int) -> dict:
        bucket = self._mod(self._hex15_to_int(self._md5_hex(f"CAST({key} AS STRING)")), n)
        row_int = self._hex15_to_int(self._md5_hex(self._canon_concat(dataset, table, columns)))
        sql = (f"SELECT {bucket} AS b, COUNT(*) AS n, "
               f"CAST(SUM(CAST({row_int} AS {self._BIGDEC})) AS STRING) AS s "
               f"FROM {self.qualify(dataset, table)} GROUP BY {bucket}")
        return {int(r["b"]): (int(r["n"]), str(r["s"])) for r in self.query(sql)}

    def bucket_keys(self, dataset: str, table: str, columns, key: str, n: int, b: int) -> dict:
        """Pull only one bucket's (key -> row md5) so the caller can diff exact keys."""
        bucket = self._mod(self._hex15_to_int(self._md5_hex(f"CAST({key} AS STRING)")), n)
        rowhex = self._md5_hex(self._canon_concat(dataset, table, columns))
        sql = (f"SELECT CAST({key} AS STRING) AS k, {rowhex} AS h "
               f"FROM {self.qualify(dataset, table)} WHERE {bucket} = {b}")
        return {r["k"]: r["h"] for r in self.query(sql)}


# ---------------------------------------------------------------------------
# BigQuery (target)
# ---------------------------------------------------------------------------


class BigQueryEngine(Engine):
    name = "bigquery"
    dialect = "bigquery"

    def __init__(self, cfg: BigQueryConfig):
        from google.cloud import bigquery

        from .gcp_auth import bigquery_credentials

        self.cfg = cfg
        self._bq = bigquery
        self.client = bigquery.Client(project=cfg.project, location=cfg.location,
                                      credentials=bigquery_credentials())

    def query(self, sql: str) -> list[dict]:
        job = self.client.query(sql)
        return [dict(row.items()) for row in job.result()]

    def qualify(self, dataset: str, table: str) -> str:
        return f"`{self.cfg.project}.{dataset}.{table}`"

    # SQL-pushdown digest hooks (md5-based, cross-engine comparable).
    _BIGDEC = "BIGNUMERIC"

    def _md5_hex(self, expr: str) -> str:
        return f"TO_HEX(MD5({expr}))"

    def _hex15_to_int(self, hex_expr: str) -> str:
        return f"CAST(CONCAT('0x', SUBSTR({hex_expr}, 1, 15)) AS INT64)"

    def _mod(self, a: str, n: int) -> str:
        return f"MOD({a}, {n})"

    def _canon_col(self, name: str, ltype) -> str:
        s = self._NULL
        if ltype is None:
            return f"COALESCE(CAST({name} AS STRING), '{s}')"
        n = ltype.name
        # Decimals: value * 10^9 as an integer string — FIXED scale (not the declared
        # one, which can differ between source/target), big type so it can't overflow.
        if n == "NUMERIC":
            return f"COALESCE(CAST(CAST(ROUND({name} * 1000000000) AS BIGNUMERIC) AS STRING), '{s}')"
        if n == "TIMESTAMP":
            return f"COALESCE(FORMAT_TIMESTAMP('%F %T', {name}, 'UTC'), '{s}')"
        if n == "FLOAT64":
            return f"COALESCE(CAST(CAST(ROUND({name} * 1000000) AS BIGNUMERIC) AS STRING), '{s}')"
        if n == "BOOL":                                      # 'true'/'false' (matches Impala's CASE form)
            return f"CASE WHEN {name} IS NULL THEN '{s}' WHEN {name} THEN 'true' ELSE 'false' END"
        return f"COALESCE(CAST({name} AS STRING), '{s}')"     # INT64/STRING/DATE/...

    def query_stats(self, sql: str, dry_run: bool = False, use_cache: bool = False,
                    max_bytes_billed: int | None = None) -> dict:
        """Run a query and return BigQuery Job-API performance stats (server-side, not
        client wall-clock). dry_run=True estimates bytes_processed for FREE without
        executing. Cache is off by default (a cache hit makes perf numbers meaningless)."""
        cfg = self._bq.QueryJobConfig(dry_run=dry_run, use_query_cache=use_cache)
        if max_bytes_billed:
            cfg.maximum_bytes_billed = max_bytes_billed
        job = self.client.query(sql, job_config=cfg)
        if dry_run:
            return {"dry_run": True, "bytes_scanned": int(job.total_bytes_processed or 0)}
        job.result()  # wait for completion; rows are NOT fetched (no egress)
        return {
            "dry_run": False,
            "bytes_scanned": int(job.total_bytes_processed or 0),   # common-core metric
            "bytes_billed": int(job.total_bytes_billed or 0),       # BigQuery-specific
            "slot_ms": int(job.slot_millis or 0),                   # BigQuery-specific
            "elapsed_ms": (job.ended - job.started).total_seconds() * 1000.0,
            "cache_hit": bool(job.cache_hit),
        }

    def list_tables(self, dataset: str) -> list[str]:
        ref = self._bq.DatasetReference(self.cfg.project, dataset)
        return sorted(t.table_id for t in self.client.list_tables(ref))

    def introspect_table(self, dataset: str, table: str) -> TableInfo:
        ref = self._bq.DatasetReference(self.cfg.project, dataset).table(table)
        t = self.client.get_table(ref)
        cols = [
            ColumnInfo(
                name=f.name,
                type=self._field_type(f),
                nullable=(f.mode != "REQUIRED"),
                description=f.description or None,
                ordinal=i,
                type_signature=self._field_signature(f),
            )
            for i, f in enumerate(t.schema)
        ]
        partition_cols: list[str] = []
        if t.time_partitioning and t.time_partitioning.field:
            partition_cols = [t.time_partitioning.field]
        elif t.time_partitioning:
            # Ingestion-time partitioning has no named field — it's the _PARTITIONTIME
            # pseudo-column. Surface it so a partition_by check doesn't read as "unpartitioned".
            partition_cols = ["_PARTITIONTIME"]
        elif t.range_partitioning and t.range_partitioning.field:
            partition_cols = [t.range_partitioning.field]
        # TABLE_OPTIONS surface — exposed by get_table (no INFORMATION_SCHEMA needed).
        # Retention (partition_expiration_days) backs the Observability/pattern-20 AC;
        # require_partition_filter + labels are captured for governance checks.
        options: dict[str, Any] = {"table_type": t.table_type}
        if t.labels:
            options["labels"] = dict(t.labels)
        rpf = getattr(t, "require_partition_filter", None)
        if rpf is not None:
            options["require_partition_filter"] = bool(rpf)
        exp_ms = getattr(t.time_partitioning, "expiration_ms", None) if t.time_partitioning else None
        if exp_ms:
            options["partition_expiration_days"] = exp_ms / 86_400_000
        return TableInfo(
            dataset=dataset,
            name=table,
            columns=cols,
            partition_columns=partition_cols,
            cluster_columns=list(t.clustering_fields or []),
            is_external=(t.table_type == "EXTERNAL"),
            options=options,
        )

    @staticmethod
    def _field_type(f) -> LogicalType:
        # BQ SchemaField.field_type spelling; ARRAY shows as mode=REPEATED on the base type.
        base = (f.field_type or "").upper()
        # mode=REPEATED means ARRAY — check it FIRST. An ARRAY<STRUCT<…>> presents as
        # mode=REPEATED with field_type=RECORD; checking RECORD first would misclassify
        # it as STRUCT and the ARRAY-ness would be lost.
        if f.mode == "REPEATED":
            return LogicalType("ARRAY", raw=f"ARRAY<{base}>")
        if base in ("RECORD", "STRUCT"):
            return LogicalType("STRUCT", raw=base)
        if base in ("NUMERIC", "BIGNUMERIC"):
            return LogicalType("NUMERIC", precision=f.precision, scale=f.scale, raw=base)
        return normalize_type(base)

    @staticmethod
    def _field_signature(f) -> str:
        """Canonical nested signature from a live SchemaField — e.g.
        'ARRAY<STRUCT<id:INT64,qty:INT64>>'. Mirrors lib.engines.type_signature so a
        declared complex type and the landed one compare element-by-element."""
        base = (f.field_type or "").upper()
        if base in ("RECORD", "STRUCT"):
            inner = ",".join(f"{s.name.lower()}:{BigQueryEngine._field_signature(s)}"
                             for s in (f.fields or []))
            sig = f"STRUCT<{inner}>"
        else:
            sig = normalize_type(base).name
        return f"ARRAY<{sig}>" if getattr(f, "mode", None) == "REPEATED" else sig


# ---------------------------------------------------------------------------
# Impala / Hive (legacy) — shared HS2 client, NoSASL
# ---------------------------------------------------------------------------


class _HS2Engine(Engine):
    def __init__(self, host: str, port: int, auth: str, database: str):
        self._host, self._port = host, port
        self._auth = "NOSASL" if (auth or "NONE").upper() in ("NONE", "NOSASL") else auth.upper()
        self.database = database

    def _connect(self):
        from impala.dbapi import connect

        return connect(host=self._host, port=self._port,
                       auth_mechanism=self._auth, database=self.database)

    def query(self, sql: str) -> list[dict]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def execute(self, *stmts: str) -> None:
        """Run write/DDL statements in a single session (used for seeding)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            for s in stmts:
                s = s.strip().rstrip(";")
                if s:
                    cur.execute(s)
        finally:
            conn.close()

    def list_tables(self, dataset: str) -> list[str]:
        rows = self.query(f"SHOW TABLES IN {dataset}")
        return sorted(next(iter(r.values())) for r in rows)

    def run_in(self, database: str, sql: str) -> list[dict]:
        """Run a query with `database` as the session default, so unqualified table
        refs in the CUT's legacy transform resolve there (HS2 analogue of BigQuery's
        default_dataset). Returns rows of the query."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"USE {database}")
            cur.execute(sql)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def introspect_table(self, dataset: str, table: str) -> TableInfo:
        # DESCRIBE FORMATTED yields columns, partition keys, table type, SERDE/location.
        # On the shared HS2 base so BOTH Impala and Hive can introspect: Impala rejects
        # Java-SerDe tables (JsonSerDe/RegexSerDe) at analysis time, so schema.py falls back
        # to the Hive engine, whose DESCRIBE reads its own SerDes (Hive-only tables in prod too).
        rows = self.query(f"DESCRIBE FORMATTED {dataset}.{table}")
        cols: list[ColumnInfo] = []
        partition_cols: list[str] = []
        options: dict[str, Any] = {}
        section = "cols"
        ordinal = 0
        for r in rows:
            vals = list(r.values())
            c0 = (str(vals[0]) if vals and vals[0] is not None else "").strip()
            c1 = (str(vals[1]) if len(vals) > 1 and vals[1] is not None else "").strip()
            if c0.startswith("# Partition Information"):
                section = "partitions"
                continue
            if c0.startswith("# Detailed Table Information") or c0.startswith("# Storage"):
                section = "detail"
                continue
            if not c0 or c0.startswith("# col_name"):
                continue
            if section == "cols" and c1:
                cols.append(ColumnInfo(name=c0, type=normalize_type(c1), ordinal=ordinal,
                                       description=(str(vals[2]).strip() if len(vals) > 2 and vals[2] else None)))
                ordinal += 1
            elif section == "partitions" and c1 and not c0.startswith("#"):
                if c0 not in [c.name for c in cols]:
                    cols.append(ColumnInfo(name=c0, type=normalize_type(c1), ordinal=ordinal))
                    ordinal += 1
                partition_cols.append(c0)
            elif section == "detail":
                key = c0.rstrip(":")
                if key in ("Location", "Table Type", "SerDe Library", "InputFormat") and c1:
                    options[key] = c1
        is_external = "EXTERNAL" in str(options.get("Table Type", "")).upper()
        return TableInfo(dataset=dataset, name=table, columns=cols,
                         partition_columns=partition_cols, is_external=is_external, options=options)


_PAREN_RE = re.compile(r"\((\d+)\)")
_TIME_RE = re.compile(r"([\d.]+)(h|ms|us|ns|m|s)")
_TIME_MS = {"h": 3_600_000, "m": 60_000, "s": 1000, "ms": 1, "us": 0.001, "ns": 0.000001}


def _profile_max_paren(profile: str, counter: str) -> int:
    """Max parenthesized raw value across all lines naming `counter` (the query-level
    aggregate is the largest)."""
    vals = [int(m) for line in profile.splitlines() if counter in line
            for m in _PAREN_RE.findall(line)]
    return max(vals) if vals else 0


def _profile_max_time_ms(profile: str, counter: str) -> float:
    """Max time (ms) across lines naming `counter`. Parses Impala's '3s942ms' / '301.611ms'."""
    best = 0.0
    for line in profile.splitlines():
        if counter not in line:
            continue
        ms = sum(float(n) * _TIME_MS[u] for n, u in _TIME_RE.findall(line.split(":", 1)[-1]))
        best = max(best, ms)
    return best


class ImpalaEngine(_HS2Engine):
    name = "impala"
    dialect = "impala"

    def __init__(self, cfg: ImpalaConfig):
        super().__init__(cfg.host, cfg.impala_port, cfg.auth, cfg.database)
        self.cfg = cfg

    # SQL-pushdown digest hooks — md5 + hex->int match BigQuery's exactly (verified).
    _BIGDEC = "DECIMAL(38,0)"

    def _md5_hex(self, expr: str) -> str:
        return f"md5({expr})"

    def _hex15_to_int(self, hex_expr: str) -> str:
        return f"conv(substr({hex_expr}, 1, 15), 16, 10)"

    def _mod(self, a: str, n: int) -> str:
        return f"pmod(CAST({a} AS BIGINT), {n})"

    def query_stats(self, sql: str, dry_run: bool = False, use_cache: bool = False,
                    max_bytes_billed: int | None = None) -> dict:
        """Impala perf stats from the runtime profile (impyla get_profile). Common core
        (bytes_scanned, elapsed_ms) + Impala extras (peak_memory, cpu_ms). No dry-run:
        Impala's planner gives estimates only, so you must execute to measure."""
        if dry_run:
            raise RuntimeError("dry_run not supported on Impala (planner estimates only; execute to measure)")
        import time
        conn = self._connect()
        try:
            cur = conn.cursor()
            t0 = time.time()
            cur.execute(sql)
            while cur.fetchmany(10000):   # force full execution, bounded client memory
                pass
            elapsed_ms = (time.time() - t0) * 1000.0
            profile = cur.get_profile()
        finally:
            conn.close()
        return {
            "dry_run": False,
            "bytes_scanned": _profile_max_paren(profile, "TotalBytesRead"),
            "peak_memory": _profile_max_paren(profile, "PeakMemoryUsage"),
            "cpu_ms": _profile_max_time_ms(profile, "TotalCpuTime"),
            "elapsed_ms": elapsed_ms,
        }

    def _canon_col(self, name: str, ltype) -> str:
        s = self._NULL
        if ltype is None:
            return f"COALESCE(CAST({name} AS STRING), '{s}')"
        n = ltype.name
        # Decimals: value * 10^9 as integer string — fixed scale (declared scale can
        # differ across engines), DECIMAL(38,0) so it can't overflow.
        if n == "NUMERIC":
            return f"COALESCE(CAST(CAST(round({name} * 1000000000) AS DECIMAL(38,0)) AS STRING), '{s}')"
        if n == "TIMESTAMP":
            return f"COALESCE(from_timestamp({name}, 'yyyy-MM-dd HH:mm:ss'), '{s}')"
        if n == "FLOAT64":
            return f"COALESCE(CAST(CAST(round({name} * 1000000) AS DECIMAL(38,0)) AS STRING), '{s}')"
        if n == "BOOL":                                      # Impala CAST(bool)->'0'/'1'; normalize to true/false
            return f"CASE WHEN {name} IS NULL THEN '{s}' WHEN {name} THEN 'true' ELSE 'false' END"
        return f"COALESCE(CAST({name} AS STRING), '{s}')"


class HiveEngine(_HS2Engine):
    name = "hive"
    dialect = "hive"

    def __init__(self, cfg: ImpalaConfig):
        super().__init__(cfg.hive_host, cfg.hive_port, cfg.auth, cfg.database)
        self.cfg = cfg

    def execute(self, *stmts: str) -> None:
        # Golden image predates the stats-autogather fix: managed INSERTs can report a
        # false StatsTask failure (README seeding caveat). Disable per session.
        super().execute("SET hive.stats.autogather=false", *stmts)


# ---------------------------------------------------------------------------
# Cloud Composer (Airflow) — orchestration engine (real customer env)
# ---------------------------------------------------------------------------

def parse_dags_list(text: str) -> list[str]:
    """Parse `airflow dags list` (via gcloud composer run) -> dag_ids. Skips Airflow's
    built-in airflow_monitoring DAG and all the log/INFO noise gcloud interleaves."""
    out, in_table = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("dag_id") and "|" in line:
            in_table = True
            continue
        if not in_table or set(line.strip()) <= {"=", "+"} or not line.strip():
            continue
        if "|" not in line:
            break
        dag_id = line.split("|", 1)[0].strip()
        if dag_id and dag_id != "airflow_monitoring":
            out.append(dag_id)
    return out


def parse_import_errors(text: str) -> list[str]:
    """Parse `airflow dags list-import-errors` -> the .py filepaths that failed to import."""
    import re as _re
    seen, out = set(), []
    for m in _re.finditer(r"(/[^\s|]+\.py)\s*\|", text):
        f = m.group(1)
        if f not in seen:
            seen.add(f); out.append(f)
    return out


class ComposerEngine:
    """Talks to a *real* Cloud Composer env via `gcloud composer environments run`
    (uses gcloud creds, no IAP token wrangling; authoritative — runs inside the env so
    the customer's own providers resolve). Read-only describe adapter for DAG config."""
    name = "composer"

    def __init__(self, cfg: "ComposerConfig"):
        self.cfg = cfg

    def _run(self, *airflow_cmd: str) -> str:
        import subprocess
        cmd = ["gcloud", "composer", "environments", "run", self.cfg.environment,
               "--location", self.cfg.location, "--project", self.cfg.project, *airflow_cmd]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout + "\n" + r.stderr   # gcloud interleaves the airflow table across both

    def list_dags(self) -> list[str]:
        return parse_dags_list(self._run("dags", "list"))

    def import_errors(self) -> list[str]:
        return parse_import_errors(self._run("dags", "list-import-errors"))
