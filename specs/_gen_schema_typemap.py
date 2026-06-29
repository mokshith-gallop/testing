#!/usr/bin/env python3
"""Generate specs/schema_typemap.mvs.yaml — source cross-check spec.

Reads BigQuery DDL + source HQL to build schema_conformance suites with
source_database / source_table / source_type for every carried column.
"""
import re, sys, os

DDL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql", "ddl")
HQL_DIR = "/workspace/source/hive/ddl"

BQ_FILES = [
    "02-staging-sqoop-mirrors.sql",
    "03-staging-delta-feeds.sql",
    "04-staging-file-feeds.sql",
    "05-ods-cleanse.sql",
    "06-ods-delta-scd2.sql",
    "07-ods-acid.sql",
    "08-dm-tables.sql",
]

HQL_FILES = [
    "01-create-databases.hql",
    "02-staging-sqoop-mirrors.hql",
    "03-staging-delta-feeds.hql",
    "04-staging-file-feeds.hql",
    "05-ods-cleanse.hql",
    "06-ods-delta-scd2.hql",
    "07-ods-acid.hql",
    "08-dm-tables.hql",
]

# Map BQ table → (source_database, source_table_name)
# Staging tables: database=staging, same name
# ODS tables: database=ods, same name
# DM tables: database=dm, same name


def find_matching_paren(s, start):
    depth = 0
    i = start
    while i < len(s):
        ch = s[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i
        elif ch == "'":
            i += 1
            while i < len(s) and s[i] != "'":
                i += 1
        i += 1
    return -1


def split_top_level(s):
    parts = []
    depth_angle = 0
    depth_paren = 0
    current = []
    in_string = False
    for ch in s:
        if in_string:
            current.append(ch)
            if ch == "'":
                in_string = False
            continue
        if ch == "'":
            in_string = True
            current.append(ch)
        elif ch == '<':
            depth_angle += 1
            current.append(ch)
        elif ch == '>':
            depth_angle -= 1
            current.append(ch)
        elif ch == '(':
            depth_paren += 1
            current.append(ch)
        elif ch == ')':
            depth_paren -= 1
            current.append(ch)
        elif ch == ',' and depth_angle == 0 and depth_paren == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current))
    return parts


