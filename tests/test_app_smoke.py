"""Renders every page of the Streamlit app against a DuckDB-backed fake Snowpark session.

The fake session transpiles each Snowflake SQL statement to DuckDB with sqlglot, so this also
checks that the app's SQL is well-formed against the real table shapes produced by the pipeline.
"""
from __future__ import annotations

import json
import os
import sys
import types

import duckdb
import pandas as pd
import sqlglot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "python"))

import offline_pipeline as op  # noqa: E402

FAILED_SQL: list[tuple[str, str]] = []


class _Res:
    def __init__(self, df):
        self.df = df

    def to_pandas(self):
        return self.df

    def collect(self):
        return [tuple(r) for r in self.df.itertuples(index=False)]


class FakeSession:
    def __init__(self, con):
        self.con = con

    def sql(self, q, params=None):
        s = q.strip()
        if s.upper().startswith("CALL"):
            return _Res(pd.DataFrame({"R": [json.dumps({"status": "ok", "injected_ring": "GT-LIVE-TEST"})]}))
        if "DATA_AGENT_RUN" in s:
            return _Res(pd.DataFrame({"R": [json.dumps({"content": [{"type": "text", "text": "There are 3 open P1 cases."},
                                                                     {"type": "tool_result", "content": [{"json": {"sql": "SELECT 1"}}]}]})]}))
        if params:
            for p in params:
                s = s.replace("?", "'" + str(p).replace("'", "''") + "'", 1)
        try:
            dq = sqlglot.transpile(s, read="snowflake", write="duckdb")[0]
            return _Res(self.con.execute(dq).df())
        except Exception as e:
            FAILED_SQL.append((s[:200], str(e)[:200]))
            raise


def build_db():
    o = op.run(6000, 16, 5, verbose=False)
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS MULEWATCH")
    for sch in ("RAW", "CURATED", "ANALYTICS", "AI", "GOV", "APP"):
        con.execute(f"CREATE SCHEMA MULEWATCH.{sch}")
    risk = o["risk"].copy()
    risk["CUSTOMER_ID"] = "C1"
    risk["ACCOUNT_TYPE"] = "SAVINGS"
    risk["OCCUPATION"] = "Student"
    risk["ACCOUNT_AGE_DAYS"] = 40
    risk["RISK_BAND"] = "HIGH"
    risk["RULE_COUNT"] = 2
    risk["RING_ID"] = risk.ACCOUNT_ID.map(o["members"].set_index("ACCOUNT_ID").RING_ID)
    risk["SCORED_AT"] = pd.Timestamp.now()
    feats = o["features"]
    rings = o["rings"].copy()
    now = pd.Timestamp.now()
    cases = pd.DataFrame([dict(CASE_ID=f"MW-20261004-{i + 1:05d}", CASE_TYPE="RING", RING_ID=r.RING_ID,
                               PRIMARY_ACCOUNT_ID=o["members"][o["members"].RING_ID == r.RING_ID].ACCOUNT_ID.iloc[0],
                               REGION=r.PRIMARY_REGION, TYPOLOGY=r.TYPOLOGY, STATUS=["NEW", "TRIAGED", "PENDING_REVIEW"][i % 3],
                               PRIORITY=["P1", "P2", "P3"][i % 3], PRIORITY_SCORE=90 - i, AMOUNT_AT_RISK=r.TOTAL_EXTERNAL_IN_30D,
                               MEMBER_COUNT=r.MEMBER_COUNT, ASSIGNED_TO="fcu@x", TRIAGE_RATIONALE="Large inflow, complaints.",
                               INVESTIGATION_SUMMARY=json.dumps({"headline": "Mule ring", "key_findings": ["a", "b"],
                                                                 "hypothesis": "h", "recommended_actions": ["STR"]}) if i == 0 else None,
                               CONFIDENCE=0.9 if i == 0 else None, CREATED_AT=now, UPDATED_AT=now, SLA_DUE_AT=now,
                               CLOSED_AT=None, DISPOSITION=None) for i, r in enumerate(rings.itertuples(index=False))])
    evidence = pd.DataFrame([
        dict(CASE_ID=cases.CASE_ID[0], EVIDENCE_TYPE="MONEY_TRAIL", TITLE="Money trail",
             PAYLOAD=json.dumps({"external_credits": 10, "distinct_external_remitters": 9, "cash_withdrawn": 500000,
                                 "median_dwell_minutes": 42.0, "top_internal_flows": [{"src": "A", "dst": "B", "amount": 1}]}),
             CREATED_BY="x", CREATED_AT=now),
        dict(CASE_ID=cases.CASE_ID[0], EVIDENCE_TYPE="MEMBERS", TITLE="Accounts", PAYLOAD=json.dumps([{"account": "A"}]),
             CREATED_BY="x", CREATED_AT=now)])
    strd = pd.DataFrame([dict(CASE_ID=cases.CASE_ID[0], VERSION=1, STATUS="DRAFT",
                              DRAFT=json.dumps({"summary_of_suspicion": "s", "grounds_of_suspicion": [
                                  {"indicator": "pass-through", "evidence": "x", "policy_ref": "POL-02#2"}]}),
                              NARRATIVE="Narrative text", CITATIONS=json.dumps(["POL-02#2"]), CITATION_VALIDITY=1.0,
                              FAITHFULNESS_SCORE=5.0, JUDGE_NOTES="ok", MODEL="m", CREATED_AT=now, REVIEWED_BY=None,
                              REVIEWED_AT=None)])
    import pipeline
    docs = pd.DataFrame(sum([pipeline.chunk_markdown(open(os.path.join(ROOT, "docs", "policies", f)).read(), f)
                             for f in os.listdir(os.path.join(ROOT, "docs", "policies"))], []))
    ev = pd.DataFrame(o["metrics"].values())
    ev.insert(0, "RUN_TS", now)
    ev["NOTES"] = ""
    comp = o["frames"]["complaints"].assign(SCAM_TYPE="Digital arrest scam", REPORTED_DATE=lambda d: d.REPORTED_TS.dt.date)
    tables = {
        "ANALYTICS.ACCOUNT_RISK": risk, "ANALYTICS.RINGS": rings, "ANALYTICS.RING_MEMBERS": o["members"],
        "ANALYTICS.RING_EDGES": o["edges"], "ANALYTICS.CASES": cases, "ANALYTICS.CASE_EVIDENCE": evidence,
        "ANALYTICS.STR_DRAFTS": strd, "ANALYTICS.EVAL_RESULTS": ev, "ANALYTICS.V_COMPLAINTS": comp,
        "ANALYTICS.RULE_CONFIG": op.rule_config(os.path.join(ROOT, "sql", "01_tables.sql")),
        "ANALYTICS.CASE_FEEDBACK": pd.DataFrame({"CASE_ID": ["x"], "DISPOSITION": ["STR_FILED"]}),
        "ANALYTICS.RULE_CONFIG_HISTORY": pd.DataFrame({"CHANGED_AT": [now], "RULE_ID": ["R01"]}),
        "ANALYTICS.MODEL_LOG": pd.DataFrame({"TRAINED_AT": [now], "MODEL_NAME": ["MULE_CLASSIFIER"], "AUC_HOLDOUT": [0.99]}),
        "GOV.AGENT_AUDIT_LOG": pd.DataFrame({"EVENT_TS": [now], "AGENT": ["ORCHESTRATOR"], "ACTION": ["RUN_FULL_PIPELINE"],
                                             "CASE_ID": [None], "MODEL": [None], "STATUS": ["OK"], "DURATION_MS": [1],
                                             "INVOKED_BY": ["me"], "DETAILS": ["{}"]}),
        "CURATED.ACCOUNT_FEATURES": feats, "RAW.DOCS": docs,
    }
    for name, df in tables.items():
        con.register("t", df)
        con.execute(f"CREATE TABLE MULEWATCH.{name} AS SELECT * FROM t")
        con.unregister("t")
    for name in ("TXN_ENRICHED", "DAILY_CHANNEL_STATS"):
        df = o["con"].execute(f"SELECT * FROM CURATED.{name}").df()
        con.register("t", df)
        con.execute(f"CREATE TABLE MULEWATCH.CURATED.{name} AS SELECT * FROM t")
        con.unregister("t")
    return con


