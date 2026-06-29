-- CUT artifact (the migration's transform), applied VERBATIM by the harness.
-- Unqualified table refs resolve to the build dataset (default-dataset redirection).
--   * epoch millis -> TIMESTAMP (the issued_ms column holds MILLIS)
--   * keep only insert/update ops (drop deletes)
CREATE OR REPLACE TABLE ods_invoice AS
SELECT
  invoice_id,
  amount,
  TIMESTAMP_MILLIS(issued_ms) AS issued_ts
FROM ods_invoice_raw
WHERE op IN ('I', 'U');
