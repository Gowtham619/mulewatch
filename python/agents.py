"""The three MuleWatch agents + human-in-the-loop feedback.

  TRIAGE_AGENT        : prioritises NEW cases (deterministic score + LLM rationale), sets SLA & owner
  INVESTIGATOR_AGENT  : assembles a structured evidence pack from 8 sources, LLM writes the hypothesis
  COMPLIANCE_AGENT    : retrieves policy (Cortex Search), drafts an STR with citations, self-checks it
  RECORD_DISPOSITION  : analyst decision -> case closure + training labels (closes the learning loop)
  TUNE_RULES          : re-weights rules from analyst outcomes (precision per rule)

Every step writes to GOV.AGENT_AUDIT_LOG. LLM output is advisory: a human approves every STR.
"""
from __future__ import annotations

import json
import re
import time

import numpy as np
import pandas as pd

from common import (DB, audit, complete, complete_json, dollar, inr, parse_json_loose, q,
                    resolve_model, to_pandas)

PRIORITY_SLA_HOURS = {"P1": 24, "P2": 72, "P3": 168}


def _j(obj) -> str:
    return json.dumps(obj, default=str)


# =============================================================================== TRIAGE
def triage_priority(score, amount, complaints, watch, media, recent) -> float:
    s = (0.35 * float(score or 0) + 25 * min(float(amount or 0) / 1_000_000, 1.0) + 6 * min(int(complaints or 0), 4)
         + 10 * (int(watch or 0) > 0) + 10 * (int(media or 0) > 0) + 8 * bool(recent))
    return round(min(s, 100.0), 1)


def triage_sp(session, limit: int = 200) -> dict:
    """CALL MULEWATCH.APP.TRIAGE_AGENT(200)"""
    t0 = time.time()
    model = resolve_model(session)
    df = to_pandas(session, f"""
      WITH mem AS (
        SELECT c.CASE_ID, COALESCE(m.ACCOUNT_ID, c.PRIMARY_ACCOUNT_ID) AS ACCOUNT_ID
        FROM {DB}.ANALYTICS.CASES c LEFT JOIN {DB}.ANALYTICS.RING_MEMBERS m ON m.RING_ID = c.RING_ID
        WHERE c.STATUS = 'NEW'),
      agg AS (
        SELECT mem.CASE_ID, MAX(r.FINAL_SCORE) AS MAX_SCORE, SUM(f.COMPLAINT_CNT) AS COMPLAINTS,
               COUNT(DISTINCT w.ENTRY_ID) AS WATCH_HITS,
               COUNT(DISTINCT IFF(mm.AI_CONFIRMED, mm.ARTICLE_ID, NULL)) AS MEDIA_HITS,
               MAX(f.LAST_TXN_TS) AS LAST_TXN, MAX(f.FEATURES_AS_OF) AS AS_OF,
               LISTAGG(DISTINCT r.RULES_HIT, ',') AS RULES
        FROM mem
        JOIN {DB}.ANALYTICS.ACCOUNT_RISK r ON r.ACCOUNT_ID = mem.ACCOUNT_ID
        JOIN {DB}.CURATED.ACCOUNT_FEATURES f ON f.ACCOUNT_ID = mem.ACCOUNT_ID
        LEFT JOIN {DB}.AI.WATCHLIST_HITS w ON w.CUSTOMER_ID = r.CUSTOMER_ID
        LEFT JOIN {DB}.AI.MEDIA_MATCHES mm ON mm.CUSTOMER_ID = r.CUSTOMER_ID
        GROUP BY mem.CASE_ID)
      SELECT c.CASE_ID, c.CASE_TYPE, c.TYPOLOGY, c.REGION, c.AMOUNT_AT_RISK, c.MEMBER_COUNT, a.*
      FROM {DB}.ANALYTICS.CASES c JOIN agg a ON a.CASE_ID = c.CASE_ID
      WHERE c.STATUS = 'NEW' ORDER BY c.AMOUNT_AT_RISK DESC LIMIT {int(limit)}""")
    if df.empty:
        return {"triaged": 0}
    df = df.loc[:, ~df.columns.duplicated()]
    df["RECENT"] = (pd.to_datetime(df.AS_OF) - pd.to_datetime(df.LAST_TXN)) < pd.Timedelta(hours=48)
    df["PRIORITY_SCORE"] = [triage_priority(*x) for x in df[["MAX_SCORE", "AMOUNT_AT_RISK", "COMPLAINTS", "WATCH_HITS",
                                                             "MEDIA_HITS", "RECENT"]].itertuples(index=False)]
    df["PRIORITY"] = np.select([df.PRIORITY_SCORE >= 70, df.PRIORITY_SCORE >= 50], ["P1", "P2"], "P3")
    df["FACTS"] = df.apply(lambda r: (
        f"Case {r.CASE_ID}: {r.CASE_TYPE} case, typology {r.TYPOLOGY}, {int(r.MEMBER_COUNT)} account(s) in {r.REGION}. "
        f"Funds at risk {inr(r.AMOUNT_AT_RISK)} in 30 days. Max risk score {r.MAX_SCORE:.0f}/100. "
        f"Victim complaints: {int(r.COMPLAINTS or 0)}. Watchlist hits: {int(r.WATCH_HITS)}. "
        f"Confirmed adverse media: {int(r.MEDIA_HITS)}. Activity in last 48h: {'yes' if r.RECENT else 'no'}. "
        f"Rules fired: {', '.join(sorted(set(filter(None, str(r.RULES or '').split(',')))))}. "
        f"Assigned priority {r.PRIORITY}."), axis=1)
    session.sql(f"CREATE OR REPLACE TRANSIENT TABLE {DB}.ANALYTICS.TMP_TRIAGE (CASE_ID VARCHAR, FACTS VARCHAR, "
                f"PRIORITY VARCHAR, PRIORITY_SCORE FLOAT, REGION VARCHAR)").collect()
    from common import write_df
    write_df(session, df[["CASE_ID", "FACTS", "PRIORITY", "PRIORITY_SCORE", "REGION"]], f"{DB}.ANALYTICS.TMP_TRIAGE")
    prompt = ("You are the triage agent in a bank financial-crime unit. In at most 3 short sentences, explain to an "
              "investigator why this case has the stated priority and what to look at first. Be factual, use only "
              "the facts given, no preamble. Facts: ")
    session.sql(f"""UPDATE {DB}.ANALYTICS.CASES c SET
          PRIORITY = t.PRIORITY, PRIORITY_SCORE = t.PRIORITY_SCORE, STATUS = 'TRIAGED',
          TRIAGE_RATIONALE = AI_COMPLETE({q(model)}, CONCAT({q(prompt)}, t.FACTS)),
          ASSIGNED_TO = 'fincrime.' || LOWER(t.REGION) || '@arcadia.example',
          SLA_DUE_AT = DATEADD('hour', DECODE(t.PRIORITY, 'P1', 24, 'P2', 72, 168), CURRENT_TIMESTAMP()),
          UPDATED_AT = CURRENT_TIMESTAMP()
        FROM {DB}.ANALYTICS.TMP_TRIAGE t WHERE c.CASE_ID = t.CASE_ID""").collect()
    res = {"triaged": int(len(df)), "by_priority": df.PRIORITY.value_counts().to_dict()}
    audit(session, "TRIAGE_AGENT", "TRIAGE", model=model, details=res, started=t0)
    return res


