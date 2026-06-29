-- Seed fact_billing_line with ~120 rows across 3 period_month DATE partitions
-- and multiple client_sk/program_sk for cluster pruning tests.
-- period_month values: 2024-01-01, 2024-02-01, 2024-03-01

INSERT INTO fact_billing_line
  (invoice_line_id, invoice_id, client_sk, program_sk, service_code,
   qty, unit_rate, line_amount, adjustment_flag, invoice_status, period_month)
SELECT
  row_n                                 AS invoice_line_id,
  MOD(row_n, 30) + 1                   AS invoice_id,
  MOD(row_n, 5) + 1                    AS client_sk,
  MOD(row_n, 8) + 1                    AS program_sk,
  CASE MOD(row_n, 4)
    WHEN 0 THEN 'VOICE_IN'
    WHEN 1 THEN 'CHAT'
    WHEN 2 THEN 'EMAIL'
    ELSE 'QA_REVIEW'
  END                                   AS service_code,
  NUMERIC '100.00'                      AS qty,
  NUMERIC '18.5000'                     AS unit_rate,
  NUMERIC '1850.00'                     AS line_amount,
  MOD(row_n, 10) = 0                   AS adjustment_flag,
  CASE MOD(row_n, 3)
    WHEN 0 THEN 'ISSUED'
    WHEN 1 THEN 'PAID'
    ELSE 'DRAFT'
  END                                   AS invoice_status,
  CASE
    WHEN row_n <= 40  THEN DATE '2024-01-01'
    WHEN row_n <= 80  THEN DATE '2024-02-01'
    ELSE DATE '2024-03-01'
  END                                   AS period_month
FROM UNNEST(GENERATE_ARRAY(1, 120)) AS row_n;
