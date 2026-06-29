-- Seed fact_interaction with ~210 rows across 3 date_key partitions and
-- multiple channels/agents/clients for partition + cluster pruning tests.
-- date_key values: 20240101, 20240201, 20240301 (70 rows each)

INSERT INTO fact_interaction
  (interaction_id, client_sk, program_sk, queue_sk, agent_sk,
   customer_ref, start_ts, end_ts, handle_seconds, resolved_flag,
   source_system, channel, date_key)
SELECT
  CONCAT('INT-', CAST(row_n AS STRING)) AS interaction_id,
  MOD(row_n, 5) + 1                    AS client_sk,
  MOD(row_n, 8) + 1                    AS program_sk,
  MOD(row_n, 6) + 1                    AS queue_sk,
  MOD(row_n, 20) + 1                   AS agent_sk,
  CONCAT('CUST-', CAST(MOD(row_n, 50) AS STRING)) AS customer_ref,
  TIMESTAMP('2024-01-15 08:00:00')      AS start_ts,
  TIMESTAMP('2024-01-15 08:05:00')      AS end_ts,
  300                                   AS handle_seconds,
  MOD(row_n, 3) = 0                    AS resolved_flag,
  'TELEPHONY'                           AS source_system,
  CASE MOD(row_n, 3)
    WHEN 0 THEN 'VOICE'
    WHEN 1 THEN 'CHAT'
    ELSE 'EMAIL'
  END                                   AS channel,
  CASE
    WHEN row_n <= 70  THEN 20240101
    WHEN row_n <= 140 THEN 20240201
    ELSE 20240301
  END                                   AS date_key
FROM UNNEST(GENERATE_ARRAY(1, 210)) AS row_n;
