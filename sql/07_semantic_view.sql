-- =====================================================================
-- MuleWatch :: 07_semantic_view.sql
-- Semantic layer for Cortex Analyst (text-to-SQL) with verified queries.
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE WAREHOUSE MULEWATCH_WH;

CREATE OR REPLACE VIEW ANALYTICS.V_COMPLAINTS AS
SELECT f.COMPLAINT_ID, f.REPORTED_TS, TO_DATE(f.REPORTED_TS) AS REPORTED_DATE, f.VICTIM_BANK, f.VICTIM_STATE,
       f.BENEFICIARY_ACCOUNT_ID, f.AMOUNT, COALESCE(l.SCAM_TYPE, 'Unclassified') AS SCAM_TYPE
FROM RAW.FRAUD_COMPLAINTS f
LEFT JOIN AI.COMPLAINT_LABELS l ON l.COMPLAINT_ID = f.COMPLAINT_ID;

CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('MULEWATCH.ANALYTICS', $$
name: FINCRIME_SV
description: >
  Financial-crime intelligence for Arcadia Bank: investigation cases, detected mule rings,
  account risk scores, the transaction ledger and victim cyber-fraud complaints.
  Amounts are in Indian Rupees (INR). 1 lakh = 100,000 and 1 crore = 10,000,000.
tables:
  - name: cases
    description: Investigation cases created by MuleWatch. One case per detected ring or per standalone alerted account.
    base_table: {database: MULEWATCH, schema: ANALYTICS, table: CASES}
    primary_key: {columns: [case_id]}
    dimensions:
      - {name: case_id, expr: CASE_ID, data_type: VARCHAR, description: Case identifier like MW-20261004-00012}
      - {name: case_type, expr: CASE_TYPE, data_type: VARCHAR, description: RING or ACCOUNT}
      - {name: ring_id, expr: RING_ID, data_type: VARCHAR}
      - {name: primary_account_id, expr: PRIMARY_ACCOUNT_ID, data_type: VARCHAR}
      - name: case_region
        expr: REGION
        data_type: VARCHAR
        description: Region of the case (North, South, East, West, Central)
        sample_values: [North, South, East, West, Central]
      - name: case_typology
        expr: TYPOLOGY
        data_type: VARCHAR
        synonyms: [scheme, pattern, crime type]
        sample_values: [MULE_LAYERING, STRUCTURING, ROUND_TRIPPING]
      - name: status
        expr: STATUS
        data_type: VARCHAR
        description: NEW, TRIAGED, INVESTIGATED, PENDING_REVIEW, ESCALATED, CLOSED_STR_FILED, CLOSED_FALSE_POSITIVE. Open = not starting with CLOSED.
        sample_values: [NEW, TRIAGED, PENDING_REVIEW, CLOSED_STR_FILED]
      - {name: priority, expr: PRIORITY, data_type: VARCHAR, description: P1 (urgent) to P3, sample_values: [P1, P2, P3]}
      - {name: assigned_to, expr: ASSIGNED_TO, data_type: VARCHAR}
      - {name: disposition, expr: DISPOSITION, data_type: VARCHAR}
    time_dimensions:
      - {name: case_created_at, expr: CREATED_AT, data_type: TIMESTAMP_NTZ}
      - {name: sla_due_at, expr: SLA_DUE_AT, data_type: TIMESTAMP_NTZ}
      - {name: closed_at, expr: CLOSED_AT, data_type: TIMESTAMP_NTZ}
    facts:
      - {name: amount_at_risk, expr: AMOUNT_AT_RISK, data_type: FLOAT, description: Funds received from outside the ring in the last 30 days (INR), synonyms: [exposure, money at risk]}
      - {name: case_member_count, expr: MEMBER_COUNT, data_type: NUMBER}
      - {name: priority_score, expr: PRIORITY_SCORE, data_type: FLOAT}
      - {name: confidence, expr: CONFIDENCE, data_type: FLOAT, description: Investigator agent confidence 0-1}
    metrics:
      - {name: case_count, expr: COUNT(case_id)}
      - {name: open_case_count, expr: "COUNT_IF(status NOT LIKE 'CLOSED%')"}
      - {name: total_amount_at_risk, expr: SUM(amount_at_risk)}

  - name: rings
    description: Connected groups of suspicious accounts linked by money transfers and shared devices.
    base_table: {database: MULEWATCH, schema: ANALYTICS, table: RINGS}
    primary_key: {columns: [ring_id]}
    dimensions:
      - {name: ring_id, expr: RING_ID, data_type: VARCHAR}
      - {name: ring_typology, expr: TYPOLOGY, data_type: VARCHAR, sample_values: [MULE_LAYERING, STRUCTURING, ROUND_TRIPPING]}
      - {name: ring_region, expr: PRIMARY_REGION, data_type: VARCHAR}
    time_dimensions:
      - {name: ring_last_activity, expr: LAST_ACTIVITY_TS, data_type: TIMESTAMP_NTZ}
    facts:
      - {name: ring_member_count, expr: MEMBER_COUNT, data_type: NUMBER}
      - {name: external_inflow_30d, expr: TOTAL_EXTERNAL_IN_30D, data_type: FLOAT, description: Money entering the ring from outside in 30 days}
      - {name: ring_cash_out_30d, expr: CASH_OUT_30D, data_type: FLOAT, description: ATM / cash withdrawals by ring members in 30 days}
      - {name: ring_crypto_out_30d, expr: VDA_OUT_30D, data_type: FLOAT, description: Transfers to crypto (VDA) exchanges}
      - {name: ring_complaints, expr: COMPLAINT_CNT, data_type: NUMBER}
      - {name: ring_shared_devices, expr: SHARED_DEVICES, data_type: NUMBER}
      - {name: ring_score, expr: RING_SCORE, data_type: FLOAT}
    metrics:
      - {name: ring_count, expr: COUNT(ring_id)}
      - {name: total_ring_cash_out, expr: SUM(ring_cash_out_30d)}

  - name: accounts
    description: Latest risk score per account (rules + ML + graph blend, 0-100).
    base_table: {database: MULEWATCH, schema: ANALYTICS, table: ACCOUNT_RISK}
    primary_key: {columns: [account_id]}
    dimensions:
      - {name: account_id, expr: ACCOUNT_ID, data_type: VARCHAR}
      - {name: account_region, expr: REGION, data_type: VARCHAR}
      - {name: account_type, expr: ACCOUNT_TYPE, data_type: VARCHAR, sample_values: [SAVINGS, CURRENT, WALLET]}
      - {name: occupation, expr: OCCUPATION, data_type: VARCHAR, sample_values: [Student, Homemaker, Gig worker, Salaried, Business owner]}
      - {name: risk_band, expr: RISK_BAND, data_type: VARCHAR, sample_values: [CRITICAL, HIGH, MEDIUM, LOW]}
      - {name: rules_hit, expr: RULES_HIT, data_type: VARCHAR, description: Comma separated rule names that fired}
      - {name: account_ring_id, expr: RING_ID, data_type: VARCHAR}
      - {name: is_alerted, expr: IS_ALERTED, data_type: BOOLEAN}
    facts:
      - {name: final_score, expr: FINAL_SCORE, data_type: FLOAT, synonyms: [risk score]}
      - {name: rule_score, expr: RULE_SCORE, data_type: FLOAT}
      - {name: ml_score, expr: ML_SCORE, data_type: FLOAT}
      - {name: account_age_days, expr: ACCOUNT_AGE_DAYS, data_type: NUMBER}
      - {name: inflow_30d, expr: TOTAL_IN_30D, data_type: FLOAT}
      - {name: outflow_30d, expr: TOTAL_OUT_30D, data_type: FLOAT}
    metrics:
      - {name: alerted_accounts, expr: COUNT_IF(is_alerted)}
      - {name: avg_risk_score, expr: AVG(final_score)}

  - name: transactions
    description: Enriched transaction ledger (UPI, IMPS, NEFT, RTGS, ATM, cash).
    base_table: {database: MULEWATCH, schema: CURATED, table: TXN_ENRICHED}
    primary_key: {columns: [txn_id]}
    dimensions:
      - {name: txn_id, expr: TXN_ID, data_type: VARCHAR}
      - {name: channel, expr: CHANNEL, data_type: VARCHAR, sample_values: [UPI, IMPS, NEFT, RTGS, ATM, CASH_DEPOSIT, CASH_WITHDRAWAL]}
      - {name: direction, expr: DIRECTION, data_type: VARCHAR, sample_values: [INWARD, OUTWARD, INTERNAL_TRANSFER]}
      - {name: txn_region, expr: REGION, data_type: VARCHAR}
      - {name: src_account_id, expr: SRC_ACCOUNT_ID, data_type: VARCHAR}
      - {name: dst_account_id, expr: DST_ACCOUNT_ID, data_type: VARCHAR}
      - {name: geo_city, expr: GEO_CITY, data_type: VARCHAR}
      - {name: near_threshold_cash, expr: NEAR_THRESHOLD_CASH, data_type: BOOLEAN, description: Cash deposit between 45,000 and 49,999}
      - {name: to_crypto_exchange, expr: TO_VDA_EXCHANGE, data_type: BOOLEAN}
    time_dimensions:
      - {name: txn_ts, expr: TXN_TS, data_type: TIMESTAMP_NTZ}
      - {name: txn_date, expr: TXN_DATE, data_type: DATE}
    facts:
      - {name: amount, expr: AMOUNT, data_type: FLOAT}
    metrics:
      - {name: txn_count, expr: COUNT(txn_id)}
      - {name: txn_value, expr: SUM(amount)}

  - name: complaints
    description: Cyber-fraud complaints naming our accounts as beneficiaries, with AI-classified scam type.
    base_table: {database: MULEWATCH, schema: ANALYTICS, table: V_COMPLAINTS}
    primary_key: {columns: [complaint_id]}
    dimensions:
      - {name: complaint_id, expr: COMPLAINT_ID, data_type: VARCHAR}
      - {name: victim_state, expr: VICTIM_STATE, data_type: VARCHAR}
      - {name: victim_bank, expr: VICTIM_BANK, data_type: VARCHAR}
      - {name: beneficiary_account_id, expr: BENEFICIARY_ACCOUNT_ID, data_type: VARCHAR}
      - name: scam_type
        expr: SCAM_TYPE
        data_type: VARCHAR
        sample_values: [Investment / trading app scam, Digital arrest scam, Part-time job / task scam]
    time_dimensions:
      - {name: reported_ts, expr: REPORTED_TS, data_type: TIMESTAMP_NTZ}
      - {name: reported_date, expr: REPORTED_DATE, data_type: DATE}
    facts:
      - {name: complaint_amount, expr: AMOUNT, data_type: FLOAT}
    metrics:
      - {name: complaint_count, expr: COUNT(complaint_id)}
      - {name: complaint_value, expr: SUM(complaint_amount)}

relationships:
  - name: cases_to_rings
    left_table: cases
    right_table: rings
    relationship_columns: [{left_column: ring_id, right_column: ring_id}]
    join_type: left_outer
    relationship_type: many_to_one
  - name: accounts_to_rings
    left_table: accounts
    right_table: rings
    relationship_columns: [{left_column: account_ring_id, right_column: ring_id}]
    join_type: left_outer
    relationship_type: many_to_one
  - name: transactions_to_accounts
    left_table: transactions
    right_table: accounts
    relationship_columns: [{left_column: src_account_id, right_column: account_id}]
    join_type: left_outer
    relationship_type: many_to_one
  - name: complaints_to_accounts
    left_table: complaints
    right_table: accounts
    relationship_columns: [{left_column: beneficiary_account_id, right_column: account_id}]
    join_type: left_outer
    relationship_type: many_to_one

verified_queries:
  - name: open_cases_by_priority
    question: How many open cases do we have by priority?
    use_as_onboarding_question: true
    sql: |
      SELECT priority, COUNT(*) AS open_cases, SUM(amount_at_risk) AS amount_at_risk
      FROM __cases WHERE status NOT LIKE 'CLOSED%' GROUP BY priority ORDER BY priority
  - name: amount_at_risk_by_region
    question: What is the total amount at risk in open cases by region?
    use_as_onboarding_question: true
    sql: |
      SELECT case_region, SUM(amount_at_risk) AS amount_at_risk, COUNT(*) AS open_cases
      FROM __cases WHERE status NOT LIKE 'CLOSED%' GROUP BY case_region ORDER BY amount_at_risk DESC
  - name: top_risky_accounts
    question: Show the 10 riskiest accounts with the rules they triggered
    sql: |
      SELECT account_id, occupation, account_region, final_score, risk_band, rules_hit
      FROM __accounts ORDER BY final_score DESC LIMIT 10
  - name: rings_by_cash_out
    question: Which mule rings withdrew the most cash in the last 30 days?
    use_as_onboarding_question: true
    sql: |
      SELECT ring_id, ring_typology, ring_member_count, ring_cash_out_30d, ring_crypto_out_30d, ring_complaints
      FROM __rings ORDER BY ring_cash_out_30d DESC LIMIT 10
  - name: rings_by_typology
    question: How many rings did we detect per typology and how much money flowed into them?
    sql: |
      SELECT ring_typology, COUNT(*) AS rings, SUM(ring_member_count) AS accounts, SUM(external_inflow_30d) AS inflow
      FROM __rings GROUP BY ring_typology ORDER BY inflow DESC
  - name: complaints_by_scam_type
    question: How many victim complaints did we receive by scam type and total value?
    use_as_onboarding_question: true
    sql: |
      SELECT scam_type, COUNT(*) AS complaints, SUM(complaint_amount) AS total_amount
      FROM __complaints GROUP BY scam_type ORDER BY complaints DESC
  - name: complaints_last_7_days_by_state
    question: Which victim states reported the most complaints in the last 7 days?
    sql: |
      SELECT victim_state, COUNT(*) AS complaints, SUM(complaint_amount) AS amount
      FROM __complaints WHERE reported_ts >= DATEADD('day', -7, CURRENT_TIMESTAMP())
      GROUP BY victim_state ORDER BY complaints DESC
  - name: daily_upi_volume
    question: What was the daily UPI transaction count and value over the last 14 days?
    sql: |
      SELECT txn_date, COUNT(*) AS txn_count, SUM(amount) AS txn_value
      FROM __transactions WHERE channel = 'UPI' AND txn_date >= DATEADD('day', -14, CURRENT_DATE())
      GROUP BY txn_date ORDER BY txn_date
  - name: crypto_outflow
    question: How much money went to crypto exchanges in the last 30 days and from how many accounts?
    sql: |
      SELECT SUM(amount) AS crypto_outflow, COUNT(DISTINCT src_account_id) AS accounts
      FROM __transactions WHERE to_crypto_exchange AND txn_ts >= DATEADD('day', -30, CURRENT_TIMESTAMP())
  - name: structuring_deposits
    question: Which accounts made the most cash deposits just below 50,000 rupees?
    sql: |
      SELECT dst_account_id AS account_id, COUNT(*) AS near_threshold_deposits, SUM(amount) AS total
      FROM __transactions WHERE near_threshold_cash GROUP BY dst_account_id
      HAVING COUNT(*) >= 3 ORDER BY near_threshold_deposits DESC LIMIT 20
  - name: sla_breaches
    question: Which open cases have breached their SLA?
    sql: |
      SELECT case_id, priority, status, assigned_to, sla_due_at, amount_at_risk
      FROM __cases WHERE status NOT LIKE 'CLOSED%' AND sla_due_at < CURRENT_TIMESTAMP()
      ORDER BY sla_due_at
  - name: high_risk_by_occupation
    question: What is the occupation mix of high and critical risk accounts?
    sql: |
      SELECT occupation, COUNT(*) AS accounts, AVG(final_score) AS avg_score
      FROM __accounts WHERE risk_band IN ('HIGH', 'CRITICAL') GROUP BY occupation ORDER BY accounts DESC
  - name: new_accounts_in_rings
    question: How many accounts younger than 90 days are part of detected rings?
    sql: |
      SELECT COUNT(*) AS young_ring_accounts, SUM(inflow_30d) AS inflow
      FROM __accounts WHERE account_ring_id IS NOT NULL AND account_age_days < 90
  - name: case_outcomes
    question: How many cases were closed as STR filed versus false positive?
    sql: |
      SELECT disposition, COUNT(*) AS cases FROM __cases WHERE disposition IS NOT NULL GROUP BY disposition
$$);

-- Try it:  SELECT * FROM SEMANTIC_VIEW(ANALYTICS.FINCRIME_SV METRICS cases.open_case_count DIMENSIONS cases.priority);
SHOW SEMANTIC VIEWS IN SCHEMA ANALYTICS;
