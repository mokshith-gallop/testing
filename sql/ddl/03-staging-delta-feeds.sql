-- ----------------------------------------------------------------------------
-- 03-staging-delta-feeds: BigQuery DDL for 8 CDC delta feed tables
-- Migrated from Hive/Impala (CDH 6.3.4) — type mapping per locked rules.
--
-- Type mapping: BIGINT→INT64, INT→INT64, STRING→STRING, BOOLEAN→BOOL,
--   DECIMAL(p,s)→NUMERIC(p,s).
-- Partition: extract_ts STRING→DATE (cast from STRING).
-- Clustering: PK column(s) per locked spec.
-- Hive directives dropped: EXTERNAL, ROW FORMAT, STORED AS, LOCATION.
-- All COMMENTs carried as OPTIONS(description=...).
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_fin_timesheet_delta (
  timesheet_id                INT64,
  agent_id                    INT64,
  work_date                   STRING,
  program_id                  INT64,
  billable_minutes            INT64,
  nonbillable_minutes         INT64,
  approved_flag               BOOL,
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY timesheet_id;

CREATE TABLE IF NOT EXISTS stg_fin_payroll_adj_delta (
  adjustment_id               INT64,
  agent_id                    INT64,
  period_month                STRING,
  adj_type                    STRING,
  amount                      NUMERIC(12,2),
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY adjustment_id;

CREATE TABLE IF NOT EXISTS stg_crm_sla_credit_delta (
  sla_credit_id               INT64,
  program_id                  INT64,
  sla_target_id               INT64,
  period_month                STRING,
  credit_amount               NUMERIC(12,2),
  reason                      STRING,
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY sla_credit_id;

CREATE TABLE IF NOT EXISTS stg_tel_callback_request_delta (
  callback_id                 INT64,
  call_id                     INT64,
  queue_id                    INT64,
  requested_epoch             INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  scheduled_epoch             INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  completed_flag              BOOL,
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY callback_id;

CREATE TABLE IF NOT EXISTS stg_wfm_shift_swap_delta (
  swap_id                     INT64,
  requesting_agent_id         INT64,
  accepting_agent_id          INT64,
  schedule_id                 INT64,
  swap_date                   STRING,
  status                      STRING,
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY swap_id;

CREATE TABLE IF NOT EXISTS stg_tkt_worklog_delta (
  worklog_id                  INT64,
  ticket_id                   INT64,
  agent_id                    INT64,
  minutes_logged              INT64,
  log_ms                      INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  note                        STRING,
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY worklog_id;

CREATE TABLE IF NOT EXISTS stg_hr_attrition_event_delta (
  attrition_event_id          INT64,
  agent_id                    INT64,
  notice_epoch                INT64 OPTIONS(description='epoch SECONDS (legacy)'),
  last_day                    STRING,
  attrition_type              STRING,
  reason_code                 STRING,
  regrettable_flag            BOOL,
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY attrition_event_id;

CREATE TABLE IF NOT EXISTS stg_fin_rate_card_change_delta (
  rate_change_id              INT64,
  rate_card_id                INT64,
  old_rate                    NUMERIC(12,4),
  new_rate                    NUMERIC(12,4),
  change_reason               STRING,
  op                          STRING,
  change_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  extract_ts                  DATE
)
PARTITION BY extract_ts
CLUSTER BY rate_change_id;
