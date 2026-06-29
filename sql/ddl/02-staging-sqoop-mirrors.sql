-- ----------------------------------------------------------------------------
-- 02-staging-sqoop-mirrors: BigQuery DDL for 27 Sqoop-landed RDBMS mirrors
-- Migrated from Hive/Impala (CDH 6.3.4) — type mapping per locked rules.
--
-- Type mapping: BIGINT→INT64, INT→INT64, STRING→STRING, BOOLEAN→BOOL,
--   DECIMAL(p,s)→NUMERIC(p,s), DOUBLE→FLOAT64.
-- Partition: load_date STRING→DATE (cast from STRING).
-- Clustering: PK column(s) per locked spec.
-- Hive directives dropped: EXTERNAL, STORED AS, LOCATION, TBLPROPERTIES,
--   CLUSTERED BY (stg_tel_call: 16 buckets dropped).
-- All COMMENTs carried as OPTIONS(description=...).
-- ----------------------------------------------------------------------------

-- ============================================================================
-- CRM on Oracle (crmdb01) — 6 tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS stg_crm_client (
  client_id                   INT64,
  client_code                 STRING,
  client_name                 STRING,
  industry                    STRING,
  hq_country                  STRING,
  status                      STRING,
  created_ts                  INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  updated_ts                  INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY client_id;

CREATE TABLE IF NOT EXISTS stg_crm_client_contact (
  contact_id                  INT64,
  client_id                   INT64,
  full_name                   STRING,
  email                       STRING,
  phone                       STRING,
  role                        STRING,
  is_primary                  BOOL,
  created_ts                  INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY contact_id;

CREATE TABLE IF NOT EXISTS stg_crm_program (
  program_id                  INT64,
  client_id                   INT64,
  program_code                STRING,
  program_name                STRING,
  line_of_business            STRING,
  channel_mix                 STRING,
  site_code                   STRING,
  status                      STRING,
  go_live_ts                  INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  updated_ts                  INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY program_id;

CREATE TABLE IF NOT EXISTS stg_crm_contract (
  contract_id                 INT64,
  client_id                   INT64,
  program_id                  INT64,
  contract_no                 STRING,
  start_dt                    STRING OPTIONS(description='Oracle string YYYYMMDDHH24MISS (legacy)'),
  end_dt                      STRING OPTIONS(description='Oracle string YYYYMMDDHH24MISS (legacy)'),
  billing_model               STRING,
  currency                    STRING,
  signed_dt                   STRING OPTIONS(description='Oracle string YYYYMMDDHH24MISS (legacy)'),
  status                      STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY contract_id;

CREATE TABLE IF NOT EXISTS stg_crm_contract_line (
  contract_line_id            INT64,
  contract_id                 INT64,
  line_no                     INT64,
  service_code                STRING,
  uom                         STRING,
  unit_rate                   NUMERIC(12,4),
  min_commit                  NUMERIC(12,2),
  effective_dt                STRING OPTIONS(description='Oracle string YYYYMMDDHH24MISS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY contract_line_id;

CREATE TABLE IF NOT EXISTS stg_crm_sla_target (
  sla_target_id               INT64,
  program_id                  INT64,
  queue_id                    INT64,
  metric_code                 STRING,
  target_value                NUMERIC(10,4),
  penalty_pct                 NUMERIC(5,2),
  effective_ts                INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY sla_target_id;

-- ============================================================================
-- HR/HCM on SQL Server (hrms01) — 5 tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS stg_hr_agent (
  agent_id                    INT64,
  employee_no                 STRING,
  first_name                  STRING,
  last_name                   STRING,
  email                       STRING,
  org_unit_id                 INT64,
  job_grade                   STRING,
  employment_type             STRING,
  hire_ts                     INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  term_ts                     INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  status                      STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY agent_id;

CREATE TABLE IF NOT EXISTS stg_hr_org_unit (
  org_unit_id                 INT64,
  parent_unit_id              INT64,
  unit_code                   STRING,
  unit_name                   STRING,
  unit_type                   STRING,
  site_code                   STRING,
  cost_center                 STRING,
  created_ts                  INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY org_unit_id;

CREATE TABLE IF NOT EXISTS stg_hr_employment_event (
  event_id                    INT64,
  agent_id                    INT64,
  event_type                  STRING,
  event_ts                    INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  from_org_unit_id            INT64,
  to_org_unit_id              INT64,
  reason_code                 STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY event_id;

CREATE TABLE IF NOT EXISTS stg_hr_skill (
  skill_id                    INT64,
  skill_code                  STRING,
  skill_name                  STRING,
  skill_family                STRING,
  created_ts                  INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY skill_id;

CREATE TABLE IF NOT EXISTS stg_hr_agent_skill (
  agent_skill_id              INT64,
  agent_id                    INT64,
  skill_id                    INT64,
  proficiency                 INT64,
  certified                   BOOL,
  effective_ts                INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  expiry_ts                   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY agent_skill_id;

-- ============================================================================
-- WFM on MySQL (wfm01) — 5 tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS stg_wfm_shift (
  shift_id                    INT64,
  shift_code                  STRING,
  shift_name                  STRING,
  start_hhmm                  STRING,
  end_hhmm                    STRING,
  overnight_flag              BOOL,
  site_code                   STRING,
  created_epoch               INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY shift_id;

-- Multi-column partition collapsed: (load_date, site_code) → load_date only.
-- site_code demoted from partition to regular column + clustering column.
CREATE TABLE IF NOT EXISTS stg_wfm_schedule (
  schedule_id                 INT64,
  agent_id                    INT64,
  shift_id                    INT64,
  sched_date                  STRING,
  start_epoch                 INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  end_epoch                   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  paid_minutes                INT64,
  activity_code               STRING,
  site_code                   STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY schedule_id, site_code;

CREATE TABLE IF NOT EXISTS stg_wfm_adherence_event (
  adherence_event_id          INT64,
  agent_id                    INT64,
  schedule_id                 INT64,
  exception_type              STRING,
  start_epoch                 INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  end_epoch                   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  approved_flag               BOOL,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY adherence_event_id;

CREATE TABLE IF NOT EXISTS stg_wfm_forecast (
  forecast_id                 INT64,
  queue_id                    INT64,
  interval_start_epoch        INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  forecast_volume             INT64,
  forecast_aht_sec            INT64,
  required_fte                NUMERIC(8,2),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY forecast_id;

CREATE TABLE IF NOT EXISTS stg_wfm_timeoff_request (
  timeoff_id                  INT64,
  agent_id                    INT64,
  request_epoch               INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  start_date                  STRING,
  end_date                    STRING,
  timeoff_type                STRING,
  status                      STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY timeoff_id;

-- ============================================================================
-- Telephony switch on Oracle (switchdb01) — 5 tables
-- ============================================================================

-- Hive CLUSTERED BY (call_id) INTO 16 BUCKETS dropped; PK clustering retained.
CREATE TABLE IF NOT EXISTS stg_tel_call (
  call_id                     INT64,
  ani                         STRING,
  dnis                        STRING,
  queue_id                    INT64,
  agent_id                    INT64,
  program_id                  INT64,
  start_epoch                 INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  answer_epoch                INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  end_epoch                   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  disposition_code            STRING,
  direction                   STRING,
  recording_id                STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY call_id;

CREATE TABLE IF NOT EXISTS stg_tel_call_segment (
  segment_id                  INT64,
  call_id                     INT64,
  segment_no                  INT64,
  segment_type                STRING,
  start_epoch                 INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  end_epoch                   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  agent_id                    INT64,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY segment_id;

CREATE TABLE IF NOT EXISTS stg_tel_queue (
  queue_id                    INT64,
  queue_code                  STRING,
  queue_name                  STRING,
  program_id                  INT64,
  media_type                  STRING,
  priority                    INT64,
  created_epoch               INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY queue_id;

CREATE TABLE IF NOT EXISTS stg_tel_agent_state_event (
  state_event_id              INT64,
  agent_id                    INT64,
  state_code                  STRING,
  start_epoch                 INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  end_epoch                   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  reason_code                 STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY state_event_id;

CREATE TABLE IF NOT EXISTS stg_tel_disposition_code (
  disposition_code            STRING,
  disposition_desc            STRING,
  category                    STRING,
  billable_flag               BOOL,
  created_epoch               INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY disposition_code;

-- ============================================================================
-- Ticketing on Postgres (tixdb01) — 3 tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS stg_tkt_ticket (
  ticket_id                   INT64,
  ticket_no                   STRING,
  program_id                  INT64,
  category_id                 INT64,
  opened_by_agent_id          INT64,
  assigned_agent_id           INT64,
  interaction_ref             STRING,
  priority                    STRING,
  status                      STRING,
  created_ms                  INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  updated_ms                  INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY ticket_id;

CREATE TABLE IF NOT EXISTS stg_tkt_ticket_event (
  ticket_event_id             INT64,
  ticket_id                   INT64,
  event_type                  STRING,
  event_ms                    INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  actor_agent_id              INT64,
  old_value                   STRING,
  new_value                   STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY ticket_event_id;

CREATE TABLE IF NOT EXISTS stg_tkt_category (
  category_id                 INT64,
  category_code               STRING,
  category_name               STRING,
  sla_hours                   INT64,
  created_ms                  INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY category_id;

-- ============================================================================
-- Finance/billing on SQL Server (findb01) — 3 tables
-- ============================================================================

CREATE TABLE IF NOT EXISTS stg_fin_invoice (
  invoice_id                  INT64,
  invoice_no                  STRING,
  client_id                   INT64,
  program_id                  INT64,
  period_month                STRING,
  issued_ts_sec               INT64 OPTIONS(description='!! name says seconds, VALUES ARE MILLIS !!'),
  due_ts_sec                  INT64 OPTIONS(description='!! name says seconds, VALUES ARE MILLIS !!'),
  currency                    STRING,
  total_amount                NUMERIC(14,2),
  status                      STRING,
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY invoice_id;

CREATE TABLE IF NOT EXISTS stg_fin_invoice_line (
  invoice_line_id             INT64,
  invoice_id                  INT64,
  contract_line_id            INT64,
  qty                         NUMERIC(12,2),
  unit_rate                   NUMERIC(12,4),
  line_amount                 NUMERIC(14,2),
  adjustment_flag             BOOL,
  created_ms                  INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY invoice_line_id;

CREATE TABLE IF NOT EXISTS stg_fin_rate_card (
  rate_card_id                INT64,
  program_id                  INT64,
  service_code                STRING,
  rate                        NUMERIC(12,4),
  currency                    STRING,
  effective_ts                INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  expiry_ts                   INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  load_date                   DATE
)
PARTITION BY load_date
CLUSTER BY rate_card_id;
