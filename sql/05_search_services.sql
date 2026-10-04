-- =====================================================================
-- MuleWatch :: 05_search_services.sql  - Cortex Search (managed hybrid retrieval)
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE WAREHOUSE MULEWATCH_WH;

-- Policies, SOPs and typologies -> grounds for STR drafting
CREATE OR REPLACE CORTEX SEARCH SERVICE AI.POLICY_SEARCH
  ON CHUNK_TEXT
  ATTRIBUTES DOC_ID, DOC_TYPE, TITLE, SECTION
  WAREHOUSE = MULEWATCH_WH
  TARGET_LAG = '1 day'
  COMMENT = 'AML policy, SOP and typology library'
AS (
  SELECT CHUNK_ID, DOC_ID, DOC_TYPE, TITLE, SECTION, CHUNK_TEXT FROM RAW.DOCS
);

-- News feed -> adverse media questions from the copilot
CREATE OR REPLACE CORTEX SEARCH SERVICE AI.MEDIA_SEARCH
  ON BODY
  ATTRIBUTES SOURCE, CITY, HEADLINE
  WAREHOUSE = MULEWATCH_WH
  TARGET_LAG = '1 day'
  COMMENT = 'Adverse media news feed'
AS (
  SELECT ARTICLE_ID, HEADLINE, BODY, SOURCE, CITY, PUBLISHED_DATE, MENTIONED_NAME FROM RAW.ADVERSE_MEDIA
);

-- Victim complaint narratives -> "show me complaints about digital arrest scams in Bihar"
CREATE OR REPLACE CORTEX SEARCH SERVICE AI.COMPLAINT_SEARCH
  ON COMPLAINT_TEXT
  ATTRIBUTES VICTIM_STATE, BENEFICIARY_ACCOUNT_ID
  WAREHOUSE = MULEWATCH_WH
  TARGET_LAG = '1 hour'
  COMMENT = 'Cyber-fraud complaint narratives'
AS (
  SELECT COMPLAINT_ID, COMPLAINT_TEXT, VICTIM_STATE, BENEFICIARY_ACCOUNT_ID, AMOUNT, REPORTED_TS
  FROM RAW.FRAUD_COMPLAINTS
);

-- smoke test
SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW('MULEWATCH.AI.POLICY_SEARCH',
  '{"query": "rapid pass-through of funds money mule red flags", "columns": ["CHUNK_ID","TITLE"], "limit": 3}')):results;
