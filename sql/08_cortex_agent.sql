-- =====================================================================
-- MuleWatch :: 08_cortex_agent.sql
-- "Ask MuleWatch" - a Cortex Agent that orchestrates:
--   * Cortex Analyst over the FINCRIME_SV semantic view (structured questions)
--   * Cortex Search over policies, adverse media and complaints (unstructured)
--   * Custom tools backed by stored procedures (case brief, run an investigation)
-- =====================================================================
USE ROLE MULEWATCH_ADMIN;
USE DATABASE MULEWATCH;
USE WAREHOUSE MULEWATCH_WH;

CREATE OR REPLACE AGENT AI.MULEWATCH_COPILOT
  COMMENT = 'Financial-crime investigation copilot for Arcadia Bank FCU'
  FROM SPECIFICATION
$$
models:
  orchestration: auto

orchestration:
  budget:
    seconds: 90
    tokens: 32000

instructions:
  system: >
    You are MuleWatch, a financial-crime investigation copilot for the Financial Crime Unit of Arcadia Bank (India).
    Users are AML investigators and the Principal Officer's team. Data is synthetic.
  orchestration: >
    Use FinCrimeAnalyst for any question about counts, amounts, trends, cases, rings, accounts, transactions or
    complaints. Use PolicySearch for questions about policy, regulation, SOPs, red flags, typologies or how to write
    an STR. Use MediaSearch for news / adverse media about a person or city. Use ComplaintSearch to find victim
    complaint narratives. Use CaseBrief when the user mentions a case id (format MW-YYYYMMDD-NNNNN).
    Only use InvestigateCase when the user explicitly asks to investigate a case or draft its STR.
    Combine tools when a question needs both numbers and policy context.
  response: >
    Be concise and factual. Format money in INR using lakh/crore. When you cite policy, include the CHUNK_ID
    (for example POL-02#2). Never reveal or speculate about information not returned by tools.
    Remind users that STR filing requires human approval when relevant.
  sample_questions:
    - question: How many open P1 cases do we have and what is the total amount at risk?
    - question: Which mule rings withdrew the most cash this month?
    - question: What are the red flags for a money mule account according to our policy?
    - question: Summarise case MW-20261004-00001 and tell me what is missing for the STR.
    - question: Show complaints about digital arrest scams.

tools:
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: FinCrimeAnalyst
      description: Text-to-SQL over cases, rings, account risk scores, transactions and victim complaints.
  - tool_spec:
      type: cortex_search
      name: PolicySearch
      description: Bank AML/CFT policies, SOPs (STR writing, lien/freeze) and the typology library.
  - tool_spec:
      type: cortex_search
      name: MediaSearch
      description: Adverse media news articles mentioning people, businesses and cities.
  - tool_spec:
      type: cortex_search
      name: ComplaintSearch
      description: Narratives of cyber-fraud complaints received against our beneficiary accounts.
  - tool_spec:
      type: generic
      name: CaseBrief
      description: Returns a JSON brief of one investigation case (status, priority, AI findings, latest STR draft).
      input_schema:
        type: object
        properties:
          case_id:
            type: string
            description: Case identifier, e.g. MW-20261004-00001
        required: [case_id]
  - tool_spec:
      type: generic
      name: InvestigateCase
      description: Runs the investigator and compliance agents on one case and returns the updated brief. Slow (about 1 minute).
      input_schema:
        type: object
        properties:
          case_id:
            type: string
            description: Case identifier
        required: [case_id]

tool_resources:
  FinCrimeAnalyst:
    semantic_view: MULEWATCH.ANALYTICS.FINCRIME_SV
    execution_environment:
      type: warehouse
      warehouse: MULEWATCH_WH
  PolicySearch:
    search_service: MULEWATCH.AI.POLICY_SEARCH
    max_results: 5
    id_column: CHUNK_ID
    title_column: TITLE
  MediaSearch:
    search_service: MULEWATCH.AI.MEDIA_SEARCH
    max_results: 5
    id_column: ARTICLE_ID
    title_column: HEADLINE
  ComplaintSearch:
    search_service: MULEWATCH.AI.COMPLAINT_SEARCH
    max_results: 8
    id_column: COMPLAINT_ID
  CaseBrief:
    type: procedure
    identifier: MULEWATCH.APP.GET_CASE_BRIEF
    execution_environment:
      type: warehouse
      warehouse: MULEWATCH_WH
  InvestigateCase:
    type: procedure
    identifier: MULEWATCH.APP.INVESTIGATE_CASE_TOOL
    execution_environment:
      type: warehouse
      warehouse: MULEWATCH_WH
$$;

GRANT USAGE ON AGENT AI.MULEWATCH_COPILOT TO ROLE FINCRIME_ANALYST;

-- Smoke test (non-streaming SQL interface)
SELECT TRY_PARSE_JSON(SNOWFLAKE.CORTEX.DATA_AGENT_RUN('MULEWATCH.AI.MULEWATCH_COPILOT',
  $${"messages":[{"role":"user","content":[{"type":"text","text":"How many open cases do we have by priority?"}]}]}$$
)) AS RESPONSE;
