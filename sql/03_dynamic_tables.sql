-- =====================================================================
-- MuleWatch :: 03_dynamic_tables.sql
-- Declarative, self-refreshing feature pipeline (Dynamic Tables).
--   RAW (append-only) --> TXN_ENRICHED --> ACCOUNT_FEATURES (+ device, payment, cycle features)
-- Windows are anchored to the latest transaction time (deterministic, replayable).
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE WAREHOUSE MULEWATCH_WH;

-- 1) Transactions joined to both account masters, with row-level risk flags
CREATE OR REPLACE DYNAMIC TABLE CURATED.TXN_ENRICHED
  TARGET_LAG = 'DOWNSTREAM'
  WAREHOUSE = MULEWATCH_WH
  COMMENT = 'Ledger enriched with internal/external flags and row-level red flags'
AS
SELECT
  t.TXN_ID,
  t.TXN_TS,
  TO_DATE(t.TXN_TS)                                     AS TXN_DATE,
  HOUR(t.TXN_TS)                                        AS TXN_HOUR,
  t.CHANNEL,
  t.AMOUNT,
  t.SRC_ACCOUNT_ID,
  t.DST_ACCOUNT_ID,
  t.REMARKS,
  t.GEO_CITY,
  t.DEVICE_ID,
  sa.ACCOUNT_ID IS NOT NULL                             AS SRC_INTERNAL,
  da.ACCOUNT_ID IS NOT NULL                             AS DST_INTERNAL,
  COALESCE(sa.REGION, da.REGION)                        AS REGION,
  (t.CHANNEL = 'CASH_DEPOSIT' AND t.AMOUNT >= 45000 AND t.AMOUNT < 50000) AS NEAR_THRESHOLD_CASH,
  (t.CHANNEL IN ('ATM', 'CASH_WITHDRAWAL'))             AS IS_CASH_OUT,
  (t.DST_ACCOUNT_ID LIKE 'EXT-VDA%')                    AS TO_VDA_EXCHANGE,
  CASE
    WHEN sa.ACCOUNT_ID IS NOT NULL AND da.ACCOUNT_ID IS NOT NULL THEN 'INTERNAL_TRANSFER'
    WHEN sa.ACCOUNT_ID IS NOT NULL THEN 'OUTWARD'
    ELSE 'INWARD'
  END                                                   AS DIRECTION
FROM RAW.TRANSACTIONS t
LEFT JOIN RAW.ACCOUNTS sa ON sa.ACCOUNT_ID = t.SRC_ACCOUNT_ID
LEFT JOIN RAW.ACCOUNTS da ON da.ACCOUNT_ID = t.DST_ACCOUNT_ID;

-- 2) Device telemetry: flatten the JSON
CREATE OR REPLACE DYNAMIC TABLE CURATED.DEVICE_EVENTS_FLAT
  TARGET_LAG = 'DOWNSTREAM'
  WAREHOUSE = MULEWATCH_WH
  COMMENT = 'Flattened device / network telemetry'
AS
SELECT
  EVENT:event_id::STRING          AS EVENT_ID,
  EVENT:account_id::STRING        AS ACCOUNT_ID,
  EVENT:event_type::STRING        AS EVENT_TYPE,
  EVENT:ts::TIMESTAMP_NTZ         AS EVENT_TS,
  EVENT:device.id::STRING         AS DEVICE_ID,
  EVENT:device.os::STRING         AS DEVICE_OS,
  EVENT:device.rooted::BOOLEAN    AS DEVICE_ROOTED,
  EVENT:network.ip::STRING        AS IP_ADDRESS,
  EVENT:network.geo.city::STRING  AS GEO_CITY
FROM RAW.DEVICE_EVENTS;

-- 3) Device sharing per account (mule herders run many accounts from few phones)
CREATE OR REPLACE DYNAMIC TABLE CURATED.DEVICE_SHARING
  TARGET_LAG = 'DOWNSTREAM'
  WAREHOUSE = MULEWATCH_WH
AS
WITH d AS (SELECT DISTINCT DEVICE_ID, ACCOUNT_ID FROM CURATED.DEVICE_EVENTS_FLAT),
     c AS (SELECT DEVICE_ID, COUNT(DISTINCT ACCOUNT_ID) AS N_ACC FROM d GROUP BY DEVICE_ID)
