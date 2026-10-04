-- =====================================================================
-- MuleWatch :: 04_seed.sql  - build the synthetic bank and load documents
-- Expect ~3-6 minutes on an X-Small warehouse for the default size.
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE WAREHOUSE MULEWATCH_WH;

-- 20k customers (~23k accounts, ~0.9M transactions), 30 planted crime rings
CALL APP.GENERATE_SYNTHETIC_DATA(20000, 30, 42);

-- Policies / SOPs / typologies were uploaded by deploy.sh to @RAW.DOCS_STAGE
CALL APP.LOAD_DOCS();

-- Materialise the feature pipeline (refresh cascades to upstream dynamic tables)
ALTER DYNAMIC TABLE CURATED.ACCOUNT_FEATURES REFRESH;
ALTER DYNAMIC TABLE CURATED.DAILY_CHANNEL_STATS REFRESH;

SELECT 'accounts' AS t, COUNT(*) FROM RAW.ACCOUNTS
UNION ALL SELECT 'transactions', COUNT(*) FROM RAW.TRANSACTIONS
UNION ALL SELECT 'features', COUNT(*) FROM CURATED.ACCOUNT_FEATURES
UNION ALL SELECT 'doc chunks', COUNT(*) FROM RAW.DOCS;
