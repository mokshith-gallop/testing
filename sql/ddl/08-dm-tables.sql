-- ----------------------------------------------------------------------------
-- 08-dm-tables: BigQuery DDL for 9 dimensions + 9 facts + 7 aggregates
-- Migrated from Hive/Impala (CDH 6.3.4) — type mapping per locked rules.
--
-- Type mapping: BIGINT→INT64, INT→INT64, STRING→STRING, BOOLEAN→BOOL,
--   TIMESTAMP→TIMESTAMP, DECIMAL(p,s)→NUMERIC(p,s).
-- Dimensions: unpartitioned, no clustering per locked spec.
-- Facts with date_key INT: RANGE partition on date_key (YYYYMMDD ranges,
--   interval=100 ≈ monthly granularity, kept under 10K partition limit).
-- fact_interaction: multi-column partition (date_key, channel) collapsed to
--   RANGE on date_key only; channel demoted to first clustering column.
-- fact_billing_line: DATE partition on period_month (STRING→DATE).
-- fact_agent_activity: cluster on agent_sk only (program_sk absent from DDL).
-- fact_queue_interval: cluster on queue_sk only (site_code absent from DDL).
-- Aggregates: 4 with RANGE on date_key/week_start_key, 3 with DATE on
--   period_month.
-- 6 named clustering configs applied per locked spec.
-- Hive directives dropped: STORED AS, TBLPROPERTIES, CLUSTERED BY
--   (fact_interaction: 16 buckets).
-- No source COMMENTs in DM layer.
-- ----------------------------------------------------------------------------

-- ============================================================================
-- Dimensions (9) — unpartitioned, no clustering
-- ============================================================================

CREATE TABLE IF NOT EXISTS dim_date (
  date_key                    INT64,
  full_date                   STRING,
  day_of_week                 INT64,
  day_name                    STRING,
  week_of_year                INT64,
  month_no                    INT64,
  month_name                  STRING,
  quarter_no                  INT64,
  year_no                     INT64,
  is_weekend                  BOOL,
  is_holiday_us               BOOL,
  fiscal_period               STRING
);

CREATE TABLE IF NOT EXISTS dim_agent (
  agent_sk                    INT64,
  agent_id                    INT64,
  employee_no                 STRING,
  full_name                   STRING,
  job_grade                   STRING,
  employment_type             STRING,
  org_unit_id                 INT64,
  team_name                   STRING,
  site_code                   STRING,
  status                      STRING,
  hire_date_key               INT64,
  is_current                  BOOL
);

CREATE TABLE IF NOT EXISTS dim_client (
  client_sk                   INT64,
  client_id                   INT64,
  client_code                 STRING,
  client_name                 STRING,
  industry                    STRING,
  hq_country                  STRING,
  primary_contact_name        STRING,
  primary_contact_email       STRING,
  status                      STRING
);

CREATE TABLE IF NOT EXISTS dim_program (
  program_sk                  INT64,
  program_id                  INT64,
  program_code                STRING,
  program_name                STRING,
  client_id                   INT64,
  line_of_business            STRING,
  channel_mix                 STRING,
  site_code                   STRING,
  billing_model               STRING,
  status                      STRING,
  go_live_date_key            INT64
);

CREATE TABLE IF NOT EXISTS dim_queue (
  queue_sk                    INT64,
  queue_id                    INT64,
  queue_code                  STRING,
  queue_name                  STRING,
  program_id                  INT64,
  media_type                  STRING,
  priority                    INT64
);

CREATE TABLE IF NOT EXISTS dim_site (
  site_sk                     INT64,
  site_code                   STRING,
  site_name                   STRING,
  region                      STRING,
  country                     STRING,
  timezone                    STRING
);

CREATE TABLE IF NOT EXISTS dim_shift (
  shift_sk                    INT64,
  shift_id                    INT64,
  shift_code                  STRING,
  shift_name                  STRING,
  start_hhmm                  STRING,
  end_hhmm                    STRING,
  overnight_flag              BOOL,
  site_code                   STRING
);

CREATE TABLE IF NOT EXISTS dim_org (
  org_sk                      INT64,
  org_unit_id                 INT64,
  unit_code                   STRING,
  unit_name                   STRING,
  unit_type                   STRING,
  level1_name                 STRING,
  level2_name                 STRING,
  level3_name                 STRING,
  level4_name                 STRING,
  site_code                   STRING,
  cost_center                 STRING
);

