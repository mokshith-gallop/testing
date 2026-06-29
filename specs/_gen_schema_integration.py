#!/usr/bin/env python3
"""Generate specs/schema_integration.mvs.yaml — FK/PK consistency + queryability.

AC5: Cross-dataset FK/PK type consistency via schema_conformance
AC6: Queryability smoke tests via query_performance (mode: measure)
"""
import re, sys, os

DDL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql", "ddl")
BQ_FILES = [
    "02-staging-sqoop-mirrors.sql",
    "03-staging-delta-feeds.sql",
    "04-staging-file-feeds.sql",
    "05-ods-cleanse.sql",
    "06-ods-delta-scd2.sql",
    "07-ods-acid.sql",
    "08-dm-tables.sql",
]

# ── FK/PK join paths: (column_name, type, [table1, table2, ...]) ──
# Each entry documents a cross-dataset or cross-table join path.
FK_JOIN_PATHS = [
    # Natural keys crossing staging → ods → dm
    ("agent_id", "INT64", [
        "stg_hr_agent", "stg_hr_employment_event", "stg_hr_agent_skill",
        "stg_wfm_schedule", "stg_wfm_adherence_event", "stg_wfm_timeoff_request",
        "stg_tel_call", "stg_tel_call_segment", "stg_tel_agent_state_event",
        "stg_fin_timesheet_delta", "stg_fin_payroll_adj_delta",
        "stg_tkt_worklog_delta", "stg_hr_attrition_event_delta",
        "stg_file_dialer_result",
        "ods_schedule", "ods_adherence_event", "ods_call", "ods_interaction",
        "ods_dialer_attempt", "ods_timesheet", "ods_payroll_adjustment",
        "ods_ticket_worklog", "ods_attrition_event",
        "ods_agent_scd2", "ods_agent_skill_scd2", "ods_agent_assignment_scd2",
        "ods_agent_acid",
        "dim_agent",
    ]),
    ("client_id", "INT64", [
        "stg_crm_client", "stg_crm_client_contact", "stg_crm_program",
        "stg_crm_contract", "stg_fin_invoice",
        "ods_program", "ods_contract", "ods_client_acid", "ods_invoice_acid",
        "dim_client", "dim_program",
    ]),
    ("program_id", "INT64", [
        "stg_crm_program", "stg_crm_contract", "stg_crm_sla_target",
        "stg_tel_call", "stg_tel_queue", "stg_tkt_ticket",
        "stg_fin_invoice", "stg_fin_rate_card",
        "stg_fin_timesheet_delta", "stg_crm_sla_credit_delta",
        "ods_program", "ods_contract", "ods_queue", "ods_call",
        "ods_interaction", "ods_timesheet", "ods_sla_credit", "ods_rate_card",
        "ods_agent_assignment_scd2", "ods_ticket_acid", "ods_invoice_acid",
        "dim_program", "dim_queue",
    ]),
    ("queue_id", "INT64", [
        "stg_crm_sla_target", "stg_wfm_forecast",
        "stg_tel_call", "stg_tel_queue",
        "stg_tel_callback_request_delta",
        "ods_queue", "ods_call", "ods_interaction",
        "ods_callback_request", "ods_agent_assignment_scd2",
        "dim_queue",
    ]),
    ("ticket_id", "INT64", [
        "stg_tkt_ticket", "stg_tkt_ticket_event",
        "stg_tkt_worklog_delta",
        "ods_ticket_worklog", "ods_ticket_acid",
        "fact_ticket",
    ]),
    ("invoice_id", "INT64", [
        "stg_fin_invoice", "stg_fin_invoice_line",
        "ods_invoice_acid",
        "fact_billing_line",
    ]),
    ("shift_id", "INT64", [
        "stg_wfm_shift", "stg_wfm_schedule",
        "ods_schedule",
        "dim_shift",  # layer-skip path
    ]),
    ("contract_id", "INT64", [
        "stg_crm_contract", "stg_crm_contract_line",
        "ods_contract", "ods_contract_line",
    ]),
    # Surrogate keys within DM
    ("agent_sk", "INT64", [
        "dim_agent",
        "fact_interaction", "fact_agent_activity", "fact_csat_survey",
        "fact_qa_evaluation", "fact_adherence_daily",
        "agg_agent_daily", "agg_agent_weekly",
    ]),
    ("client_sk", "INT64", [
        "dim_client",
        "fact_interaction", "fact_csat_survey", "fact_billing_line",
        "agg_program_monthly", "agg_csat_rollup_monthly", "agg_billing_monthly",
    ]),
    ("program_sk", "INT64", [
        "dim_program",
        "fact_interaction", "fact_csat_survey", "fact_qa_evaluation",
        "fact_billing_line", "fact_ticket",
        "agg_program_monthly", "agg_csat_rollup_monthly", "agg_billing_monthly",
    ]),
    ("queue_sk", "INT64", [
        "dim_queue",
        "fact_interaction", "fact_queue_interval",
        "agg_queue_hourly",
    ]),
    ("disposition_sk", "INT64", [
        "dim_disposition",
        # No fact/agg table references disposition_sk; facts use disposition_code
    ]),
    # Cross-dataset STRING keys
    ("disposition_code", "STRING", [
        "stg_tel_call", "stg_tel_disposition_code",
        "ods_call",
        "dim_disposition",
    ]),
    ("interaction_id", "STRING", [
        "ods_interaction",
        "fact_interaction", "fact_csat_survey", "fact_qa_evaluation",
    ]),
]

# Also check assigned_agent_sk which is a renamed surrogate FK
EXTRA_SK_COLUMNS = [
    # (table, column_name, expected_type) — these are surrogate FK columns
    # with different names that still must be INT64
    ("fact_ticket", "assigned_agent_sk", "INT64"),
]