# =============================================================================== INVESTIGATOR
def hold_time_minutes(legs: pd.DataFrame) -> float | None:
    """Median minutes between a credit and the next debit on the same account (money 'dwell' time)."""
    if legs.empty:
        return None
    legs = legs.sort_values("TXN_TS")
    ins = legs[legs.DIR == "IN"][["ACCOUNT_ID", "TXN_TS"]]
    outs = legs[legs.DIR == "OUT"][["ACCOUNT_ID", "TXN_TS"]].rename(columns={"TXN_TS": "OUT_TS"})
    if ins.empty or outs.empty:
        return None
    m = pd.merge_asof(ins.sort_values("TXN_TS"), outs.sort_values("OUT_TS"), left_on="TXN_TS", right_on="OUT_TS",
                      by="ACCOUNT_ID", direction="forward")
    d = (m.OUT_TS - m.TXN_TS).dt.total_seconds().dropna() / 60
    return round(float(d.median()), 1) if len(d) else None


def gather_evidence(session, case_id: str) -> dict:
    case = to_pandas(session, f"SELECT * FROM {DB}.ANALYTICS.CASES WHERE CASE_ID = {q(case_id)}")
    if case.empty:
        raise ValueError(f"Unknown case {case_id}")
    c = case.iloc[0]
    if c.RING_ID:
        mem_sql = f"SELECT ACCOUNT_ID, ROLE FROM {DB}.ANALYTICS.RING_MEMBERS WHERE RING_ID = {q(c.RING_ID)}"
    else:
        mem_sql = f"SELECT {q(c.PRIMARY_ACCOUNT_ID)} AS ACCOUNT_ID, 'SUBJECT' AS ROLE"
    session.sql(f"CREATE OR REPLACE TRANSIENT TABLE {DB}.ANALYTICS.TMP_MEMBERS AS {mem_sql}").collect()
    M = f"{DB}.ANALYTICS.TMP_MEMBERS"
    members = to_pandas(session, f"""
        SELECT m.ACCOUNT_ID, m.ROLE, cu.FULL_NAME, cu.CITY, cu.OCCUPATION_DECLARED, cu.ANNUAL_INCOME_DECLARED,
               cu.ONBOARD_CHANNEL, a.OPEN_DATE, f.ACCOUNT_AGE_DAYS, f.TOTAL_IN_30D, f.TOTAL_OUT_30D,
               f.DISTINCT_IN_CP_30D, f.SAME_DAY_PASS_THROUGH_RATIO, f.CASH_OUT_RATIO, f.INCOME_MULTIPLE,
               f.SHARED_DEVICE_PEERS, f.COMPLAINT_CNT, r.FINAL_SCORE, r.RULE_SCORE, r.ML_SCORE, r.RULES_HIT,
               cu.CUSTOMER_ID
        FROM {M} m JOIN {DB}.RAW.ACCOUNTS a ON a.ACCOUNT_ID = m.ACCOUNT_ID
        JOIN {DB}.RAW.CUSTOMERS cu ON cu.CUSTOMER_ID = a.CUSTOMER_ID
        JOIN {DB}.CURATED.ACCOUNT_FEATURES f ON f.ACCOUNT_ID = m.ACCOUNT_ID
        JOIN {DB}.ANALYTICS.ACCOUNT_RISK r ON r.ACCOUNT_ID = m.ACCOUNT_ID
        ORDER BY r.FINAL_SCORE DESC""")
    legs = to_pandas(session, f"""
        WITH anchor AS (SELECT MAX(TXN_TS) AS AS_OF FROM {DB}.CURATED.TXN_ENRICHED)
        SELECT DST_ACCOUNT_ID AS ACCOUNT_ID, 'IN' AS DIR, TXN_TS, AMOUNT, CHANNEL, SRC_ACCOUNT_ID AS CP
        FROM {DB}.CURATED.TXN_ENRICHED, anchor WHERE DST_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {M})
          AND TXN_TS > DATEADD('day', -30, AS_OF)
        UNION ALL
        SELECT SRC_ACCOUNT_ID, 'OUT', TXN_TS, AMOUNT, CHANNEL, DST_ACCOUNT_ID
        FROM {DB}.CURATED.TXN_ENRICHED, anchor WHERE SRC_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {M})
          AND TXN_TS > DATEADD('day', -30, AS_OF)""")
    legs["TXN_TS"] = pd.to_datetime(legs.TXN_TS)
    member_set = set(members.ACCOUNT_ID)
    ext_in = legs[(legs.DIR == "IN") & ~legs.CP.isin(member_set)]
    big_in = ext_in[ext_in.CHANNEL.isin(["UPI", "IMPS", "NEFT", "RTGS"])]
    cash = legs[(legs.DIR == "OUT") & legs.CHANNEL.isin(["ATM", "CASH_WITHDRAWAL"])]
    vda = legs[(legs.DIR == "OUT") & legs.CP.astype(str).str.startswith("EXT-VDA")]
    internal = legs[(legs.DIR == "OUT") & legs.CP.isin(member_set)]
    flow_edges = (internal.groupby(["ACCOUNT_ID", "CP"]).AMOUNT.agg(["sum", "count"]).reset_index()
                  .sort_values("sum", ascending=False).head(15))
    devices = to_pandas(session, f"""
        SELECT DEVICE_ID, COUNT(DISTINCT ACCOUNT_ID) AS ACCOUNTS, ARRAY_AGG(DISTINCT ACCOUNT_ID) AS ACCOUNT_IDS,
               MAX(IFF(DEVICE_ROOTED, 1, 0)) AS ROOTED, COUNT(DISTINCT IP_ADDRESS) AS IPS
        FROM {DB}.CURATED.DEVICE_EVENTS_FLAT WHERE ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {M})
        GROUP BY DEVICE_ID HAVING COUNT(DISTINCT ACCOUNT_ID) > 1""")
    complaints = to_pandas(session, f"""
        SELECT f.COMPLAINT_ID, f.REPORTED_TS, f.BENEFICIARY_ACCOUNT_ID, f.AMOUNT, f.VICTIM_STATE,
               COALESCE(l.SCAM_TYPE, 'Unclassified') AS SCAM_TYPE, LEFT(f.COMPLAINT_TEXT, 300) AS EXCERPT
        FROM {DB}.RAW.FRAUD_COMPLAINTS f LEFT JOIN {DB}.AI.COMPLAINT_LABELS l ON l.COMPLAINT_ID = f.COMPLAINT_ID
        WHERE f.BENEFICIARY_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {M}) ORDER BY f.REPORTED_TS""")
    kyc = to_pandas(session, f"""
        SELECT k.CUSTOMER_ID, k.EXTRACTED, n.NOTE_TEXT FROM {DB}.RAW.KYC_NOTES n
        LEFT JOIN {DB}.AI.KYC_EXTRACT k ON k.NOTE_ID = n.NOTE_ID
        WHERE n.CUSTOMER_ID IN (SELECT a.CUSTOMER_ID FROM {DB}.RAW.ACCOUNTS a WHERE a.ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {M}))""")
    media = to_pandas(session, f"""
        SELECT mm.CUSTOMER_ID, mm.ARTICLE_ID, mm.HEADLINE, mm.RISK_LABEL, mm.NAME_SIMILARITY, mm.AI_CONFIRMED
        FROM {DB}.AI.MEDIA_MATCHES mm
        WHERE mm.CUSTOMER_ID IN (SELECT a.CUSTOMER_ID FROM {DB}.RAW.ACCOUNTS a WHERE a.ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {M}))""")
    watch = to_pandas(session, f"""
        SELECT CUSTOMER_ID, WATCHLIST_NAME, LIST_TYPE, NAME_SIMILARITY FROM {DB}.AI.WATCHLIST_HITS
        WHERE CUSTOMER_ID IN (SELECT a.CUSTOMER_ID FROM {DB}.RAW.ACCOUNTS a WHERE a.ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {M}))""")

    kyc_mismatch = []
    for m in members.itertuples(index=False):
        declared_m = float(m.ANNUAL_INCOME_DECLARED or 0) / 12
        if m.TOTAL_IN_30D and m.TOTAL_IN_30D > 3 * max(declared_m, 5000):
            kyc_mismatch.append(dict(account=m.ACCOUNT_ID, occupation=m.OCCUPATION_DECLARED,
                                     declared_monthly_income=round(declared_m), observed_30d_inflow=round(m.TOTAL_IN_30D),
                                     multiple=round(m.TOTAL_IN_30D / max(declared_m, 5000), 1)))
    kyc_records = []
    for k in kyc.itertuples(index=False):
        ex = parse_json_loose(k.EXTRACTED) if k.EXTRACTED else {}
        kyc_records.append(dict(customer=k.CUSTOMER_ID, kyc_risk=ex.get("kyc_risk"), red_flags=ex.get("red_flags"),
                                stated_purpose=ex.get("account_purpose"), note_excerpt=str(k.NOTE_TEXT)[:240]))
    first_ts = legs.TXN_TS.min() if len(legs) else None
    ev = {
        "case": dict(case_id=c.CASE_ID, case_type=c.CASE_TYPE, ring_id=c.RING_ID, typology=c.TYPOLOGY,
                     region=c.REGION, amount_at_risk=float(c.AMOUNT_AT_RISK or 0), priority=c.PRIORITY,
                     triage_rationale=c.TRIAGE_RATIONALE),
        "members": [dict(account=m.ACCOUNT_ID, role=m.ROLE, name=m.FULL_NAME, city=m.CITY,
                         occupation=m.OCCUPATION_DECLARED, onboarding=m.ONBOARD_CHANNEL,
                         account_age_days=int(m.ACCOUNT_AGE_DAYS), in_30d=round(m.TOTAL_IN_30D),
                         out_30d=round(m.TOTAL_OUT_30D), distinct_remitters=int(m.DISTINCT_IN_CP_30D),
                         same_day_pass_through=round(float(m.SAME_DAY_PASS_THROUGH_RATIO), 2),
                         risk_score=round(float(m.FINAL_SCORE), 1), rules=m.RULES_HIT)
                    for m in members.itertuples(index=False)],
        "money_trail": dict(
            external_credits=int(len(big_in)), distinct_external_remitters=int(big_in.CP.nunique()),
            external_credit_total=round(float(big_in.AMOUNT.sum())),
            internal_layering_total=round(float(internal.AMOUNT.sum())),
            cash_withdrawn=round(float(cash.AMOUNT.sum())), atm_withdrawals=int(len(cash)),
            crypto_exchange_outflow=round(float(vda.AMOUNT.sum())),
            median_dwell_minutes=hold_time_minutes(legs),
            first_activity=str(first_ts) if first_ts is not None else None,
            last_activity=str(legs.TXN_TS.max()) if len(legs) else None,
            top_internal_flows=[dict(src=r.ACCOUNT_ID, dst=r.CP, amount=round(r["sum"]), txns=int(r["count"]))
                                for _, r in flow_edges.iterrows()]),
        "devices": [dict(device=d.DEVICE_ID, accounts=int(d.ACCOUNTS), rooted=bool(d.ROOTED), distinct_ips=int(d.IPS))
                    for d in devices.itertuples(index=False)],
        "complaints": dict(count=int(len(complaints)), total=round(float(complaints.AMOUNT.sum())) if len(complaints) else 0,
                           scam_types=complaints.SCAM_TYPE.value_counts().to_dict() if len(complaints) else {},
                           victim_states=sorted(complaints.VICTIM_STATE.unique().tolist()) if len(complaints) else [],
                           samples=complaints.head(3)[["COMPLAINT_ID", "AMOUNT", "SCAM_TYPE", "EXCERPT"]]
                           .to_dict("records") if len(complaints) else []),
        "kyc": dict(income_mismatches=kyc_mismatch, notes=kyc_records[:8]),
        "adverse_media": media[media.AI_CONFIRMED.astype(bool)].to_dict("records") if len(media) else [],
        "media_namesakes_rejected": int((~media.AI_CONFIRMED.astype(bool)).sum()) if len(media) else 0,
        "watchlist": watch.to_dict("records") if len(watch) else [],
    }
    return json.loads(_j(ev))