CREATE TABLE IF NOT EXISTS dim_disposition (
  disposition_sk              INT64,
  disposition_code            STRING,
  disposition_desc            STRING,
  category                    STRING,
  billable_flag               BOOL
);

-- ============================================================================
-- Facts (9) — RANGE or DATE partitioned, named clustering where specified
-- ============================================================================

-- Multi-column partition (date_key, channel) collapsed to RANGE on date_key.
-- channel demoted to first clustering column.
-- Hive CLUSTERED BY (agent_sk) INTO 16 BUCKETS dropped; named clustering applied.
-- NOTE: fact_interaction.channel demoted from 2nd partition column per locked spec.
CREATE TABLE IF NOT EXISTS fact_interaction (
  interaction_id              STRING,
  client_sk                   INT64,
  program_sk                  INT64,
  queue_sk                    INT64,
  agent_sk                    INT64,
  customer_ref                STRING,
  start_ts                    TIMESTAMP,
  end_ts                      TIMESTAMP,
  handle_seconds              INT64,
  resolved_flag               BOOL,
  source_system               STRING,
  channel                     STRING,
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100))
CLUSTER BY channel, agent_sk, client_sk;

-- NOTE: locked spec clustering (agent_sk, program_sk) — program_sk ABSENT from
-- source DDL. Clustering applied to existing columns only: agent_sk.
-- Flagged for decision-owner review.
CREATE TABLE IF NOT EXISTS fact_agent_activity (
  agent_sk                    INT64,
  state_code                  STRING,
  state_seconds               INT64,
  occurrence_count            INT64,
  first_state_ts              TIMESTAMP,
  last_state_ts               TIMESTAMP,
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100))
CLUSTER BY agent_sk;

-- NOTE: locked spec clustering (queue_sk, site_code) — site_code ABSENT from
-- source DDL. Clustering applied to existing columns only: queue_sk.
-- Flagged for decision-owner review.
CREATE TABLE IF NOT EXISTS fact_queue_interval (
  queue_sk                    INT64,
  interval_start_ts           TIMESTAMP,
  offered                     INT64,
  answered                    INT64,
  abandoned                   INT64,
  answered_in_sl              INT64,
  sl_threshold_sec            INT64,
  avg_speed_answer_sec        NUMERIC(8,2),
  avg_handle_sec              NUMERIC(8,2),
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100))
CLUSTER BY queue_sk;

CREATE TABLE IF NOT EXISTS fact_csat_survey (
  survey_id                   STRING,
  interaction_id              STRING,
  client_sk                   INT64,
  program_sk                  INT64,
  agent_sk                    INT64,
  survey_ts                   TIMESTAMP,
  csat_score                  INT64,
  nps_score                   INT64,
  fcr_claimed                 BOOL,
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100));

CREATE TABLE IF NOT EXISTS fact_qa_evaluation (
  qa_form_id                  STRING,
  interaction_id              STRING,
  agent_sk                    INT64,
  program_sk                  INT64,
  evaluated_ts                TIMESTAMP,
  scored_points               INT64,
  max_points                  INT64,
  overall_pct                 NUMERIC(5,2),
  auto_fail                   BOOL,
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100));

-- DATE partition on period_month (STRING→DATE, first-of-month values).
CREATE TABLE IF NOT EXISTS fact_billing_line (
  invoice_line_id             INT64,
  invoice_id                  INT64,
  client_sk                   INT64,
  program_sk                  INT64,
  service_code                STRING,
  qty                         NUMERIC(12,2),
  unit_rate                   NUMERIC(12,4),
  line_amount                 NUMERIC(14,2),
  adjustment_flag             BOOL,
  invoice_status              STRING,
  period_month                DATE
)
PARTITION BY period_month
CLUSTER BY client_sk, program_sk, invoice_status;

CREATE TABLE IF NOT EXISTS fact_adherence_daily (
  agent_sk                    INT64,
  scheduled_minutes           INT64,
  worked_minutes              INT64,
  exception_minutes           INT64,
  timeoff_minutes             INT64,
  adherence_pct               NUMERIC(5,2),
  occupancy_pct               NUMERIC(5,2),
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100));

