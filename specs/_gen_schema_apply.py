#!/usr/bin/env python3
"""Generate specs/schema_apply.mvs.yaml from the BigQuery DDL files.

This is a one-shot generator; the output file is the committed artifact.
"""
import re, sys, os

DDL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql", "ddl")
FILES = [
    "02-staging-sqoop-mirrors.sql",
    "03-staging-delta-feeds.sql",
    "04-staging-file-feeds.sql",
    "05-ods-cleanse.sql",
    "06-ods-delta-scd2.sql",
    "07-ods-acid.sql",
    "08-dm-tables.sql",
]

STAGING_PREFIXES = ("stg_",)
ODS_PREFIXES = ("ods_",)
DM_PREFIXES = ("dim_", "fact_", "agg_")


def find_matching_paren(s, start):
    """Find the matching ')' for '(' at position start, respecting nesting and angle brackets."""
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
            # skip string literal
            i += 1
            while i < len(s) and s[i] != "'":
                i += 1
        i += 1
    return -1


def parse_tables(sql_text):
    """Parse CREATE TABLE stmts → list of table dicts."""
    tables = []
    # Find all CREATE TABLE positions
    for m in re.finditer(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(', sql_text, re.I | re.S):
        name = m.group(1)
        paren_start = m.end() - 1  # position of '('
        paren_end = find_matching_paren(sql_text, paren_start)
        if paren_end == -1:
            continue

        cols_str = sql_text[paren_start + 1:paren_end]

        # Find the suffix after the closing paren up to the next ';'
        rest = sql_text[paren_end + 1:]
        semi = rest.find(';')
        suffix = rest[:semi].strip() if semi >= 0 else rest.strip()

        columns = parse_columns(cols_str)

        # Parse PARTITION BY
        partition_by = None
        pm = re.search(r'PARTITION\s+BY\s+RANGE_BUCKET\s*\(\s*(\w+)', suffix, re.I)
        if pm:
            partition_by = pm.group(1)
        else:
            pm = re.search(r'PARTITION\s+BY\s+(\w+)', suffix, re.I)
            if pm:
                partition_by = pm.group(1)

        # Parse CLUSTER BY
        cluster_by = None
        cm = re.search(r'CLUSTER\s+BY\s+([\w,\s]+?)(?:\s*$|\s*;)', suffix, re.I)
        if cm:
            cluster_by = [c.strip() for c in cm.group(1).split(',') if c.strip()]

        tables.append({
            "name": name,
            "columns": columns,
            "partition_by": partition_by,
            "cluster_by": cluster_by,
        })
    return tables


def split_top_level(s):
    """Split string on commas not inside <> brackets or ()."""
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


def parse_columns(cols_str):
    """Parse column definitions from within parentheses."""
    columns = []
    parts = split_top_level(cols_str)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        cm = re.match(r'(\w+)\s+(.*)', part, re.S)
        if not cm:
            continue
        col_name = cm.group(1)
        rest = cm.group(2).strip()

        # Extract OPTIONS(description='...')
        description = None
        dm = re.search(r"OPTIONS\s*\(\s*description\s*=\s*'([^']*)'\s*\)", rest, re.I)
        if dm:
            description = dm.group(1)
            rest = rest[:dm.start()].strip() + rest[dm.end():].strip()

        col_type = rest.strip()

        columns.append({
            "name": col_name,
            "type": col_type,
            "description": description,
        })
    return columns


def needs_scale(col_type):
    """Return scale if NUMERIC(p,s)."""
    m = re.match(r'NUMERIC\(\d+,(\d+)\)', col_type, re.I)
    if m:
        return int(m.group(1))
    return None


def emit_table_yaml(t, indent=8):
    """Emit YAML for one table entry."""
    pad = ' ' * indent
    lines = []
    lines.append(f'{pad}- table: {t["name"]}')
    lines.append(f'{pad}  expect_object_type: TABLE')
    if t["partition_by"]:
        lines.append(f'{pad}  partition_by: {t["partition_by"]}')
    if t["cluster_by"]:
        cb = ', '.join(t["cluster_by"])
        lines.append(f'{pad}  cluster_by: [{cb}]')
    lines.append(f'{pad}  columns:')
    for col in t["columns"]:
        parts = [f'name: {col["name"]}']
        ty = col["type"]
        if '<' in ty or '>' in ty or '(' in ty:
            parts.append(f'type: "{ty}"')
        else:
            parts.append(f'type: {ty}')
        sc = needs_scale(ty)
        if sc is not None:
            parts.append(f'scale: {sc}')
        if col.get("description"):
            desc = col["description"]
            parts.append(f"description: '{desc}'")
        line = ', '.join(parts)
        lines.append(f'{pad}    - {{{line}}}')
    return '\n'.join(lines)


def main():
    all_tables = []
    for fname in FILES:
        path = os.path.join(DDL_DIR, fname)
        with open(path) as f:
            sql = f.read()
        tables = parse_tables(sql)
        print(f"  {fname}: {len(tables)} tables", file=sys.stderr)
        all_tables.extend(tables)

    staging = [t for t in all_tables if t["name"].startswith(STAGING_PREFIXES)]
    ods = [t for t in all_tables if t["name"].startswith(ODS_PREFIXES)]
    dm = [t for t in all_tables if t["name"].startswith(DM_PREFIXES)]

    print(f"Parsed: {len(staging)} staging, {len(ods)} ods, {len(dm)} dm = {len(all_tables)} total",
          file=sys.stderr)
    assert len(all_tables) == 100, f"Expected 100 tables, got {len(all_tables)}"
    assert len(staging) == 45, f"Expected 45 staging, got {len(staging)}"
    assert len(ods) == 30, f"Expected 30 ods, got {len(ods)}"
    assert len(dm) == 25, f"Expected 25 dm, got {len(dm)}"

    # Count total columns
    total_cols = sum(len(t["columns"]) for t in all_tables)
    print(f"Total columns: {total_cols}", file=sys.stderr)

    # Count descriptions
    total_desc = sum(1 for t in all_tables for c in t["columns"] if c.get("description"))
    print(f"Total descriptions: {total_desc}", file=sys.stderr)

    lines = []
    lines.append("# schema_apply.mvs.yaml — TARGET-ONLY schema conformance")
    lines.append("# Proves all 100 BigQuery tables build and match the declared shape.")
    lines.append("# AC1 (DDL creates), AC3 (object type), AC4 (partition/cluster), AC7/AC8 (integrity)")
    lines.append("#")
    lines.append("# NO source_setup / source_database — this spec only checks the target.")
    lines.append("# Source cross-check is in schema_typemap.mvs.yaml.")
    lines.append("")
    lines.append("name: schema_apply")
    lines.append("")
    lines.append("connections:")
    lines.append("  source: { engine: impala }")
    lines.append("  target: { engine: bigquery }")
    lines.append("")
    lines.append("migration:")
    lines.append("  steps:")
    for fname in FILES:
        lines.append(f"    - {{ kind: ddl, sql: sql/ddl/{fname} }}")
    lines.append("")
    lines.append("suites:")

    # One suite with expect_table_count: 100 (all tables share ${BUILD_DATASET})
    lines.append("  # ── Staging (45 tables) ──")
    lines.append("  - pattern: schema_conformance")
    lines.append("    id: staging-schema")
    lines.append('    target_dataset: "${BUILD_DATASET}"')
    lines.append("    expect_table_count: 100")
    lines.append("    tables:")
    for t in staging:
        lines.append(emit_table_yaml(t))
    lines.append("")

    lines.append("  # ── ODS (30 tables) ──")
    lines.append("  - pattern: schema_conformance")
    lines.append("    id: ods-schema")
    lines.append('    target_dataset: "${BUILD_DATASET}"')
    lines.append("    tables:")
    for t in ods:
        lines.append(emit_table_yaml(t))
    lines.append("")

    lines.append("  # ── DM (25 tables) ──")
    lines.append("  - pattern: schema_conformance")
    lines.append("    id: dm-schema")
    lines.append('    target_dataset: "${BUILD_DATASET}"')
    lines.append("    tables:")
    for t in dm:
        lines.append(emit_table_yaml(t))
    lines.append("")

    output = '\n'.join(lines)
    outpath = os.path.join(os.path.dirname(__file__), "schema_apply.mvs.yaml")
    with open(outpath, 'w') as f:
        f.write(output)
    print(f"Wrote {outpath} ({len(output)} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