INVESTIGATOR_PROMPT = """You are a senior financial-crime investigator at an Indian bank (Arcadia Bank).
Analyse the evidence pack below (JSON, all amounts in INR) and return a JSON object with keys:
  headline (one sentence), typology (MULE_LAYERING | STRUCTURING | ROUND_TRIPPING | OTHER),
  hypothesis (3-5 sentences describing the suspected scheme and each role),
  key_findings (array of 4-8 short bullet strings, each citing a concrete number from the evidence),
  exculpatory_factors (array - facts that could indicate legitimate activity),
  confidence (number 0-1), recommended_actions (array; consider: debit freeze / lien on balances per complaints,
  STR filing, enhanced due diligence, re-KYC, device blocking, LEA coordination via the 1930 helpline/NCRP),
  information_gaps (array).
Only use facts present in the evidence. Do not invent names, amounts or dates.

EVIDENCE PACK:
"""


def investigate_sp(session, case_id: str) -> dict:
    """CALL MULEWATCH.APP.INVESTIGATOR_AGENT('MW-...')"""
    t0 = time.time()
    model = resolve_model(session)
    ev = gather_evidence(session, case_id)
    session.sql(f"DELETE FROM {DB}.ANALYTICS.CASE_EVIDENCE WHERE CASE_ID = {q(case_id)}").collect()
    titles = {"members": "Accounts & roles", "money_trail": "Money trail", "devices": "Shared devices",
              "complaints": "Victim complaints", "kyc": "KYC vs behaviour", "adverse_media": "Adverse media",
              "watchlist": "Watchlist screening"}
    for k, title in titles.items():
        session.sql(f"""INSERT INTO {DB}.ANALYTICS.CASE_EVIDENCE
            SELECT {q(case_id)}, {q(k.upper())}, {q(title)}, PARSE_JSON({dollar(_j(ev[k]))}),
                   'INVESTIGATOR_AGENT', CURRENT_TIMESTAMP()""").collect()
    pack = _j(ev)
    if len(pack) > 24000:  # keep prompt bounded
        ev["members"] = ev["members"][:15]
        pack = _j(ev)[:24000]
    summary = complete_json(session, INVESTIGATOR_PROMPT + pack, model=model, max_tokens=4000)
    conf = summary.get("confidence")
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = None
    session.sql(f"""UPDATE {DB}.ANALYTICS.CASES SET INVESTIGATION_SUMMARY = PARSE_JSON({dollar(_j(summary))}),
                    CONFIDENCE = {q(conf)}, STATUS = 'INVESTIGATED', UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE CASE_ID = {q(case_id)}""").collect()
    audit(session, "INVESTIGATOR_AGENT", "INVESTIGATE", case_id=case_id, model=model,
          details={"members": len(ev["members"]), "complaints": ev["complaints"]["count"], "confidence": conf},
          started=t0)
    return {"case_id": case_id, "summary": summary}


