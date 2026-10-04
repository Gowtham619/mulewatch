-- =====================================================================
-- MuleWatch :: 09_tasks.sql  - autonomous orchestration (Snowflake Tasks DAG)
--
--   MW_STREAM_SIM (every 5 min)      : simulated live UPI traffic, sometimes a new ring
--
--   MW_ROOT_SCORE (every 15 min)     : rules + ML scoring (features refreshed by dynamic tables)
--     -> MW_RINGS                    : graph ring detection + case creation
--        -> MW_ENRICH                : Cortex AI enrichment of the alerted population
--           -> MW_TRIAGE             : triage agent
--              -> MW_INVESTIGATE     : investigator + compliance agents on top 2 new cases
--                 -> MW_EVALUATE     : precision / recall vs held-out ground truth
--
--   MW_LEARN (daily 02:00 IST)       : rule re-weighting + model retraining from analyst outcomes
--
-- Everything is created SUSPENDED to protect trial credits. Resume for the demo, suspend after
-- (see 99_suspend_all.sql).
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE SCHEMA APP;
USE WAREHOUSE MULEWATCH_WH;

CREATE OR REPLACE TASK APP.MW_STREAM_SIM
  WAREHOUSE = MULEWATCH_WH
  SCHEDULE = '5 MINUTE'
  COMMENT = 'Simulated live traffic'
AS CALL APP.SIMULATE_STREAM(1500, 0.2);

CREATE OR REPLACE TASK APP.MW_ROOT_SCORE
  WAREHOUSE = MULEWATCH_WH
  SCHEDULE = '15 MINUTE'
  COMMENT = 'Root of the detection DAG'
AS CALL APP.SCORE_ACCOUNTS();

CREATE OR REPLACE TASK APP.MW_RINGS
  WAREHOUSE = MULEWATCH_WH AFTER APP.MW_ROOT_SCORE
AS CALL APP.DETECT_RINGS_AND_CASES();

CREATE OR REPLACE TASK APP.MW_ENRICH
  WAREHOUSE = MULEWATCH_WH AFTER APP.MW_RINGS
AS CALL APP.ENRICH_ALERTED();

CREATE OR REPLACE TASK APP.MW_TRIAGE
  WAREHOUSE = MULEWATCH_WH AFTER APP.MW_ENRICH
AS CALL APP.TRIAGE_AGENT(200);

CREATE OR REPLACE TASK APP.MW_INVESTIGATE
  WAREHOUSE = MULEWATCH_WH AFTER APP.MW_TRIAGE
AS CALL APP.RUN_INVESTIGATIONS(2);

CREATE OR REPLACE TASK APP.MW_EVALUATE
  WAREHOUSE = MULEWATCH_WH AFTER APP.MW_INVESTIGATE
AS CALL APP.EVALUATE_DETECTION();

CREATE OR REPLACE PROCEDURE APP.LEARN_FROM_FEEDBACK()
  RETURNS VARIANT
  LANGUAGE SQL
  EXECUTE AS OWNER
AS
$$
DECLARE
  r1 VARIANT;
  r2 VARIANT;
BEGIN
  CALL APP.TUNE_RULES(5) INTO :r1;
  CALL APP.TRAIN_MULE_MODEL() INTO :r2;
  RETURN OBJECT_CONSTRUCT('tune_rules', :r1, 'train_model', :r2);
END;
$$;

CREATE OR REPLACE TASK APP.MW_LEARN
  WAREHOUSE = MULEWATCH_WH
  SCHEDULE = 'USING CRON 30 20 * * * UTC'   -- 02:00 IST
  COMMENT = 'Learning loop: analyst outcomes -> rule weights + model'
AS CALL APP.LEARN_FROM_FEEDBACK();

-- ---------------------------------------------------------------------
-- DEMO: turn on autonomous mode (uncomment). Child tasks first, root last.
-- ---------------------------------------------------------------------
-- SELECT SYSTEM$TASK_DEPENDENTS_ENABLE('MULEWATCH.APP.MW_ROOT_SCORE');
-- ALTER TASK APP.MW_STREAM_SIM RESUME;
-- ALTER TASK APP.MW_LEARN RESUME;
--
-- Or run the DAG once on demand:
-- EXECUTE TASK APP.MW_ROOT_SCORE;
--
-- Monitor:
-- SELECT NAME, STATE, SCHEDULED_TIME, COMPLETED_TIME, ERROR_MESSAGE
-- FROM TABLE(INFORMATION_SCHEMA.TASK_HISTORY()) ORDER BY SCHEDULED_TIME DESC LIMIT 20;
