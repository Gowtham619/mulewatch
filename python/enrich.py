"""AI enrichment with Cortex AI SQL functions - run only on the alerted population to control cost.

  * AI_COMPLETE (structured output) : KYC officer notes -> stated occupation / income / red flags
  * AI_CLASSIFY                     : news articles -> risk category ; complaints -> scam typology
  * JAROWINKLER_SIMILARITY + AI_FILTER : adverse-media name screening with an LLM to reject namesakes
  * JAROWINKLER_SIMILARITY          : watchlist screening
"""
from __future__ import annotations

import time

from common import DB, audit, light_model, q, resolve_model

MEDIA_LABELS = ["financial crime or money laundering", "online fraud or scam", "violent or street crime",
                "business or community news", "general advisory"]
SCAM_LABELS = ["Investment / trading app scam", "Digital arrest scam", "Part-time job / task scam",
               "Loan app extortion", "Phishing / KYC update scam", "Other"]

KYC_PROMPT = ("You are a KYC analyst at an Indian bank. Extract facts from this KYC officer note. "
              "Use null when a fact is not stated. kyc_risk must be LOW, MEDIUM or HIGH based on red flags "
              "such as third-party prompting, vague purpose, agent referral, inability to explain funds. Note: ")

TARGETS = f"""
  SELECT DISTINCT a.CUSTOMER_ID FROM {DB}.RAW.ACCOUNTS a
  WHERE a.ACCOUNT_ID IN (
      SELECT m.ACCOUNT_ID FROM {DB}.ANALYTICS.RING_MEMBERS m
      JOIN {DB}.ANALYTICS.CASES c ON c.RING_ID = m.RING_ID AND c.STATUS NOT LIKE 'CLOSED%'
      UNION SELECT PRIMARY_ACCOUNT_ID FROM {DB}.ANALYTICS.CASES WHERE STATUS NOT LIKE 'CLOSED%')"""


def _arr(values):
    return "[" + ", ".join(q(v) for v in values) + "]"


def _run(session, sqls: list[str]) -> str:
    """Try SQL variants in order (newest Cortex syntax first); return the label of the one that worked."""
    last = None
    for label, sql in sqls:
        try:
            session.sql(sql).collect()
            return label
        except Exception as e:
            last = e
    raise last