SELECT d.ACCOUNT_ID,
       MAX(c.N_ACC)                AS MAX_ACCOUNTS_ON_DEVICE,
       COUNT_IF(c.N_ACC > 1)       AS SHARED_DEVICE_COUNT
FROM d JOIN c ON c.DEVICE_ID = d.DEVICE_ID
GROUP BY d.ACCOUNT_ID;

-- 4) Outward payment messages (semi-structured) flattened
CREATE OR REPLACE DYNAMIC TABLE CURATED.PAYMENT_MSG_FLAT
  TARGET_LAG = 'DOWNSTREAM'
  WAREHOUSE = MULEWATCH_WH
AS
SELECT
  MSG:msg_id::STRING                  AS MSG_ID,
  MSG:txn_id::STRING                  AS TXN_ID,
  MSG:msg_type::STRING                AS MSG_TYPE,
  MSG:value_ts::TIMESTAMP_NTZ         AS VALUE_TS,
  MSG:amount.value::NUMBER(14,2)      AS AMOUNT,
  MSG:remitter.account::STRING        AS REMITTER_ACCOUNT,
  MSG:beneficiary.account::STRING     AS BENEFICIARY_ACCOUNT,
  MSG:beneficiary.bank::STRING        AS BENEFICIARY_BANK,
  MSG:purpose_code::STRING            AS PURPOSE_CODE,
  MSG:remittance_info::STRING         AS REMITTANCE_INFO,
  LOWER(MSG:remittance_info::STRING) IN ('personal', 'help', 'loan return', 'family support', 'gift', 'family')
                                      AS IS_VAGUE_PURPOSE
FROM RAW.PAYMENT_MESSAGES;

-- 5) Circular flows: large funds that come back to origin in 2 or 3 hops (round-tripping)
CREATE OR REPLACE DYNAMIC TABLE CURATED.CIRCULAR_FLOWS
  TARGET_LAG = 'DOWNSTREAM'
  WAREHOUSE = MULEWATCH_WH
  REFRESH_MODE = FULL
AS
WITH anchor AS (SELECT MAX(TXN_TS) AS AS_OF FROM CURATED.TXN_ENRICHED),
e AS (
  SELECT t.SRC_ACCOUNT_ID AS S, t.DST_ACCOUNT_ID AS D, SUM(t.AMOUNT) AS AMT
  FROM CURATED.TXN_ENRICHED t, anchor
  WHERE t.SRC_INTERNAL AND t.DST_INTERNAL AND t.AMOUNT >= 100000
    AND t.TXN_TS >= DATEADD('day', -60, anchor.AS_OF)
    AND t.SRC_ACCOUNT_ID <> t.DST_ACCOUNT_ID
  GROUP BY 1, 2
),
cyc AS (
  SELECT e1.S AS A, e1.D AS B, e2.D AS C, LEAST(e1.AMT, e2.AMT, e3.AMT) AS MIN_LEG, 3 AS HOPS
  FROM e e1 JOIN e e2 ON e1.D = e2.S JOIN e e3 ON e2.D = e3.S AND e3.D = e1.S
  WHERE e2.D <> e1.S
  UNION ALL
  SELECT e1.S, e1.D, NULL, LEAST(e1.AMT, e2.AMT), 2
  FROM e e1 JOIN e e2 ON e1.D = e2.S AND e2.D = e1.S
)
SELECT ACCOUNT_ID, MAX(MIN_LEG) AS MAX_CYCLE_AMOUNT, MIN(HOPS) AS MIN_HOPS, COUNT(*) AS CYCLE_COUNT
FROM (
  SELECT A AS ACCOUNT_ID, MIN_LEG, HOPS FROM cyc
  UNION ALL SELECT B, MIN_LEG, HOPS FROM cyc
  UNION ALL SELECT C, MIN_LEG, HOPS FROM cyc WHERE C IS NOT NULL
) x
GROUP BY ACCOUNT_ID;

-- 6) The feature store: one row per account, 30-day behavioural window
CREATE OR REPLACE DYNAMIC TABLE CURATED.ACCOUNT_FEATURES
  TARGET_LAG = '15 minutes'
  WAREHOUSE = MULEWATCH_WH
  REFRESH_MODE = FULL
  COMMENT = 'Behavioural features per account over the trailing 30 days'
