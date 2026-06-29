-- ----------------------------------------------------------------------------
-- 04-staging-file-feeds: BigQuery DDL for 10 SFTP/file-landed client feeds
-- Migrated from Hive/Impala (CDH 6.3.4) — type mapping per locked rules.
--
-- Type mapping: BIGINT→INT64, INT→INT64, STRING→STRING, BOOLEAN→BOOL,
--   DECIMAL(p,s)→NUMERIC(p,s), DOUBLE→FLOAT64.
-- Complex types: ARRAY<STRUCT<...>>→ARRAY<STRUCT<...>> (INT→INT64 inside),
--   MAP<STRING,STRING>→ARRAY<STRUCT<key STRING, value STRING>>,
--   ARRAY<STRING>→ARRAY<STRING>.
-- Partition: multi-column (client_code, feed_date) collapsed to feed_date
--   DATE only; client_code demoted to regular column + clustering column.
-- Clustering: PK column(s) + client_code per locked spec.
-- Hive directives dropped: EXTERNAL, ROW FORMAT, SERDE, STORED AS, LOCATION,
--   TBLPROPERTIES.
-- All COMMENTs carried as OPTIONS(description=...).
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stg_file_interaction_export (
  interaction_ref             STRING,
  channel                     STRING,
  client_interaction_id       STRING,
  agent_email                 STRING,
  start_ms                    INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  end_ms                      INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  outcome                     STRING,
  customer_ref                STRING,
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY interaction_ref, client_code;

CREATE TABLE IF NOT EXISTS stg_file_survey_csat (
  survey_id                   STRING,
  interaction_ref             STRING,
  survey_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  csat_score                  INT64,
  nps_score                   INT64,
  fcr_claimed                 BOOL,
  verbatim                    STRING,
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY survey_id, client_code;

CREATE TABLE IF NOT EXISTS stg_file_qa_forms (
  qa_form_id                  STRING,
  interaction_ref             STRING,
  evaluator_email             STRING,
  evaluated_ms                INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  form_version                STRING,
  sections                    ARRAY<STRUCT<section_code STRING, max_points INT64, scored_points INT64>>,
  auto_fail                   BOOL,
  overall_pct                 NUMERIC(5,2),
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY qa_form_id, client_code;

CREATE TABLE IF NOT EXISTS stg_file_ivr_logs (
  event_ms                    INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  session_ref                 STRING,
  menu_path                   STRING,
  key_pressed                 STRING,
  raw_tail                    STRING,
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY client_code;

CREATE TABLE IF NOT EXISTS stg_file_chat_transcripts (
  chat_ref                    STRING,
  queue_code                  STRING,
  agent_email                 STRING,
  started_ms                  INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  ended_ms                    INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  messages                    ARRAY<STRUCT<sender STRING, ts_ms INT64, text STRING>>,
  metadata                    ARRAY<STRUCT<key STRING, value STRING>>,
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY chat_ref, client_code;

CREATE TABLE IF NOT EXISTS stg_file_roster (
  employee_no                 STRING,
  agent_email                 STRING,
  client_login                STRING,
  role_on_program             STRING,
  active_flag                 BOOL,
  as_of_ms                    INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY employee_no, client_code;

CREATE TABLE IF NOT EXISTS stg_file_telco_invoice (
  telco_invoice_id            STRING,
  carrier                     STRING,
  circuit_id                  STRING,
  usage_minutes               INT64,
  charge_amount               NUMERIC(12,2),
  bill_period                 STRING,
  billed_ms                   INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY telco_invoice_id, client_code;

CREATE TABLE IF NOT EXISTS stg_file_dialer_result (
  attempt_id                  STRING,
  campaign_code               STRING,
  phone_hash                  STRING,
  agent_id                    INT64,
  attempt_ms                  INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  result_code                 STRING,
  talk_seconds                INT64,
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY attempt_id, client_code;

CREATE TABLE IF NOT EXISTS stg_file_email_interaction (
  email_ref                   STRING,
  mailbox                     STRING,
  agent_email                 STRING,
  received_ms                 INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  first_reply_ms              INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  resolved_ms                 INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  subject_category            STRING,
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY email_ref, client_code;

CREATE TABLE IF NOT EXISTS stg_file_speech_analytics (
  recording_id                STRING,
  call_ref                    STRING,
  analyzed_ms                 INT64 OPTIONS(description='epoch MILLISECONDS (legacy)'),
  sentiment_score             FLOAT64,
  silence_pct                 FLOAT64,
  talk_over_count             INT64,
  keywords                    ARRAY<STRING>,
  client_code                 STRING,
  feed_date                   DATE
)
PARTITION BY feed_date
CLUSTER BY recording_id, client_code;
