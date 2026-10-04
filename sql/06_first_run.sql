-- =====================================================================
-- MuleWatch :: 06_first_run.sql  - train the model and run the whole pipeline once
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE WAREHOUSE MULEWATCH_WH;

CALL APP.TRAIN_MULE_MODEL();          -- learns from HISTORICAL_LABELS (past case outcomes)
CALL APP.RUN_FULL_PIPELINE(3);        -- score -> rings -> enrich -> triage -> investigate + STR (top 3) -> evaluate

SELECT DETECTOR, PRECISION, RECALL, F1, ALERTED FROM ANALYTICS.EVAL_RESULTS
QUALIFY RUN_TS = MAX(RUN_TS) OVER () ORDER BY DETECTOR;

SELECT CASE_ID, TYPOLOGY, PRIORITY, STATUS, AMOUNT_AT_RISK, MEMBER_COUNT
FROM ANALYTICS.CASES ORDER BY PRIORITY_SCORE DESC NULLS LAST LIMIT 10;