AS
WITH anchor AS (SELECT MAX(TXN_TS) AS AS_OF FROM CURATED.TXN_ENRICHED),
w AS (
  SELECT t.* FROM CURATED.TXN_ENRICHED t, anchor
  WHERE t.TXN_TS > DATEADD('day', -30, anchor.AS_OF)
),
legs AS (
  SELECT DST_ACCOUNT_ID AS ACCOUNT_ID, TXN_TS, TXN_DATE, TXN_HOUR, AMOUNT AS IN_AMT, 0 AS OUT_AMT,
         SRC_ACCOUNT_ID AS CP, 'IN' AS DIR, FALSE AS IS_CASH_OUT, NEAR_THRESHOLD_CASH, FALSE AS TO_VDA
  FROM w WHERE DST_INTERNAL
  UNION ALL
  SELECT SRC_ACCOUNT_ID, TXN_TS, TXN_DATE, TXN_HOUR, 0, AMOUNT,
         DST_ACCOUNT_ID, 'OUT', IS_CASH_OUT, FALSE, TO_VDA_EXCHANGE
  FROM w WHERE SRC_INTERNAL
),
daily AS (
  SELECT ACCOUNT_ID, TXN_DATE, SUM(IN_AMT) AS IN_D, SUM(OUT_AMT) AS OUT_D
  FROM legs GROUP BY ACCOUNT_ID, TXN_DATE
),
pt AS (
  SELECT ACCOUNT_ID, SUM(LEAST(IN_D, OUT_D)) AS MATCHED, SUM(IN_D) AS TIN
  FROM daily GROUP BY ACCOUNT_ID
),
agg AS (
  SELECT ACCOUNT_ID,
    SUM(IN_AMT)                                       AS TOTAL_IN_30D,
    SUM(OUT_AMT)                                      AS TOTAL_OUT_30D,
    COUNT_IF(DIR = 'IN')                              AS N_IN_30D,
    COUNT_IF(DIR = 'OUT')                             AS N_OUT_30D,
    COUNT(DISTINCT IFF(DIR = 'IN', CP, NULL))         AS DISTINCT_IN_CP_30D,
    COUNT(DISTINCT IFF(DIR = 'OUT', CP, NULL))        AS DISTINCT_OUT_CP_30D,
    SUM(IFF(IS_CASH_OUT, OUT_AMT, 0))                 AS CASH_OUT_30D,
    COUNT_IF(NEAR_THRESHOLD_CASH)                     AS NEAR_THRESHOLD_CASH_CNT_30D,
    SUM(IFF(TO_VDA, OUT_AMT, 0))                      AS VDA_OUT_30D,
    COUNT_IF(TXN_HOUR < 5)                            AS NIGHT_TXN_CNT_30D,
    COUNT(*)                                          AS N_TXN_30D,
    MAX(IN_AMT)                                       AS MAX_SINGLE_CREDIT_30D,
    MAX(TXN_TS)                                       AS LAST_TXN_TS
  FROM legs GROUP BY ACCOUNT_ID
),
vague AS (
  SELECT p.REMITTER_ACCOUNT AS ACCOUNT_ID, COUNT_IF(p.IS_VAGUE_PURPOSE) AS VAGUE_PURPOSE_CNT_30D
  FROM CURATED.PAYMENT_MSG_FLAT p, anchor
  WHERE p.VALUE_TS > DATEADD('day', -30, anchor.AS_OF)
  GROUP BY p.REMITTER_ACCOUNT
),
cmp AS (
  SELECT BENEFICIARY_ACCOUNT_ID AS ACCOUNT_ID, COUNT(*) AS COMPLAINT_CNT, SUM(AMOUNT) AS COMPLAINT_AMT
  FROM RAW.FRAUD_COMPLAINTS GROUP BY BENEFICIARY_ACCOUNT_ID
)
SELECT
  a.ACCOUNT_ID,
  a.CUSTOMER_ID,
  a.ACCOUNT_TYPE,
  a.REGION,
  a.CITY,
  a.BRANCH_ID,
  c.OCCUPATION_DECLARED,
  c.ANNUAL_INCOME_DECLARED,
  c.ONBOARD_CHANNEL,
  DATEDIFF('day', a.OPEN_DATE, TO_DATE(anchor.AS_OF))                      AS ACCOUNT_AGE_DAYS,
  COALESCE(g.TOTAL_IN_30D, 0)                                             AS TOTAL_IN_30D,
  COALESCE(g.TOTAL_OUT_30D, 0)                                            AS TOTAL_OUT_30D,
  COALESCE(g.N_IN_30D, 0)                                                 AS N_IN_30D,
  COALESCE(g.N_OUT_30D, 0)                                                AS N_OUT_30D,
  COALESCE(g.DISTINCT_IN_CP_30D, 0)                                       AS DISTINCT_IN_CP_30D,
  COALESCE(g.DISTINCT_OUT_CP_30D, 0)                                      AS DISTINCT_OUT_CP_30D,
  COALESCE(g.CASH_OUT_30D, 0)                                             AS CASH_OUT_30D,
  COALESCE(g.NEAR_THRESHOLD_CASH_CNT_30D, 0)                              AS NEAR_THRESHOLD_CASH_CNT_30D,
  COALESCE(g.VDA_OUT_30D, 0)                                              AS VDA_OUT_30D,
  COALESCE(g.MAX_SINGLE_CREDIT_30D, 0)                                    AS MAX_SINGLE_CREDIT_30D,
  COALESCE(g.N_TXN_30D, 0)                                                AS N_TXN_30D,
  IFF(COALESCE(pt.TIN, 0) > 0, pt.MATCHED / pt.TIN, 0)                    AS SAME_DAY_PASS_THROUGH_RATIO,
  IFF(COALESCE(g.TOTAL_OUT_30D, 0) > 0, g.CASH_OUT_30D / g.TOTAL_OUT_30D, 0) AS CASH_OUT_RATIO,
  IFF(COALESCE(g.N_TXN_30D, 0) > 0, g.NIGHT_TXN_CNT_30D / g.N_TXN_30D, 0)  AS NIGHT_TXN_RATIO,
  COALESCE(g.TOTAL_IN_30D, 0) * 12 / GREATEST(COALESCE(c.ANNUAL_INCOME_DECLARED, 0), 60000) AS INCOME_MULTIPLE,
  IFF(DATEDIFF('day', a.OPEN_DATE, TO_DATE(anchor.AS_OF)) <= 90 AND COALESCE(g.TOTAL_IN_30D, 0) >= 200000, 1, 0)
                                                                          AS NEW_ACCT_HIGH_VALUE,
  GREATEST(COALESCE(ds.MAX_ACCOUNTS_ON_DEVICE, 1) - 1, 0)                 AS SHARED_DEVICE_PEERS,
  COALESCE(cmp.COMPLAINT_CNT, 0)                                          AS COMPLAINT_CNT,
  COALESCE(cmp.COMPLAINT_AMT, 0)                                          AS COMPLAINT_AMT,
  IFF(cf.ACCOUNT_ID IS NOT NULL, 1, 0)                                    AS IN_CIRCULAR_FLOW,
  COALESCE(cf.MAX_CYCLE_AMOUNT, 0)                                        AS MAX_CYCLE_AMOUNT,
  COALESCE(v.VAGUE_PURPOSE_CNT_30D, 0)                                    AS VAGUE_PURPOSE_CNT_30D,
  g.LAST_TXN_TS,
  anchor.AS_OF                                                              AS FEATURES_AS_OF