def parse_bq_tables(sql_text):
    tables = []
    for m in re.finditer(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(', sql_text, re.I | re.S):
        name = m.group(1)
        paren_start = m.end() - 1
        paren_end = find_matching_paren(sql_text, paren_start)
        if paren_end == -1:
            continue
        cols_str = sql_text[paren_start + 1:paren_end]
        columns = []
        for part in split_top_level(cols_str):
            part = part.strip()
            if not part:
                continue
            cm = re.match(r'(\w+)\s+(.*)', part, re.S)
            if not cm:
                continue
            col_name = cm.group(1)
            rest = cm.group(2).strip()
            desc = None
            dm = re.search(r"OPTIONS\s*\(\s*description\s*=\s*'([^']*)'\s*\)", rest, re.I)
            if dm:
                desc = dm.group(1)
                rest = rest[:dm.start()].strip() + rest[dm.end():].strip()
            col_type = rest.strip()
            columns.append({"name": col_name, "type": col_type, "description": desc})
        tables.append({"name": name, "columns": columns})
    return tables


def parse_hql_tables(sql_text):
    """Parse Hive DDL — extract table name, database, columns, and partition columns."""
    tables = {}
    # Find CREATE [EXTERNAL] TABLE statements
    for m in re.finditer(
        r'CREATE\s+(?:EXTERNAL\s+)?TABLE\s+IF\s+NOT\s+EXISTS\s+([\w.]+)\s*\(',
        sql_text, re.I | re.S
    ):
        full_name = m.group(1)
        parts = full_name.split('.')
        if len(parts) == 2:
            db, tname = parts
        else:
            db, tname = 'default', parts[0]

        paren_start = m.end() - 1
        paren_end = find_matching_paren(sql_text, paren_start)
        if paren_end == -1:
            continue

        cols_str = sql_text[paren_start + 1:paren_end]
        columns = []
        for part in split_top_level(cols_str):
            part = part.strip()
            if not part:
                continue
            cm = re.match(r'(\w+)\s+(.*)', part, re.S)
            if not cm:
                continue
            col_name = cm.group(1)
            rest = cm.group(2).strip()
            # Remove COMMENT '...'
            rest = re.sub(r"\s*COMMENT\s+'[^']*'", '', rest, flags=re.I).strip()
            col_type = rest.strip()
            columns.append({"name": col_name, "type": col_type})

        # Parse PARTITIONED BY
        rest_text = sql_text[paren_end + 1:]
        semi = rest_text.find(';')
        suffix = rest_text[:semi] if semi >= 0 else rest_text

        pm = re.search(r'PARTITIONED\s+BY\s*\(([^)]+)\)', suffix, re.I)
        if pm:
            for ppart in pm.group(1).split(','):
                ppart = ppart.strip()
                pcm = re.match(r'(\w+)\s+(\w+)', ppart)
                if pcm:
                    columns.append({"name": pcm.group(1), "type": pcm.group(2)})

        tables[tname] = {"db": db, "name": tname, "columns": {c["name"]: c["type"] for c in columns}}

    return tables


# Type mapping from Hive → BQ for source_type cross-check
HIVE_TO_SOURCE_TYPE = {
    'BIGINT': 'BIGINT',
    'INT': 'INT',
    'SMALLINT': 'SMALLINT',
    'STRING': 'STRING',
    'BOOLEAN': 'BOOLEAN',
    'DOUBLE': 'DOUBLE',
    'TIMESTAMP': 'TIMESTAMP',
    'DATE': 'DATE',
}


def hive_source_type(hive_type):
    """Return the source_type to declare for schema_conformance cross-check."""
    if not hive_type:
        return None
    ht = hive_type.upper().strip()
    if ht in HIVE_TO_SOURCE_TYPE:
        return HIVE_TO_SOURCE_TYPE[ht]
    if ht.startswith('DECIMAL'):
        return 'DECIMAL'
    if ht.startswith('ARRAY') or ht.startswith('MAP'):
        return None  # Complex types — skip source cross-check
    return ht


def needs_scale(col_type):
    m = re.match(r'NUMERIC\(\d+,(\d+)\)', col_type, re.I)
    if m:
        return int(m.group(1))
    return None


def emit_table_yaml(bq_table, hql_info, indent=8):
    """Emit YAML for one table with source cross-check fields."""
    pad = ' ' * indent
    lines = []
    name = bq_table["name"]
    src_table = name  # same name in source

    lines.append(f'{pad}- table: {name}')
    lines.append(f'{pad}  source_table: {src_table}')
    lines.append(f'{pad}  columns:')

    for col in bq_table["columns"]:
        cname = col["name"]
        ctype = col["type"]

        parts = [f'name: {cname}']
        if '<' in ctype or '>' in ctype or '(' in ctype:
            parts.append(f'type: "{ctype}"')
        else:
            parts.append(f'type: {ctype}')

        sc = needs_scale(ctype)
        if sc is not None:
            parts.append(f'scale: {sc}')

        if col.get("description"):
            parts.append(f"description: '{col['description']}'")

        # Source cross-check: add source_name and source_type if the column exists in source
        if hql_info:
            hive_type = hql_info.get("columns", {}).get(cname)
            if hive_type:
                st = hive_source_type(hive_type)
                if st:
                    parts.append(f'source_name: {cname}')
                    parts.append(f'source_type: {st}')

        line = ', '.join(parts)
        lines.append(f'{pad}    - {{{line}}}')
    return '\n'.join(lines)


def main():
    # Parse all BQ DDL
    all_bq = []
    for fname in BQ_FILES:
        with open(os.path.join(DDL_DIR, fname)) as f:
            all_bq.extend(parse_bq_tables(f.read()))
    print(f"BQ tables parsed: {len(all_bq)}", file=sys.stderr)

    # Parse all HQL DDL
    all_hql = {}
    for fname in HQL_FILES:
        path = os.path.join(HQL_DIR, fname)
        with open(path) as f:
            all_hql.update(parse_hql_tables(f.read()))
    print(f"HQL tables parsed: {len(all_hql)}", file=sys.stderr)

    # Categorize BQ tables by layer
    staging = [t for t in all_bq if t["name"].startswith("stg_")]
    ods = [t for t in all_bq if t["name"].startswith("ods_")]
    dm = [t for t in all_bq if t["name"].startswith(("dim_", "fact_", "agg_"))]

    print(f"Staging: {len(staging)}, ODS: {len(ods)}, DM: {len(dm)}", file=sys.stderr)

    # Count source-mapped columns
    total_src_mapped = 0
    for t in all_bq:
        hql = all_hql.get(t["name"])
        if hql:
            for c in t["columns"]:
                hive_type = hql.get("columns", {}).get(c["name"])
                if hive_type and hive_source_type(hive_type):
                    total_src_mapped += 1
    print(f"Source-mapped columns: {total_src_mapped}", file=sys.stderr)

    lines = []
    lines.append("# schema_typemap.mvs.yaml — SOURCE CROSS-CHECK")
    lines.append("# Proves legacy→target type mapping against live Hive source.")
    lines.append("# AC2 (per-column fidelity: presence, name, ordinal, mapped type,")
    lines.append("#       precision/scale, nested types, nullability, description)")
    lines.append("#")
    lines.append("# source_setup applies the legacy HQL to a sandbox Hive, then")
    lines.append("# schema_conformance cross-checks each column's source_type.")
    lines.append("")
    lines.append("name: schema_typemap")
    lines.append("")
    lines.append("connections:")
    lines.append("  source: { engine: impala }")
    lines.append("  target: { engine: bigquery }")
    lines.append("")
    lines.append("migration:")
    lines.append("  steps:")
    for fname in BQ_FILES:
        lines.append(f"    - {{ kind: ddl, sql: sql/ddl/{fname} }}")
    lines.append("")
    lines.append("source_setup:")
    lines.append("  location_base: ${SOURCE_WAREHOUSE:-/tmp/dmt_src}")
    lines.append("  ddl:")
    for fname in HQL_FILES:
        lines.append(f"    - /workspace/source/hive/ddl/{fname}")
    lines.append("")
    lines.append("suites:")

    # Suite per layer
    for layer_name, layer_tables, src_db in [
        ("staging", staging, "staging"),
        ("ods", ods, "ods"),
        ("dm", dm, "dm"),
    ]:
        lines.append(f"  # ── {layer_name} ({len(layer_tables)} tables) ──")
        lines.append(f"  - pattern: schema_conformance")
        lines.append(f"    id: typemap-{layer_name}")
        lines.append(f'    target_dataset: "${{BUILD_DATASET}}"')
        lines.append(f"    source_database: {src_db}")
        lines.append(f"    tables:")
        for t in layer_tables:
            hql = all_hql.get(t["name"])
            lines.append(emit_table_yaml(t, hql))
        lines.append("")

    output = '\n'.join(lines)
    outpath = os.path.join(os.path.dirname(__file__), "schema_typemap.mvs.yaml")
    with open(outpath, 'w') as f:
        f.write(output)
    print(f"Wrote {outpath} ({len(output)} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
