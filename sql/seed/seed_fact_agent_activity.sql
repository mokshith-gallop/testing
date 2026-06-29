-- Seed fact_agent_activity with ~120 rows across 3 date_key partitions
-- and multiple agent_sk for cluster pruning tests.
-- date_key values: 20240101, 20240201, 20240301

INSERT INTO fact_agent_activity
  (agent_sk, state_code, state_seconds, occurrence_count,
   first_state_ts, last_state_ts, date_key)
SELECT
  MOD(row_n, 20) + 1                   AS agent_sk,
  CASE MOD(row_n, 5)
    WHEN 0 THEN 'READY'
    WHEN 1 THEN 'TALK'
    WHEN 2 THEN 'HOLD'
    WHEN 3 THEN 'ACW'
    ELSE 'AUX_BREAK'
  END                                   AS state_code,
  CAST(1800 + MOD(row_n, 3600) AS INT64) AS state_seconds,
  10 + MOD(row_n, 20)                  AS occurrence_count,
  TIMESTAMP('2024-01-15 08:00:00')      AS first_state_ts,
  TIMESTAMP('2024-01-15 17:00:00')      AS last_state_ts,
  CASE
    WHEN row_n <= 40  THEN 20240101
    WHEN row_n <= 80  THEN 20240201
    ELSE 20240301
  END                                   AS date_key
FROM UNNEST(GENERATE_ARRAY(1, 120)) AS row_n;