FROM RAW.ACCOUNTS a
CROSS JOIN anchor
JOIN RAW.CUSTOMERS c            ON c.CUSTOMER_ID = a.CUSTOMER_ID
LEFT JOIN agg g                 ON g.ACCOUNT_ID = a.ACCOUNT_ID
LEFT JOIN pt                    ON pt.ACCOUNT_ID = a.ACCOUNT_ID
LEFT JOIN CURATED.DEVICE_SHARING ds ON ds.ACCOUNT_ID = a.ACCOUNT_ID
LEFT JOIN cmp                   ON cmp.ACCOUNT_ID = a.ACCOUNT_ID
LEFT JOIN CURATED.CIRCULAR_FLOWS cf ON cf.ACCOUNT_ID = a.ACCOUNT_ID
LEFT JOIN vague v               ON v.ACCOUNT_ID = a.ACCOUNT_ID;

-- Daily trend table for dashboards & Cortex Analyst
CREATE OR REPLACE DYNAMIC TABLE CURATED.DAILY_CHANNEL_STATS
  TARGET_LAG = '15 minutes'
  WAREHOUSE = MULEWATCH_WH
AS
SELECT TXN_DATE, CHANNEL, REGION, DIRECTION,
       COUNT(*) AS TXN_COUNT, SUM(AMOUNT) AS TOTAL_AMOUNT
FROM CURATED.TXN_ENRICHED
GROUP BY TXN_DATE, CHANNEL, REGION, DIRECTION;
