-- A tiny stand-in for real legacy DDL, with the Hive-only clauses that break a verbatim
-- apply to a sandbox: a cluster hdfs:// LOCATION and CREATE EXTERNAL TABLE. The comment on
-- the next line intentionally contains a ';' to exercise comment-aware splitting; do not edit.
CREATE DATABASE IF NOT EXISTS dmt_legacy
  LOCATION 'hdfs://legacy-cluster/data/dmt_legacy';

CREATE EXTERNAL TABLE IF NOT EXISTS dmt_legacy.ss_demo (
  id          BIGINT,
  nm          STRING,
  created_ts  BIGINT COMMENT 'epoch SECONDS (legacy)'
)
STORED AS PARQUET
LOCATION 'hdfs://legacy-cluster/data/dmt_legacy/ss_demo'
TBLPROPERTIES ('parquet.compression'='SNAPPY');

-- ACID/managed transactional table — exercises the ACID -> non-ACID rewrite (our HS2 cannot
-- create an ACID table; the sanitize flips 'transactional'='true' to 'false'; schema unchanged).
CREATE TABLE IF NOT EXISTS dmt_legacy.ss_acid (
  id   BIGINT,
  amt  DECIMAL(12,2)
)
STORED AS ORC
TBLPROPERTIES ('transactional'='true');

-- RegexSerDe table whose input.regex contains ';' — exercises the quote-aware statement splitter
-- (a naive split on ';' would shred this CREATE into broken fragments and abort the run).
CREATE EXTERNAL TABLE IF NOT EXISTS dmt_legacy.ss_logs (
  part_a STRING,
  part_b STRING
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.RegexSerDe'
WITH SERDEPROPERTIES ('input.regex'='([^;]*);(.*)')
STORED AS TEXTFILE
LOCATION 'hdfs://legacy-cluster/data/dmt_legacy/ss_logs';
