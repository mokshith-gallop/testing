-- Seed agg_billing_monthly with ~60 rows across 3 period_month DATE partitions
-- and multiple client_sk/program_sk for cluster pruning tests.
-- period_month values: 2024-01-01, 2024-02-01, 2024-03-01

INSERT INTO agg_billing_monthly
  (client_sk, program_sk, billed_amount, sla_credit_amount,
   telco_cost_amount, net_revenue, period_month)
SELECT
  MOD(row_n, 5) + 1                    AS client_sk,
  MOD(row_n, 8) + 1                    AS program_sk,
  NUMERIC '125000.00'                   AS billed_amount,
  NUMERIC '2500.00'                     AS sla_credit_amount,
  NUMERIC '8000.00'                     AS telco_cost_amount,
  NUMERIC '114500.00'                   AS net_revenue,
  CASE
    WHEN row_n <= 20  THEN DATE '2024-01-01'
    WHEN row_n <= 40  THEN DATE '2024-02-01'
    ELSE DATE '2024-03-01'
  END                                   AS period_month
FROM UNNEST(GENERATE_ARRAY(1, 60)) AS row_n;
