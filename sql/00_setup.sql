-- =====================================================================
-- MuleWatch :: 00_setup.sql
-- Account-level setup: warehouse, database, schemas, roles, stages, config.
-- Run as ACCOUNTADMIN (trial accounts: the default user has it).
-- =====================================================================
USE ROLE ACCOUNTADMIN;

-- Let Cortex route to models that are not hosted in your region
-- (needed on many trial accounts for Claude / GPT class models).
ALTER ACCOUNT SET CORTEX_ENABLED_CROSS_REGION = 'ANY_REGION';

-- ---------------------------------------------------------------------
-- Compute: one X-Small warehouse, aggressive auto-suspend to protect credits
-- ---------------------------------------------------------------------
CREATE WAREHOUSE IF NOT EXISTS MULEWATCH_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'MuleWatch - agentic mule-ring investigator';

-- Hard stop on spend: 40 credits/month on this warehouse (well inside $400 trial)
CREATE RESOURCE MONITOR IF NOT EXISTS MULEWATCH_RM
  WITH CREDIT_QUOTA = 40 FREQUENCY = MONTHLY START_TIMESTAMP = IMMEDIATELY
  TRIGGERS ON 80 PERCENT DO NOTIFY
           ON 100 PERCENT DO SUSPEND;
ALTER WAREHOUSE MULEWATCH_WH SET RESOURCE_MONITOR = MULEWATCH_RM;

-- ---------------------------------------------------------------------
-- Roles (least privilege)
--   MULEWATCH_ADMIN   : owns all objects, runs pipelines
--   FINCRIME_MANAGER  : sees unmasked PII, all regions, approves STRs
--   FINCRIME_ANALYST  : masked PII, only mapped regions
-- ---------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS MULEWATCH_ADMIN;
CREATE ROLE IF NOT EXISTS FINCRIME_MANAGER;
CREATE ROLE IF NOT EXISTS FINCRIME_ANALYST;
GRANT ROLE FINCRIME_ANALYST TO ROLE FINCRIME_MANAGER;
GRANT ROLE FINCRIME_MANAGER TO ROLE MULEWATCH_ADMIN;
GRANT ROLE MULEWATCH_ADMIN  TO ROLE SYSADMIN;

SET MY_USER = CURRENT_USER();
GRANT ROLE MULEWATCH_ADMIN  TO USER IDENTIFIER($MY_USER);
GRANT ROLE FINCRIME_MANAGER TO USER IDENTIFIER($MY_USER);
GRANT ROLE FINCRIME_ANALYST TO USER IDENTIFIER($MY_USER);

GRANT USAGE, OPERATE ON WAREHOUSE MULEWATCH_WH TO ROLE MULEWATCH_ADMIN;
GRANT USAGE ON WAREHOUSE MULEWATCH_WH TO ROLE FINCRIME_ANALYST;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE MULEWATCH_ADMIN;
GRANT EXECUTE TASK ON ACCOUNT TO ROLE MULEWATCH_ADMIN;
GRANT EXECUTE MANAGED TASK ON ACCOUNT TO ROLE MULEWATCH_ADMIN;
GRANT CREATE INTEGRATION ON ACCOUNT TO ROLE MULEWATCH_ADMIN;
GRANT APPLY MASKING POLICY ON ACCOUNT TO ROLE MULEWATCH_ADMIN;
GRANT APPLY TAG ON ACCOUNT TO ROLE MULEWATCH_ADMIN;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE MULEWATCH_ADMIN;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE FINCRIME_ANALYST;

-- ---------------------------------------------------------------------
-- Database & schemas (medallion-style)
-- ---------------------------------------------------------------------
USE ROLE MULEWATCH_ADMIN;
CREATE DATABASE IF NOT EXISTS MULEWATCH COMMENT = 'MuleWatch FinCrime platform';
USE DATABASE MULEWATCH;

CREATE SCHEMA IF NOT EXISTS RAW       COMMENT = 'Landing: core banking, UPI switch, device telemetry, complaints, documents';
CREATE SCHEMA IF NOT EXISTS CURATED   COMMENT = 'Dynamic tables: enriched transactions and account features';
CREATE SCHEMA IF NOT EXISTS ANALYTICS COMMENT = 'Risk scores, rings, cases, evidence, STR drafts, evaluation';
CREATE SCHEMA IF NOT EXISTS AI        COMMENT = 'AI enrichment outputs, search services, agent';
CREATE SCHEMA IF NOT EXISTS APP       COMMENT = 'Code stage, procedures, Streamlit app';
CREATE SCHEMA IF NOT EXISTS GOV       COMMENT = 'Policies, tags, ground truth (hidden from detection), audit';

CREATE STAGE IF NOT EXISTS APP.CODE_STAGE  DIRECTORY = (ENABLE = TRUE) COMMENT = 'Python modules for stored procedures';
CREATE STAGE IF NOT EXISTS RAW.DOCS_STAGE  DIRECTORY = (ENABLE = TRUE) COMMENT = 'Policy / typology documents';
CREATE STAGE IF NOT EXISTS ANALYTICS.MODEL_STAGE COMMENT = 'Serialized ML models';
-- Reads a whole text file as one value (fallback loader for documents)
CREATE FILE FORMAT IF NOT EXISTS RAW.FF_WHOLE_FILE TYPE = CSV FIELD_DELIMITER = NONE RECORD_DELIMITER = NONE
  ESCAPE_UNENCLOSED_FIELD = NONE FIELD_OPTIONALLY_ENCLOSED_BY = NONE;

-- ---------------------------------------------------------------------
-- Runtime configuration (read by every procedure)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS APP.CONFIG (
  KEY   VARCHAR PRIMARY KEY,
  VALUE VARCHAR,
  DESCRIPTION VARCHAR
);

MERGE INTO APP.CONFIG t USING (
  SELECT * FROM VALUES
    ('LLM_MODELS',        'claude-sonnet-4-6,claude-sonnet-4-5,claude-4-sonnet,openai-gpt-4.1,llama3.3-70b,mistral-large2,llama3.1-70b',
                          'Preference-ordered LLMs; first one that responds in this account is used'),
    ('LLM_MODEL_LIGHT',   'llama3.1-8b',  'Cheap model for bulk enrichment (falls back to main model)'),
    ('ALERT_THRESHOLD',   '55',           'Final risk score (0-100) at/above which an account is alerted'),
    ('RING_SEED_THRESHOLD','35',          'Preliminary score at/above which an account can seed a ring'),
    ('WEIGHT_RULES',      '0.50',         'Blend weight: rules engine'),
    ('WEIGHT_ML',         '0.35',         'Blend weight: ML classifier'),
    ('WEIGHT_GRAPH',      '0.15',         'Blend weight: graph / ring score'),
    ('ACTIVE_LLM',        '',             'Resolved at runtime - do not edit')
  AS v(KEY, VALUE, DESCRIPTION)
) s ON t.KEY = s.KEY
WHEN NOT MATCHED THEN INSERT (KEY, VALUE, DESCRIPTION) VALUES (s.KEY, s.VALUE, s.DESCRIPTION);
