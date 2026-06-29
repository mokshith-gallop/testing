-- CUT artifact: target DDL for the migrated ods_invoice. Applied VERBATIM by the
-- harness into the build dataset (unqualified name -> default-dataset redirection).
CREATE TABLE IF NOT EXISTS ods_invoice (
  invoice_id  INT64,
  contact_id  INT64,
  amount      NUMERIC,
  issued_ts   TIMESTAMP,
  op          STRING
);