def get_all_table_names():
    """Parse all table names from the DDL files."""
    tables = []
    for fname in BQ_FILES:
        with open(os.path.join(DDL_DIR, fname)) as f:
            sql = f.read()
        for m in re.finditer(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(', sql, re.I):
            tables.append(m.group(1))
    return tables


def emit_fk_suite():
    """Emit the FK/PK consistency schema_conformance suite."""
    lines = []
    lines.append("  # ── AC5: Cross-dataset FK/PK type consistency ──")
    lines.append("  # Asserts every FK column joining across datasets has identical BigQuery type.")
    lines.append("  - pattern: schema_conformance")
    lines.append("    id: fk-pk-consistency")
    lines.append('    target_dataset: "${BUILD_DATASET}"')
    lines.append("    tables:")

    # Build table → [(col_name, col_type)] map, deduped
    table_fk_cols = {}  # table -> list of (col, type)
    for col_name, col_type, table_list in FK_JOIN_PATHS:
        for tbl in table_list:
            if tbl not in table_fk_cols:
                table_fk_cols[tbl] = []
            if (col_name, col_type) not in table_fk_cols[tbl]:
                table_fk_cols[tbl].append((col_name, col_type))

    # Add extra SK columns
    for tbl, col_name, col_type in EXTRA_SK_COLUMNS:
        if tbl not in table_fk_cols:
            table_fk_cols[tbl] = []
        if (col_name, col_type) not in table_fk_cols[tbl]:
            table_fk_cols[tbl].append((col_name, col_type))

    # Sort tables for deterministic output
    for tbl in sorted(table_fk_cols.keys()):
        cols = table_fk_cols[tbl]
        lines.append(f"        - table: {tbl}")
        lines.append(f"          columns:")
        for col_name, col_type in sorted(cols):
            lines.append(f"            - {{name: {col_name}, type: {col_type}}}")

    return '\n'.join(lines)


def emit_queryability_suite(all_tables):
    """Emit the queryability smoke test query_performance suite."""
    lines = []
    lines.append("  # ── AC6: Queryability smoke — SELECT * succeeds for every table ──")
    lines.append("  - pattern: query_performance")
    lines.append("    id: queryability-smoke")
    lines.append('    target_dataset: "${BUILD_DATASET}"')
    lines.append("    queries:")

    # 100 per-table SELECT * LIMIT 0 queries
    for tbl in all_tables:
        lines.append(f"      - id: select-{tbl}")
        lines.append(f"        mode: measure")
        lines.append(f'        sql: "SELECT * FROM ${{BUILD_DATASET}}.{tbl} LIMIT 0"')

    # 3 cross-dataset representative joins
    lines.append("")
    lines.append("      # Cross-dataset representative queries")
    lines.append("      - id: join-staging-to-ods")
    lines.append("        mode: measure")
    lines.append('        sql: "SELECT s.invoice_id, a.invoice_no FROM ${BUILD_DATASET}.stg_fin_invoice s JOIN ${BUILD_DATASET}.ods_invoice_acid a ON a.invoice_id = s.invoice_id LIMIT 0"')

    lines.append("      - id: join-ods-to-dm")
    lines.append("        mode: measure")
    lines.append('        sql: "SELECT i.interaction_id, f.handle_seconds FROM ${BUILD_DATASET}.ods_interaction i JOIN ${BUILD_DATASET}.fact_interaction f ON f.interaction_id = i.interaction_id LIMIT 0"')

    lines.append("      - id: join-dm-fact-to-dim")
    lines.append("        mode: measure")
    lines.append('        sql: "SELECT f.interaction_id, a.full_name, p.program_name, q.queue_name FROM ${BUILD_DATASET}.fact_interaction f JOIN ${BUILD_DATASET}.dim_agent a ON a.agent_sk = f.agent_sk JOIN ${BUILD_DATASET}.dim_program p ON p.program_sk = f.program_sk JOIN ${BUILD_DATASET}.dim_queue q ON q.queue_sk = f.queue_sk LIMIT 0"')

    return '\n'.join(lines)


def main():
    all_tables = get_all_table_names()
    print(f"Total tables: {len(all_tables)}", file=sys.stderr)
    assert len(all_tables) == 100, f"Expected 100, got {len(all_tables)}"

    # Count FK join path coverage
    all_fk_tables = set()
    for _, _, tlist in FK_JOIN_PATHS:
        all_fk_tables.update(tlist)
    for tbl, _, _ in EXTRA_SK_COLUMNS:
        all_fk_tables.add(tbl)
    print(f"FK/PK tables covered: {len(all_fk_tables)}", file=sys.stderr)
    total_fk_checks = sum(len(tlist) for _, _, tlist in FK_JOIN_PATHS) + len(EXTRA_SK_COLUMNS)
    print(f"FK/PK column checks: {total_fk_checks}", file=sys.stderr)

    lines = []
    lines.append("# schema_integration.mvs.yaml — Cross-dataset FK/PK + Queryability")
    lines.append("# AC5: FK/PK type consistency across all 3 datasets")
    lines.append("# AC6: Queryability smoke tests (SELECT * + cross-dataset joins)")
    lines.append("# AC7/AC8: Integrity guards + live-only execution")
    lines.append("")
    lines.append("name: schema_integration")
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
    lines.append("suites:")
    lines.append(emit_fk_suite())
    lines.append("")
    lines.append(emit_queryability_suite(all_tables))
    lines.append("")

    output = '\n'.join(lines)
    outpath = os.path.join(os.path.dirname(__file__), "schema_integration.mvs.yaml")
    with open(outpath, 'w') as f:
        f.write(output)
    print(f"Wrote {outpath} ({len(output)} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