# =============================================================================== COMPLIANCE / STR
def retrieve_policy(session, query: str, limit: int = 6) -> list[dict]:
    """Cortex Search over policies/SOPs/typologies, with a keyword fallback."""
    body = json.dumps({"query": query[:1000], "columns": ["CHUNK_ID", "DOC_ID", "TITLE", "SECTION", "CHUNK_TEXT"],
                       "limit": limit})
    try:
        raw = session.sql(f"SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW('{DB}.AI.POLICY_SEARCH', {dollar(body)})").collect()[0][0]
        res = json.loads(raw).get("results", [])
        if res:
            return [{k.upper(): v for k, v in r.items()} for r in res]
    except Exception:
        pass
    words = [w for w in re.findall(r"[a-zA-Z]{5,}", query.lower())][:12]
    score = " + ".join(f"IFF(CONTAINS(LOWER(CHUNK_TEXT), {q(w)}), 1, 0)" for w in words) or "0"
    df = to_pandas(session, f"""SELECT CHUNK_ID, DOC_ID, TITLE, SECTION, CHUNK_TEXT FROM {DB}.RAW.DOCS
                               ORDER BY {score} DESC LIMIT {int(limit)}""")
    return df.to_dict("records")


STR_PROMPT = """You are the compliance agent of Arcadia Bank's Principal Officer team. Draft a Suspicious Transaction
Report (STR) for internal approval before filing with the Financial Intelligence Unit.
Use ONLY facts from the EVIDENCE and the INVESTIGATION. Use the POLICY EXCERPTS to justify grounds of suspicion and
cite them by their exact CHUNK_ID in square brackets, e.g. [POL-02#3]. Amounts in INR with Indian digit grouping.
Return a JSON object with keys:
  subject_summary (string: accounts, roles, KYC profile),
  summary_of_suspicion (string, 2-3 sentences),
  transaction_analysis (string: chronology, amounts, channels, dwell time, cash-out),
  grounds_of_suspicion (array of objects {indicator, evidence, policy_ref}),
  actions_taken_or_recommended (array of strings),
  narrative (string, 250-400 words, covering WHO, WHAT, WHEN, WHERE, WHY suspicious, HOW funds moved),
  citations (array of CHUNK_IDs used).
"""


