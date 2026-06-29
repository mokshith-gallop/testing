-- Seed fact_queue_interval with ~120 rows across 3 date_key partitions
-- and multiple queue_sk for cluster pruning tests.
-- date_key values: 20240101, 20240201, 20240301

INSERT INTO fact_queue_interval
  (queue_sk, interval_start_ts, offered, answered, abandoned,
   answered_in_sl, sl_threshold_sec, avg_speed_answer_sec, avg_handle_sec,
   date_key)
SELECT
  MOD(row_n, 10) + 1                   AS queue_sk,
  TIMESTAMP('2024-01-15 08:00:00')      AS interval_start_ts,
  50 + MOD(row_n, 30)                  AS offered,
  45 + MOD(row_n, 25)                  AS answered,
  MOD(row_n, 8)                        AS abandoned,
  40 + MOD(row_n, 20)                  AS answered_in_sl,
  20                                    AS sl_threshold_sec,
  NUMERIC '12.50'                       AS avg_speed_answer_sec,
  NUMERIC '320.00'                      AS avg_handle_sec,
  CASE
    WHEN row_n <= 40  THEN 20240101
    WHEN row_n <= 80  THEN 20240201
    ELSE 20240301
  END                                   AS date_key
FROM UNNEST(GENERATE_ARRAY(1, 120)) AS row_n;
