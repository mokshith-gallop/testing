-- Seed all 3 SCD-2 tables with rows across 3 eff_from_year partitions (2022, 2023, 2024).
-- ~90 rows total per table = 270 total SCD-2 rows.

-- ods_agent_scd2: 90 rows
INSERT INTO ods_agent_scd2
  (agent_history_id, agent_id, employee_no, org_unit_id, job_grade,
   employment_type, status, eff_from_ts, eff_to_ts, is_current, eff_from_year)
SELECT
  CONCAT('AH-', CAST(row_n AS STRING)) AS agent_history_id,
  MOD(row_n, 30) + 1                   AS agent_id,
  CONCAT('EMP', CAST(MOD(row_n, 30) + 1000 AS STRING)) AS employee_no,
  MOD(row_n, 10) + 1                   AS org_unit_id,
  CASE MOD(row_n, 4) WHEN 0 THEN 'A1' WHEN 1 THEN 'A2' WHEN 2 THEN 'SME' ELSE 'TL' END AS job_grade,
  CASE MOD(row_n, 2) WHEN 0 THEN 'FT' ELSE 'PT' END AS employment_type,
  'ACTIVE'                              AS status,
  TIMESTAMP('2024-01-01 00:00:00')      AS eff_from_ts,
  TIMESTAMP('9999-12-31 23:59:59')      AS eff_to_ts,
  row_n > 60                            AS is_current,
  CASE
    WHEN row_n <= 30  THEN 2022
    WHEN row_n <= 60  THEN 2023
    ELSE 2024
  END                                   AS eff_from_year
FROM UNNEST(GENERATE_ARRAY(1, 90)) AS row_n;

-- ods_agent_skill_scd2: 90 rows
INSERT INTO ods_agent_skill_scd2
  (agent_skill_history_id, agent_id, skill_id, skill_code, proficiency,
   certified, eff_from_ts, eff_to_ts, is_current, eff_from_year)
SELECT
  CONCAT('ASH-', CAST(row_n AS STRING)) AS agent_skill_history_id,
  MOD(row_n, 30) + 1                    AS agent_id,
  MOD(row_n, 10) + 1                    AS skill_id,
  CONCAT('SK', CAST(MOD(row_n, 10) + 1 AS STRING)) AS skill_code,
  MOD(row_n, 5) + 1                     AS proficiency,
  MOD(row_n, 3) = 0                     AS certified,
  TIMESTAMP('2024-01-01 00:00:00')       AS eff_from_ts,
  TIMESTAMP('9999-12-31 23:59:59')       AS eff_to_ts,
  row_n > 60                             AS is_current,
  CASE
    WHEN row_n <= 30  THEN 2022
    WHEN row_n <= 60  THEN 2023
    ELSE 2024
  END                                    AS eff_from_year
FROM UNNEST(GENERATE_ARRAY(1, 90)) AS row_n;

-- ods_agent_assignment_scd2: 90 rows
INSERT INTO ods_agent_assignment_scd2
  (assignment_history_id, agent_id, program_id, queue_id, role_on_program,
   eff_from_ts, eff_to_ts, is_current, eff_from_year)
SELECT
  CONCAT('AAH-', CAST(row_n AS STRING)) AS assignment_history_id,
  MOD(row_n, 30) + 1                    AS agent_id,
  MOD(row_n, 8) + 1                     AS program_id,
  MOD(row_n, 6) + 1                     AS queue_id,
  CASE MOD(row_n, 3) WHEN 0 THEN 'AGENT' WHEN 1 THEN 'SME' ELSE 'TL' END AS role_on_program,
  TIMESTAMP('2024-01-01 00:00:00')       AS eff_from_ts,
  TIMESTAMP('9999-12-31 23:59:59')       AS eff_to_ts,
  row_n > 60                             AS is_current,
  CASE
    WHEN row_n <= 30  THEN 2022
    WHEN row_n <= 60  THEN 2023
    ELSE 2024
  END                                    AS eff_from_year
FROM UNNEST(GENERATE_ARRAY(1, 90)) AS row_n;
