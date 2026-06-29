-- ----------------------------------------------------------------------------
-- 07-ods-acid: BigQuery DDL for 4 ACID-to-standard conversion tables
-- Migrated from Hive/Impala (CDH 6.3.4) — type mapping per locked rules.
--
-- Type mapping: BIGINT→INT64, STRING→STRING, TIMESTAMP→TIMESTAMP,
--   DECIMAL(p,s)→NUMERIC(p,s).
-- Hive ACID directives dropped:
--   - CLUSTERED BY ... INTO N BUCKETS (ods_client_acid: 4, ods_agent_acid: 8,
--     ods_ticket_acid: 8, ods_invoice_acid: 4)
--   - STORED AS ORC
--   - TBLPROPERTIES ('transactional'='true', 'orc.compress'='SNAPPY')
-- No partition, no clustering per locked spec (ACID tables converted to
-- standard BigQuery managed tables).
-- No source COMMENTs in ODS ACID layer.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS ods_client_acid (
  client_id                   INT64,
  client_code                 STRING,
  client_name                 STRING,
  industry                    STRING,
  hq_country                  STRING,
  status                      STRING,
  created_ts                  TIMESTAMP,
  updated_ts                  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ods_agent_acid (
  agent_id                    INT64,
  employee_no                 STRING,
  full_name                   STRING,
  email                       STRING,
  org_unit_id                 INT64,
  job_grade                   STRING,
  employment_type             STRING,
  hire_ts                     TIMESTAMP,
  term_ts                     TIMESTAMP,
  status                      STRING
);

CREATE TABLE IF NOT EXISTS ods_ticket_acid (
  ticket_id                   INT64,
  ticket_no                   STRING,
  program_id                  INT64,
  category_id                 INT64,
  assigned_agent_id           INT64,
  priority                    STRING,
  status                      STRING,
  created_ts                  TIMESTAMP,
  updated_ts                  TIMESTAMP,
  resolved_ts                 TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ods_invoice_acid (
  invoice_id                  INT64,
  invoice_no                  STRING,
  client_id                   INT64,
  program_id                  INT64,
  period_month                STRING,
  issued_ts                   TIMESTAMP,
  due_ts                      TIMESTAMP,
  currency                    STRING,
  total_amount                NUMERIC(14,2),
  status                      STRING
);
