-- CUT artifact: the ods_invoice transform (ELT, runs in BigQuery).
--   * issued_ms holds epoch MILLIS -> TIMESTAMP
--   * keep insert/update ops, drop deletes
--   * normalize op to upper-case
-- Reads the raw staging table, writes the modeled ods_invoice. Both unqualified ->
-- resolved in the build dataset.
CREATE OR REPLACE TABLE ods_invoice AS
SELECT
  invoice_id,
  contact_id,
  amount,
  TIMESTAMP_MILLIS(issued_ms) AS issued_ts,
  UPPER(op)                   AS op
FROM ods_invoice_raw
WHERE UPPER(op) IN ('I', 'U');   -- normalize before filtering (source casing is messy)
