-- =====================================================================
-- MuleWatch :: 02_procedures.sql
-- Python stored procedures. Code is uploaded to @APP.CODE_STAGE by deploy.sh:
--   snow stage copy python/*.py @MULEWATCH.APP.CODE_STAGE --overwrite
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE SCHEMA APP;
USE WAREHOUSE MULEWATCH_WH;

CREATE OR REPLACE PROCEDURE APP.GENERATE_SYNTHETIC_DATA(N_CUSTOMERS INT DEFAULT 20000, N_RINGS INT DEFAULT 30, SEED INT DEFAULT 42)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'datagen.generate_sp'
  COMMENT = 'Builds the synthetic bank (customers, ledger, telemetry, documents, ground truth)'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.SIMULATE_STREAM(N_TXN INT DEFAULT 2000, RING_PROBABILITY FLOAT DEFAULT 0.25)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'datagen.simulate_stream_sp'
  COMMENT = 'Appends live traffic; RING_PROBABILITY=1 injects a new mule ring'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.LOAD_DOCS()
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'pipeline.load_docs_sp'
  COMMENT = 'Chunks policy/SOP/typology markdown from RAW.DOCS_STAGE into RAW.DOCS'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.SCORE_ACCOUNTS()
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'scoring.score_accounts_sp'
  COMMENT = 'Rules engine + ML classifier'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.TRAIN_MULE_MODEL()
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow','snowflake-ml-python')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'scoring.train_model_sp'
  COMMENT = 'Trains & registers the mule classifier from investigation outcomes'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.DETECT_RINGS_AND_CASES()
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'rings.detect_rings_sp'
  COMMENT = 'Graph ring detection, final blended score, case creation'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.ENRICH_ALERTED()
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'enrich.enrich_sp'
  COMMENT = 'Cortex AI enrichment of the alerted population'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.TRIAGE_AGENT(LIMIT_N INT DEFAULT 200)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.triage_sp'
  COMMENT = 'Prioritises NEW cases'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.INVESTIGATOR_AGENT(CASE_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.investigate_sp'
  COMMENT = 'Builds the evidence pack and hypothesis for one case'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.COMPLIANCE_AGENT(CASE_ID VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.compliance_sp'
  COMMENT = 'Drafts and self-checks an STR with policy citations'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.RUN_INVESTIGATIONS(TOP_N INT DEFAULT 3)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.run_investigations_sp'
  COMMENT = 'Investigator + compliance on the top triaged cases'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.RECORD_DISPOSITION(CASE_ID VARCHAR, DISPOSITION VARCHAR, REVIEWER VARCHAR, NOTES VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.record_disposition_sp'
  COMMENT = 'Human decision -> closure + training labels'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.TUNE_RULES(MIN_HITS INT DEFAULT 5)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.tune_rules_sp'
  COMMENT = 'Re-weights rules from analyst outcomes'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.EVALUATE_DETECTION()
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'evaluate.evaluate_sp'
  COMMENT = 'Precision/recall vs held-out ground truth'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.RUN_FULL_PIPELINE(INVESTIGATE_TOP_N INT DEFAULT 3)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'pipeline.run_full_pipeline_sp'
  COMMENT = 'End-to-end pipeline'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.GET_CASE_BRIEF(CASE_ID VARCHAR)
  RETURNS VARCHAR
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.case_brief_sp'
  COMMENT = 'Agent tool: compact JSON brief of a case'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.INVESTIGATE_CASE_TOOL(CASE_ID VARCHAR)
  RETURNS VARCHAR
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.investigate_tool_sp'
  COMMENT = 'Agent tool: run investigator + compliance on a case'
  EXECUTE AS OWNER;

CREATE OR REPLACE PROCEDURE APP.ASK_FALLBACK(QUESTION VARCHAR)
  RETURNS VARCHAR
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python','pandas','numpy','networkx','scikit-learn','joblib','pyarrow')
  IMPORTS = ('@MULEWATCH.APP.CODE_STAGE/common.py',
          '@MULEWATCH.APP.CODE_STAGE/datagen.py',
          '@MULEWATCH.APP.CODE_STAGE/scoring.py',
          '@MULEWATCH.APP.CODE_STAGE/rings.py',
          '@MULEWATCH.APP.CODE_STAGE/evaluate.py',
          '@MULEWATCH.APP.CODE_STAGE/enrich.py',
          '@MULEWATCH.APP.CODE_STAGE/agents.py',
          '@MULEWATCH.APP.CODE_STAGE/pipeline.py')
  HANDLER = 'agents.ask_fallback'
  COMMENT = 'Fallback question answering when the Cortex Agent is unavailable'
  EXECUTE AS OWNER;