def _numeric_grounding(text: str, evidence_text: str) -> float:
    """Share of large numbers in the narrative that literally appear in the evidence (anti-hallucination check)."""
    nums = re.findall(r"\d[\d,]{3,}", text or "")
    if not nums:
        return 1.0
    ev = evidence_text.replace(",", "")
    ok = sum(1 for n in nums if n.replace(",", "") in ev)
    return round(ok / len(nums), 3)


def compliance_sp(session, case_id: str) -> dict:
    """CALL MULEWATCH.APP.COMPLIANCE_AGENT('MW-...')"""
    t0 = time.time()
    model = resolve_model(session)
    case = to_pandas(session, f"SELECT * FROM {DB}.ANALYTICS.CASES WHERE CASE_ID = {q(case_id)}")
    if case.empty:
        raise ValueError(f"Unknown case {case_id}")
    c = case.iloc[0]
    if c.INVESTIGATION_SUMMARY is None:
        investigate_sp(session, case_id)
        c = to_pandas(session, f"SELECT * FROM {DB}.ANALYTICS.CASES WHERE CASE_ID = {q(case_id)}").iloc[0]
    summary = parse_json_loose(c.INVESTIGATION_SUMMARY)
    ev_rows = to_pandas(session, f"SELECT EVIDENCE_TYPE, PAYLOAD FROM {DB}.ANALYTICS.CASE_EVIDENCE WHERE CASE_ID = {q(case_id)}")
    evidence = {r.EVIDENCE_TYPE.lower(): parse_json_loose(r.PAYLOAD) if str(r.PAYLOAD).strip().startswith("{")
                else json.loads(r.PAYLOAD) for r in ev_rows.itertuples(index=False)}
    findings = " ".join(summary.get("key_findings", [])[:4]) if isinstance(summary.get("key_findings"), list) else ""
    chunks = retrieve_policy(session, f"{c.TYPOLOGY} money mule indicators grounds of suspicion. {findings}", 5)
    chunks += [x for x in retrieve_policy(session, "STR narrative structure who what when where why how filing timeline", 3)
               if x["CHUNK_ID"] not in {y["CHUNK_ID"] for y in chunks}]
    pol = "\n\n".join(f"[{x['CHUNK_ID']}] {x['TITLE']} - {x['SECTION']}\n{str(x['CHUNK_TEXT'])[:1200]}" for x in chunks)
    ev_text = _j(evidence)[:18000]
    prompt = (STR_PROMPT + "\nEVIDENCE:\n" + ev_text + "\n\nINVESTIGATION:\n" + _j(summary)[:5000]
              + "\n\nPOLICY EXCERPTS:\n" + pol)
    draft = complete_json(session, prompt, model=model, max_tokens=6000)
    allowed = {x["CHUNK_ID"] for x in chunks}
    cited = set(draft.get("citations") or []) | set(re.findall(r"\[([A-Z]{3}-\d{2}#\d+)\]", _j(draft)))
    for g in draft.get("grounds_of_suspicion") or []:
        if isinstance(g, dict) and g.get("policy_ref"):
            cited |= set(re.findall(r"[A-Z]{3}-\d{2}#\d+", str(g["policy_ref"])))
    validity = round(len(cited & allowed) / len(cited), 3) if cited else 0.0
    grounding = _numeric_grounding(draft.get("narrative", "") + " " + draft.get("transaction_analysis", ""), ev_text)
    judge = complete_json(session, (
        "You are a strict QA reviewer of Suspicious Transaction Reports. Compare the DRAFT with the EVIDENCE. "
        "Return JSON {faithfulness: integer 1-5 (5 = every claim supported), completeness: integer 1-5, "
        "issues: array of strings}.\nEVIDENCE:\n" + ev_text[:9000] + "\nDRAFT:\n" + _j(draft)[:7000]),
        model=model, max_tokens=600)
    try:
        faith = float(judge.get("faithfulness"))
    except (TypeError, ValueError):
        faith = None
    narrative = draft.get("narrative") or draft.get("raw") or ""
    version = int(session.sql(f"SELECT COALESCE(MAX(VERSION), 0) + 1 FROM {DB}.ANALYTICS.STR_DRAFTS "
                              f"WHERE CASE_ID = {q(case_id)}").collect()[0][0])
    notes = f"numeric_grounding={grounding}; completeness={judge.get('completeness')}; issues={judge.get('issues')}"
    session.sql(f"""INSERT INTO {DB}.ANALYTICS.STR_DRAFTS (CASE_ID, VERSION, STATUS, DRAFT, NARRATIVE, CITATIONS,
            CITATION_VALIDITY, FAITHFULNESS_SCORE, JUDGE_NOTES, MODEL, CREATED_AT)
        SELECT {q(case_id)}, {version}, 'DRAFT', PARSE_JSON({dollar(_j(draft))}), {dollar(narrative)},
               PARSE_JSON({dollar(_j(sorted(cited)))}), {validity}, {q(faith)}, {dollar(notes)}, {q(model)},
               CURRENT_TIMESTAMP()""").collect()
    session.sql(f"""UPDATE {DB}.ANALYTICS.CASES SET STATUS = 'PENDING_REVIEW', UPDATED_AT = CURRENT_TIMESTAMP()
                    WHERE CASE_ID = {q(case_id)} AND STATUS NOT LIKE 'CLOSED%'""").collect()
    res = {"case_id": case_id, "version": version, "citation_validity": validity, "numeric_grounding": grounding,
           "faithfulness": faith, "policy_chunks": sorted(allowed)}
    audit(session, "COMPLIANCE_AGENT", "DRAFT_STR", case_id=case_id, model=model, details=res, started=t0)
    return res