CREATE TABLE IF NOT EXISTS fact_ticket (
  ticket_id                   INT64,
  program_sk                  INT64,
  category_code               STRING,
  assigned_agent_sk           INT64,
  priority                    STRING,
  status                      STRING,
  created_ts                  TIMESTAMP,
  resolved_ts                 TIMESTAMP,
  resolution_minutes          INT64,
  sla_breached_flag           BOOL,
  touch_count                 INT64,
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100));

CREATE TABLE IF NOT EXISTS fact_ivr_path (
  session_ref                 STRING,
  client_code                 STRING,
  menu_path_full              STRING,
  hops                        INT64,
  contained_flag              BOOL,
  exit_key                    STRING,
  duration_seconds            INT64,
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100));

-- ============================================================================
-- Aggregates (7) — 4 RANGE on date_key/week_start_key, 3 DATE on period_month
-- ============================================================================

CREATE TABLE IF NOT EXISTS agg_agent_daily (
  agent_sk                    INT64,
  site_code                   STRING,
  interactions_handled        INT64,
  avg_handle_seconds          NUMERIC(8,2),
  talk_seconds                INT64,
  acw_seconds                 INT64,
  aux_seconds                 INT64,
  adherence_pct               NUMERIC(5,2),
  occupancy_pct               NUMERIC(5,2),
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100))
CLUSTER BY agent_sk, site_code;

CREATE TABLE IF NOT EXISTS agg_agent_weekly (
  agent_sk                    INT64,
  site_code                   STRING,
  days_worked                 INT64,
  interactions_handled        INT64,
  avg_handle_seconds          NUMERIC(8,2),
  adherence_pct               NUMERIC(5,2),
  occupancy_pct               NUMERIC(5,2),
  week_start_key              INT64
)
PARTITION BY RANGE_BUCKET(week_start_key, GENERATE_ARRAY(20150101, 20360101, 100));

-- DATE partition on period_month (STRING→DATE, first-of-month values).
CREATE TABLE IF NOT EXISTS agg_program_monthly (
  client_sk                   INT64,
  program_sk                  INT64,
  line_of_business            STRING,
  interactions                INT64,
  avg_handle_seconds          NUMERIC(8,2),
  avg_csat                    NUMERIC(5,2),
  billed_amount               NUMERIC(14,2),
  grouping_level              INT64,
  period_month                DATE
)
PARTITION BY period_month;

CREATE TABLE IF NOT EXISTS agg_queue_hourly (
  queue_sk                    INT64,
  hour_of_day                 INT64,
  offered                     INT64,
  answered                    INT64,
  abandoned                   INT64,
  sl_pct                      NUMERIC(5,2),
  forecast_volume             INT64,
  volume_variance_pct         NUMERIC(7,2),
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100));

-- DATE partition on period_month (STRING→DATE, first-of-month values).
CREATE TABLE IF NOT EXISTS agg_csat_rollup_monthly (
  client_sk                   INT64,
  program_sk                  INT64,
  site_code                   STRING,
  surveys                     INT64,
  avg_csat                    NUMERIC(5,2),
  pct_promoters               NUMERIC(5,2),
  pct_detractors              NUMERIC(5,2),
  grouping_id                 INT64,
  period_month                DATE
)
PARTITION BY period_month;

-- DATE partition on period_month (STRING→DATE, first-of-month values).
CREATE TABLE IF NOT EXISTS agg_billing_monthly (
  client_sk                   INT64,
  program_sk                  INT64,
  billed_amount               NUMERIC(14,2),
  sla_credit_amount           NUMERIC(12,2),
  telco_cost_amount           NUMERIC(12,2),
  net_revenue                 NUMERIC(14,2),
  period_month                DATE
)
PARTITION BY period_month
CLUSTER BY client_sk, program_sk;

CREATE TABLE IF NOT EXISTS agg_site_daily (
  site_code                   STRING,
  agents_active               INT64,
  interactions                INT64,
  avg_handle_seconds          NUMERIC(8,2),
  sl_pct                      NUMERIC(5,2),
  adherence_pct               NUMERIC(5,2),
  date_key                    INT64
)
PARTITION BY RANGE_BUCKET(date_key, GENERATE_ARRAY(20150101, 20360101, 100));