def main():
    con = build_db()
    fake = FakeSession(con)
    mod = types.ModuleType("snowflake.snowpark.context")
    mod.get_active_session = lambda: fake
    sys.modules.setdefault("snowflake", types.ModuleType("snowflake"))
    sys.modules.setdefault("snowflake.snowpark", types.ModuleType("snowflake.snowpark"))
    sys.modules["snowflake.snowpark.context"] = mod
    from streamlit.testing.v1 import AppTest
    ok = True
    for page in ["Command Center", "Case Queue", "Investigation", "Ask MuleWatch", "Model & Governance"]:
        at = AppTest.from_file(os.path.join(ROOT, "app", "streamlit_app.py"), default_timeout=60)
        at.session_state["page"] = page
        at.run()
        errs = [e.value for e in at.exception]
        warn = [c.value for c in at.caption if "⚠️" in str(c.value)]
        print(f"{page:20s} exceptions={len(errs)} sql_warnings={len(warn)} metrics={len(at.metric)} "
              f"dataframes={len(at.dataframe)}")
        for e in errs:
            print("   EXC:", str(e)[:400])
        for w in warn:
            print("   WARN:", w)
        ok &= not errs
        if page == "Ask MuleWatch":
            at.chat_input[0].set_value("How many open cases?").run()
            print("   chat reply:", [m.markdown[0].value for m in at.chat_message][-1] if at.chat_message else None,
                  "| exceptions:", len(at.exception))
            ok &= not at.exception
    if FAILED_SQL:
        print("\nSQL that failed to transpile/run in DuckDB (expected only for Snowflake-only features):")
        for s, e in FAILED_SQL:
            print(" -", s.replace("\n", " ")[:140], "=>", e[:120])
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