def run_investigations_sp(session, top_n: int = 3) -> dict:
    """CALL MULEWATCH.APP.RUN_INVESTIGATIONS(3) - investigator + compliance on the highest-priority triaged cases."""
    ids = [r[0] for r in session.sql(f"""SELECT CASE_ID FROM {DB}.ANALYTICS.CASES WHERE STATUS = 'TRIAGED'
                                         ORDER BY PRIORITY_SCORE DESC NULLS LAST, AMOUNT_AT_RISK DESC
                                         LIMIT {int(top_n)}""").collect()]
    out = []
    for cid in ids:
        try:
            investigate_sp(session, cid)
            out.append(compliance_sp(session, cid))
        except Exception as e:  # one bad case must not stop the batch
            audit(session, "ORCHESTRATOR", "INVESTIGATE_FAILED", case_id=cid, status="ERROR", details={"error": str(e)[:500]})
            out.append({"case_id": cid, "error": str(e)[:300]})
    return {"processed": out}


# =============================================================================== HUMAN-IN-THE-LOOP
DISPOSITIONS = {"STR_FILED": ("CLOSED_STR_FILED", 1, "APPROVED"),
                "ESCALATED_LEA": ("ESCALATED", 1, "APPROVED"),
                "FALSE_POSITIVE": ("CLOSED_FALSE_POSITIVE", 0, "REJECTED"),
                "NEEDS_MORE_INFO": ("INVESTIGATED", None, "RETURNED")}


