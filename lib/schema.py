"""Pattern 7 — Schema / DDL conformance (Axis B, P0-1).

Asserts the migrated BigQuery target exists *correctly* before any data flows:
exact table set, logical type mapping (BIGINT->INT64, DECIMAL scale preserved),
partition + clustering columns, column rename + description, and absence of
Hive-only directives (the target is a native managed table, not an EXTERNAL one
pointing back at the old LOCATION/SERDE).

Fully declarative (SPEC §5.1): the agent supplies the expected table set, type map,
and partition/cluster/desc. Optional source_database/source_table cross-checks the
legacy type against the declared mapping.
"""
from __future__ import annotations

from .engines import LogicalType, TableInfo, normalize_type, type_signature
from .harness import Context, require
from .registry import register
from .report import CheckResult, Status, SuiteResult

SCHEMA = {
    "type": "object",
    "required": ["pattern", "target_dataset", "tables"],
    "properties": {
        "pattern": {"const": "schema_conformance"},
        "id": {"type": "string"},
        "story_id": {"type": "string"},
        "target_dataset": {"type": "string"},
        "source_database": {"type": "string"},
        "expect_table_count": {"type": "integer", "minimum": 0},
        "tables": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["table", "columns"],
                "properties": {
                    "table": {"type": "string"},
                    "source_table": {"type": "string"},
                    # Object-type fidelity: assert the landed object is a TABLE / VIEW /
                    # MATERIALIZED_VIEW, so a source table silently flipped to a view fails.
                    "expect_object_type": {"enum": ["TABLE", "VIEW", "MATERIALIZED_VIEW"]},
                    "partition_by": {"type": "string"},
                    "cluster_by": {"type": "array", "items": {"type": "string"}},
                    "no_hive_directives": {"type": "boolean"},
                    # TABLE_OPTIONS assertions (all optional). expiration backs the
                    # retention AC (pattern 20); the others are governance checks.
                    "table_options": {
                        "type": "object",
                        "properties": {
                            "partition_expiration_days": {"type": ["number", "null"]},
                            "require_partition_filter": {"type": "boolean"},
                            "labels": {"type": "object"},
                        },
                        "additionalProperties": False,
                    },
                    "columns": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["name", "type"],
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "scale": {"type": "integer"},
                                "nullable": {"type": "boolean"},
                                "description": {"type": "string"},
                                "source_name": {"type": "string"},
                                "source_type": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}

_HIVE_MARKERS = ("SerDe Library", "InputFormat", "Location")


@register("schema_conformance", SCHEMA)
def run(suite: dict, ctx: Context) -> SuiteResult:
    require(ctx, "bigquery")
    sr = SuiteResult(pattern="schema_conformance", suite_id=suite.get("id", "schema_conformance"))
    story = suite.get("story_id")
    dataset = suite["target_dataset"]
    src_db = suite.get("source_database")
    src = ctx.source if src_db else None

    def chk(target, status, message="", **kw):
        return sr.add(CheckResult(pattern="schema_conformance", target=target, status=status,
                                  message=message, story_id=story, **kw))

    # Dataset-level: exact table count, if asserted.
    actual_tables = set(ctx.target.list_tables(dataset))
    if "expect_table_count" in suite:
        want = suite["expect_table_count"]
        chk(dataset, Status.PASS if len(actual_tables) == want else Status.FAIL,
            f"table count {len(actual_tables)} (expected {want})",
            expected=want, actual=len(actual_tables),
            metrics={"tables": sorted(actual_tables)})

    for tbl in suite["tables"]:
        name = tbl["table"]
        fq = f"{dataset}.{name}"
        if name not in actual_tables:
            chk(fq, Status.FAIL, "table missing from target dataset")
            continue
        info = ctx.target.introspect_table(dataset, name)

        # Source introspect ONCE per table (was per-column). Impala can't DESCRIBE Java-SerDe
        # tables (JsonSerDe/RegexSerDe); _introspect_source falls back to Hive, which can.
        src_info = (_introspect_source(ctx, src_db, tbl["source_table"])
                    if src and tbl.get("source_table") else None)

        # Object-type fidelity: the landed object must be the declared TABLE / VIEW /
        # MATERIALIZED_VIEW (a source table silently flipped to a view, or vice versa, fails).
        # EXTERNAL / SNAPSHOT / CLONE are table variants (queryable like a table, not a view),
        # so they normalize to TABLE here — external-ness is caught separately by
        # `no_hive_directives` (is_external).
        if "expect_object_type" in tbl:
            want = tbl["expect_object_type"]
            raw_type = (info.options.get("table_type") or "").upper()
            got = "TABLE" if raw_type in ("EXTERNAL", "SNAPSHOT", "CLONE") else (raw_type or "UNKNOWN")
            chk(f"{fq} (object_type)", Status.PASS if got == want else Status.FAIL,
                f"object type {got}", expected=want, actual=got)

        # Columns: existence + logical type (+ NUMERIC scale) + nullability + description.
        for col in tbl["columns"]:
            cn = col["name"]
            actual = info.column(cn)
            cfq = f"{fq}.{cn}"
            if actual is None:
                chk(cfq, Status.FAIL, f"column '{cn}' missing"
                    + (f" (renamed from '{col['source_name']}'?)" if col.get("source_name") else ""))
                continue
            decl = col["type"].strip()
            # Normalize the DECLARED type the same way the introspected one is, so a
            # natural spelling (INTEGER / BIGINT / FLOAT) equals INT64 / FLOAT64 etc.
            expected_type = normalize_type(decl)
            if col.get("scale") is not None:
                expected_type = LogicalType(expected_type.name, precision=expected_type.precision,
                                            scale=col["scale"], raw=expected_type.raw)
            if expected_type.name in ("ARRAY", "STRUCT") and "<" in decl:
                # Complex type with declared inner shape: compare the full nested
                # signature, so a wrong field type INSIDE an array/struct is caught.
                want_sig, got_sig = type_signature(decl), (actual.type_signature or actual.type.name)
                ok = want_sig == got_sig
                chk(cfq, Status.PASS if ok else Status.FAIL,
                    f"type {got_sig}" if ok else f"nested type mismatch: expected {want_sig}, got {got_sig}",
                    expected=want_sig, actual=got_sig)
            elif expected_type.name == "NUMERIC" and expected_type.scale is None:
                # A NUMERIC column must pin its scale, or a precision change passes silently.
                chk(cfq, Status.FAIL,
                    f"NUMERIC column '{cn}' must declare its expected scale (add `scale: N`) so a "
                    f"precision change is caught; target is {actual.type}",
                    expected="NUMERIC(scale required)", actual=str(actual.type))
            elif actual.type.matches(expected_type):
                chk(cfq, Status.PASS, f"type {actual.type}", expected=str(expected_type), actual=str(actual.type))
            else:
                chk(cfq, Status.FAIL, f"type mismatch: expected {expected_type}, got {actual.type}",
                    expected=str(expected_type), actual=str(actual.type))
            if "nullable" in col and actual.nullable != col["nullable"]:
                chk(cfq, Status.FAIL, f"nullability mismatch: expected nullable={col['nullable']}, "
                    f"got {actual.nullable}", expected=col["nullable"], actual=actual.nullable)
            if "description" in col and (actual.description or "") != col["description"]:
                chk(cfq, Status.FAIL, "description mismatch",
                    expected=col["description"], actual=actual.description)

            # Cross-engine type-map check — opt-in per column: only when the agent
            # declares a mapping (source_type/source_name). Target-only/derived
            # columns (e.g. a new partition key) omit both and are not cross-checked.
            if src_info and (col.get("source_type") or col.get("source_name")):
                _cross_check_source(src_info, src_db, tbl["source_table"], col, expected_type, chk, cfq)

        # Partition column present + correct.
        if "partition_by" in tbl:
            pc = tbl["partition_by"]
            ok = pc in info.partition_columns
            chk(f"{fq} (partition)", Status.PASS if ok else Status.FAIL,
                f"partitioned by {info.partition_columns}", expected=pc, actual=info.partition_columns)

        # Clustering columns exact match (order matters in BQ).
        if "cluster_by" in tbl:
            want = tbl["cluster_by"]
            ok = info.cluster_columns == want
            chk(f"{fq} (cluster)", Status.PASS if ok else Status.FAIL,
                f"clustered by {info.cluster_columns}", expected=want, actual=info.cluster_columns)

        # TABLE_OPTIONS (retention / require-filter / labels) — all optional.
        if "table_options" in tbl:
            _check_table_options(tbl["table_options"], info, fq, chk)

        # Absence of Hive-only directives: target must be native, not external.
        if tbl.get("no_hive_directives", True):
            if info.is_external:
                chk(f"{fq} (native)", Status.FAIL, "target is EXTERNAL — Hive LOCATION leaked into migration")
            else:
                chk(f"{fq} (native)", Status.PASS, "native managed table (no Hive directives)")

    return sr


def _check_table_options(want: dict, info, fq, chk):
    """Assert TABLE_OPTIONS captured from get_table (no INFORMATION_SCHEMA).
    partition_expiration_days backs the retention AC; require_partition_filter and
    labels (subset match) are governance checks."""
    opts = info.options
    if "partition_expiration_days" in want:
        exp = want["partition_expiration_days"]
        act = opts.get("partition_expiration_days")
        ok = (exp is None and act is None) or (
            exp is not None and act is not None and abs(act - exp) < 0.5)
        chk(f"{fq} (retention)", Status.PASS if ok else Status.FAIL,
            f"partition_expiration_days={act}", expected=exp, actual=act)
    if "require_partition_filter" in want:
        act = bool(opts.get("require_partition_filter", False))
        ok = act == want["require_partition_filter"]
        chk(f"{fq} (require_partition_filter)", Status.PASS if ok else Status.FAIL,
            f"require_partition_filter={act}", expected=want["require_partition_filter"], actual=act)
    if "labels" in want:
        act = opts.get("labels", {})
        missing = {k: v for k, v in want["labels"].items() if act.get(k) != v}
        chk(f"{fq} (labels)", Status.PASS if not missing else Status.FAIL,
            f"labels={act}" if not missing else f"label mismatch: {missing}",
            expected=want["labels"], actual=act)


def _introspect_source(ctx, src_db, src_table):
    """Introspect a legacy source table for the schema cross-check. Try the source engine
    (Impala — fast), falling back to Hive (HS2) when Impala can't read it: Impala has no
    Java-SerDe support, so JsonSerDe/RegexSerDe tables raise 'SerDe ... not supported'. Hive's
    DESCRIBE reads its own SerDes — and in production those tables are Hive-only too. If Hive
    also fails (e.g. the table truly doesn't exist) that error propagates -> the suite ERRORs."""
    try:
        return ctx.source.introspect_table(src_db, src_table)
    except Exception:
        return ctx.hive.introspect_table(src_db, src_table)


def _cross_check_source(src_info, src_db, src_table, col, expected_type, chk, cfq):
    """Validate the legacy->target type mapping. For a 1:1 mapping (BIGINT->INT64) the
    legacy logical type should equal the declared target type; for an encoding
    conversion (epoch BIGINT(millis)->TIMESTAMP) the column declares `source_type`
    naming the *legacy* logical type, which is what we assert against."""
    src_name = col.get("source_name", col["name"])
    # Normalize the declared source type so the natural legacy spelling (BIGINT, DECIMAL,
    # FLOAT) matches the introspected source's logical type (INT64, NUMERIC, FLOAT64).
    want = normalize_type(col.get("source_type") or expected_type.name).name
    sc = src_info.column(src_name)
    if sc is None:
        chk(f"{cfq} (source map)", Status.FAIL, f"source column '{src_name}' not found in {src_db}.{src_table}")
        return
    if sc.type.name == want:
        chk(f"{cfq} (source map)", Status.PASS,
            f"{src_name}:{sc.type} -> {col['name']}:{expected_type}")
    else:
        chk(f"{cfq} (source map)", Status.FAIL,
            f"type-map break: legacy {src_name} is {sc.type.name}, expected source_type {want}",
            expected=want, actual=sc.type.name)