def enrich_sp(session) -> dict:
    """CALL MULEWATCH.APP.ENRICH_ALERTED()"""
    t0 = time.time()
    res = {}
    model = resolve_model(session)
    lm = light_model(session)

    # 1) KYC notes -> structured facts
    kyc_sel = f"""FROM {DB}.RAW.KYC_NOTES n
                 WHERE n.CUSTOMER_ID IN ({TARGETS})
                   AND n.NOTE_ID NOT IN (SELECT NOTE_ID FROM {DB}.AI.KYC_EXTRACT)"""
    res["kyc_mode"] = _run(session, [
        ("response_format", f"""INSERT INTO {DB}.AI.KYC_EXTRACT
            SELECT n.CUSTOMER_ID, n.NOTE_ID,
                   TRY_PARSE_JSON(TO_VARCHAR(AI_COMPLETE(
                       model => {q(lm)},
                       prompt => CONCAT({q(KYC_PROMPT)}, n.NOTE_TEXT),
                       response_format => TYPE OBJECT(stated_occupation STRING, employer STRING,
                           stated_monthly_income_inr NUMBER, source_of_funds STRING,
                           account_purpose STRING, red_flags ARRAY(STRING), kyc_risk STRING)))),
                   {q(lm)}, CURRENT_TIMESTAMP() {kyc_sel}"""),
        ("json_prompt", f"""INSERT INTO {DB}.AI.KYC_EXTRACT
            SELECT n.CUSTOMER_ID, n.NOTE_ID,
                   TRY_PARSE_JSON(REGEXP_SUBSTR(AI_COMPLETE({q(model)}, CONCAT({q(KYC_PROMPT)}, n.NOTE_TEXT,
                     ' Return ONLY JSON with keys stated_occupation, employer, stated_monthly_income_inr, '
                     'source_of_funds, account_purpose, red_flags (array of strings), kyc_risk.')), '\\\\{{.*\\\\}}', 1, 1, 's')),
                   {q(model)}, CURRENT_TIMESTAMP() {kyc_sel}"""),
    ])

    # 2) Adverse media categorisation (all new articles - small table)
    res["media_mode"] = _run(session, [
        ("ai_classify", f"""INSERT INTO {DB}.AI.MEDIA_LABELS
            SELECT ARTICLE_ID, AI_CLASSIFY(CONCAT(HEADLINE, '. ', BODY), {_arr(MEDIA_LABELS)}):labels[0]::STRING,
                   CURRENT_TIMESTAMP()
            FROM {DB}.RAW.ADVERSE_MEDIA WHERE ARTICLE_ID NOT IN (SELECT ARTICLE_ID FROM {DB}.AI.MEDIA_LABELS)"""),
        ("classify_text", f"""INSERT INTO {DB}.AI.MEDIA_LABELS
            SELECT ARTICLE_ID, SNOWFLAKE.CORTEX.CLASSIFY_TEXT(CONCAT(HEADLINE, '. ', BODY), {_arr(MEDIA_LABELS)}):label::STRING,
                   CURRENT_TIMESTAMP()
            FROM {DB}.RAW.ADVERSE_MEDIA WHERE ARTICLE_ID NOT IN (SELECT ARTICLE_ID FROM {DB}.AI.MEDIA_LABELS)"""),
    ])

    # 3) Adverse media screening: fuzzy name match, then LLM rejects namesakes / irrelevant stories
    cand = f"""WITH cand AS (
          SELECT c.CUSTOMER_ID, c.FULL_NAME, c.CITY, m.ARTICLE_ID, m.HEADLINE, m.BODY,
                 JAROWINKLER_SIMILARITY(UPPER(c.FULL_NAME), UPPER(m.MENTIONED_NAME)) AS SIM
          FROM {DB}.RAW.CUSTOMERS c JOIN {DB}.RAW.ADVERSE_MEDIA m ON m.MENTIONED_NAME IS NOT NULL
          WHERE c.CUSTOMER_ID IN ({TARGETS})
            AND JAROWINKLER_SIMILARITY(UPPER(c.FULL_NAME), UPPER(m.MENTIONED_NAME)) >= 92
            AND NOT EXISTS (SELECT 1 FROM {DB}.AI.MEDIA_MATCHES x
                            WHERE x.CUSTOMER_ID = c.CUSTOMER_ID AND x.ARTICLE_ID = m.ARTICLE_ID))"""
    question = ("A bank customer named {0} lives in {1}. News article: {2} "
                "Is this article most likely about this same person (the name matches AND the city is consistent) "
                "AND does it describe involvement in fraud, money laundering or another financial crime?")
    res["screening_mode"] = _run(session, [
        ("ai_filter", f"""INSERT INTO {DB}.AI.MEDIA_MATCHES {cand}
            SELECT cand.CUSTOMER_ID, cand.ARTICLE_ID, cand.SIM,
                   AI_FILTER(PROMPT({q(question)}, cand.FULL_NAME, cand.CITY, cand.BODY)),
                   l.RISK_LABEL, cand.HEADLINE, CURRENT_TIMESTAMP()
            FROM cand LEFT JOIN {DB}.AI.MEDIA_LABELS l ON l.ARTICLE_ID = cand.ARTICLE_ID"""),
        ("ai_complete_yes_no", f"""INSERT INTO {DB}.AI.MEDIA_MATCHES {cand}
            SELECT cand.CUSTOMER_ID, cand.ARTICLE_ID, cand.SIM,
                   CONTAINS(UPPER(AI_COMPLETE({q(model)}, CONCAT('Answer only YES or NO. A bank customer named ',
                       cand.FULL_NAME, ' lives in ', cand.CITY, '. News article: ', cand.BODY,
                       ' Is the article about this same person and does it describe financial crime?'))), 'YES'),
                   l.RISK_LABEL, cand.HEADLINE, CURRENT_TIMESTAMP()
            FROM cand LEFT JOIN {DB}.AI.MEDIA_LABELS l ON l.ARTICLE_ID = cand.ARTICLE_ID"""),
    ])

    # 4) Watchlist screening (deterministic)
    session.sql(f"""INSERT INTO {DB}.AI.WATCHLIST_HITS
        SELECT c.CUSTOMER_ID, w.ENTRY_ID, w.NAME, w.LIST_TYPE,
               JAROWINKLER_SIMILARITY(UPPER(c.FULL_NAME), UPPER(w.NAME)), CURRENT_TIMESTAMP()
        FROM {DB}.RAW.CUSTOMERS c JOIN {DB}.RAW.WATCHLIST w
          ON JAROWINKLER_SIMILARITY(UPPER(c.FULL_NAME), UPPER(w.NAME)) >= 93
        WHERE c.CUSTOMER_ID IN ({TARGETS})
          AND NOT EXISTS (SELECT 1 FROM {DB}.AI.WATCHLIST_HITS h
                          WHERE h.CUSTOMER_ID = c.CUSTOMER_ID AND h.ENTRY_ID = w.ENTRY_ID)""").collect()

    # 5) Victim complaints -> scam typology
    res["complaint_mode"] = _run(session, [
        ("ai_classify", f"""INSERT INTO {DB}.AI.COMPLAINT_LABELS
            SELECT COMPLAINT_ID, AI_CLASSIFY(COMPLAINT_TEXT, {_arr(SCAM_LABELS)}):labels[0]::STRING, CURRENT_TIMESTAMP()
            FROM {DB}.RAW.FRAUD_COMPLAINTS WHERE COMPLAINT_ID NOT IN (SELECT COMPLAINT_ID FROM {DB}.AI.COMPLAINT_LABELS)"""),
        ("classify_text", f"""INSERT INTO {DB}.AI.COMPLAINT_LABELS
            SELECT COMPLAINT_ID, SNOWFLAKE.CORTEX.CLASSIFY_TEXT(COMPLAINT_TEXT, {_arr(SCAM_LABELS)}):label::STRING,
                   CURRENT_TIMESTAMP()
            FROM {DB}.RAW.FRAUD_COMPLAINTS WHERE COMPLAINT_ID NOT IN (SELECT COMPLAINT_ID FROM {DB}.AI.COMPLAINT_LABELS)"""),
    ])
    for t in ("KYC_EXTRACT", "MEDIA_LABELS", "MEDIA_MATCHES", "WATCHLIST_HITS", "COMPLAINT_LABELS"):
        res[t.lower()] = int(session.sql(f"SELECT COUNT(*) FROM {DB}.AI.{t}").collect()[0][0])
    audit(session, "ENRICHMENT_AGENT", "ENRICH_ALERTED", model=lm, details=res, started=t0)
    return res