def record_disposition_sp(session, case_id: str, disposition: str, reviewer: str, notes: str) -> dict:
    """CALL MULEWATCH.APP.RECORD_DISPOSITION('MW-..', 'STR_FILED', 'analyst@bank', 'notes')"""
    t0 = time.time()
    disposition = disposition.upper()
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {list(DISPOSITIONS)}")
    status, label, str_status = DISPOSITIONS[disposition]
    closed = "CURRENT_TIMESTAMP()" if status.startswith("CLOSED") else "NULL"
    session.sql(f"""INSERT INTO {DB}.ANALYTICS.CASE_FEEDBACK VALUES ({q(case_id)}, {q(disposition)}, {q(reviewer)},
                    {dollar(notes or '')}, CURRENT_TIMESTAMP())""").collect()
    session.sql(f"""UPDATE {DB}.ANALYTICS.CASES SET STATUS = {q(status)}, DISPOSITION = {q(disposition)},
                    CLOSED_AT = {closed}, UPDATED_AT = CURRENT_TIMESTAMP() WHERE CASE_ID = {q(case_id)}""").collect()
    session.sql(f"""UPDATE {DB}.ANALYTICS.STR_DRAFTS SET STATUS = {q(str_status)}, REVIEWED_BY = {q(reviewer)},
                    REVIEWED_AT = CURRENT_TIMESTAMP()
                    WHERE CASE_ID = {q(case_id)} AND VERSION = (SELECT MAX(VERSION) FROM {DB}.ANALYTICS.STR_DRAFTS
                                                               WHERE CASE_ID = {q(case_id)})""").collect()
    n = 0
    if label is not None:
        session.sql(f"""INSERT INTO {DB}.ANALYTICS.FEEDBACK_LABELS
            SELECT x.ACCOUNT_ID, {label}, {q(case_id)}, CURRENT_TIMESTAMP() FROM (
              SELECT m.ACCOUNT_ID FROM {DB}.ANALYTICS.CASES c JOIN {DB}.ANALYTICS.RING_MEMBERS m ON m.RING_ID = c.RING_ID
              WHERE c.CASE_ID = {q(case_id)}
              UNION SELECT PRIMARY_ACCOUNT_ID FROM {DB}.ANALYTICS.CASES WHERE CASE_ID = {q(case_id)}) x""").collect()
        n = int(session.sql(f"SELECT COUNT(*) FROM {DB}.ANALYTICS.FEEDBACK_LABELS WHERE CASE_ID = {q(case_id)}").collect()[0][0])
    res = {"case_id": case_id, "status": status, "labels_written": n}
    audit(session, "HUMAN_REVIEWER", f"DISPOSITION_{disposition}", case_id=case_id, details=res | {"reviewer": reviewer},
          started=t0)
    return res


