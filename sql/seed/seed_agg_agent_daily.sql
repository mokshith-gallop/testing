-- Seed agg_agent_daily with ~120 rows across 3 date_key partitions
-- and multiple agent_sk/site_code for cluster pruning tests.
-- date_key values: 20240101, 20240201, 20240301

INSERT INTO agg_agent_daily
  (agent_sk, site_code, interactions_handled, avg_handle_seconds,
   talk_seconds, acw_seconds, aux_seconds, adherence_pct, occupancy_pct,
   date_key)
SELECT
  MOD(row_n, 20) + 1                   AS agent_sk,
  CASE MOD(row_n, 3)
    WHEN 0 THEN 'MNL1'
    WHEN 1 THEN 'BLR2'
    ELSE 'MTY3'
  END                                   AS site_code,
  25 + MOD(row_n, 15)                  AS interactions_handled,
  NUMERIC '320.50'                      AS avg_handle_seconds,
  CAST(7200 + MOD(row_n, 1800) AS INT64) AS talk_seconds,
  CAST(1200 + MOD(row_n, 600)  AS INT64) AS acw_seconds,
  CAST(900  + MOD(row_n, 300)  AS INT64) AS aux_seconds,
  NUMERIC '92.50'                       AS adherence_pct,
  NUMERIC '78.30'                       AS occupancy_pct,
  CASE
    WHEN row_n <= 40  THEN 20240101
    WHEN row_n <= 80  THEN 20240201
    ELSE 20240301
  END                                   AS date_key
FROM UNNEST(GENERATE_ARRAY(1, 120)) AS row_n;