def tune_weights(hits: pd.DataFrame, cfg: pd.DataFrame, min_hits: int = 5) -> pd.DataFrame:
    """Pure: hits(ACCOUNT_ID, LABEL, RULES_HIT) -> proposed new weights with precision per rule."""
    rows = []
    for rule in cfg.itertuples(index=False):
        mask = hits.RULES_HIT.fillna("").str.split(",").apply(lambda xs: rule.RULE_NAME in xs)
        n = int(mask.sum())
        if n < min_hits:
            continue
        prec = float(hits[mask].LABEL.mean())
        new = float(np.clip(rule.BASE_WEIGHT * (0.5 + prec), 0.25 * rule.BASE_WEIGHT, 2.0 * rule.BASE_WEIGHT))
        rows.append(dict(RULE_ID=rule.RULE_ID, OLD_WEIGHT=float(rule.WEIGHT), NEW_WEIGHT=round(new, 3),
                         PRECISION=round(prec, 3), N_HITS=n))
    return pd.DataFrame(rows)


def tune_rules_sp(session, min_hits: int = 5) -> dict:
    """CALL MULEWATCH.APP.TUNE_RULES(5)"""
    t0 = time.time()
    hits = to_pandas(session, f"""SELECT l.ACCOUNT_ID, l.LABEL, r.RULES_HIT FROM {DB}.ANALYTICS.FEEDBACK_LABELS l
                                  JOIN {DB}.ANALYTICS.ACCOUNT_RISK r ON r.ACCOUNT_ID = l.ACCOUNT_ID""")
    cfg = to_pandas(session, f"SELECT * FROM {DB}.ANALYTICS.RULE_CONFIG WHERE ENABLED")
    if hits.empty:
        return {"changed": 0, "reason": "no analyst feedback yet"}
    ch = tune_weights(hits, cfg, int(min_hits))
    for r in ch.itertuples(index=False):
        if abs(r.NEW_WEIGHT - r.OLD_WEIGHT) < 0.01:
            continue
        session.sql(f"""INSERT INTO {DB}.ANALYTICS.RULE_CONFIG_HISTORY VALUES (CURRENT_TIMESTAMP(), {q(r.RULE_ID)},
                        {r.OLD_WEIGHT}, {r.NEW_WEIGHT}, {r.PRECISION}, {r.N_HITS}, 'analyst feedback precision')""").collect()
        session.sql(f"""UPDATE {DB}.ANALYTICS.RULE_CONFIG SET WEIGHT = {r.NEW_WEIGHT}, UPDATED_AT = CURRENT_TIMESTAMP()
                        WHERE RULE_ID = {q(r.RULE_ID)}""").collect()
    res = {"evaluated_rules": int(len(ch)), "changes": ch.to_dict("records")}
    audit(session, "LEARNING_LOOP", "TUNE_RULES", details=res, started=t0)
    return res


# =============================================================================== AGENT TOOLS
def case_brief_sp(session, case_id: str) -> str:
    """Custom tool for the Cortex Agent: compact JSON brief of a case."""
    c = to_pandas(session, f"""SELECT CASE_ID, CASE_TYPE, RING_ID, PRIMARY_ACCOUNT_ID, REGION, TYPOLOGY, STATUS,
                                   PRIORITY, AMOUNT_AT_RISK, MEMBER_COUNT, TRIAGE_RATIONALE, INVESTIGATION_SUMMARY,
                                   CONFIDENCE, SLA_DUE_AT FROM {DB}.ANALYTICS.CASES WHERE CASE_ID = {q(case_id.strip())}""")
    if c.empty:
        return _j({"error": f"case {case_id} not found"})
    out = {k.lower(): v for k, v in c.iloc[0].to_dict().items()}
    out["investigation_summary"] = parse_json_loose(out.get("investigation_summary"))
    s = to_pandas(session, f"""SELECT VERSION, STATUS, NARRATIVE, CITATION_VALIDITY, FAITHFULNESS_SCORE
                               FROM {DB}.ANALYTICS.STR_DRAFTS WHERE CASE_ID = {q(case_id.strip())}
                               ORDER BY VERSION DESC LIMIT 1""")
    out["latest_str"] = s.iloc[0].to_dict() if len(s) else None
    return _j(out)


def investigate_tool_sp(session, case_id: str) -> str:
    """Custom tool for the Cortex Agent: run investigator + compliance agents for one case."""
    investigate_sp(session, case_id.strip())
    compliance_sp(session, case_id.strip())
    return case_brief_sp(session, case_id)


def ask_fallback(session, question: str) -> str:
    """Used by the app if the Cortex Agent object is unavailable: answers from KPI context."""
    ctx = to_pandas(session, f"""SELECT STATUS, PRIORITY, COUNT(*) N, SUM(AMOUNT_AT_RISK) AMT
                                 FROM {DB}.ANALYTICS.CASES GROUP BY 1, 2""").to_dict("records")
    return complete(session, f"You are MuleWatch, a financial-crime copilot. Case statistics: {_j(ctx)}. "
                             f"Answer briefly: {question}")
