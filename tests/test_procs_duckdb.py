"""Executes the REAL stored-procedure handlers against DuckDB through a fake Snowpark session.

Cortex functions are replaced by deterministic DuckDB UDFs, so this verifies the SQL and the
Python glue of scoring, ring detection, case management, triage, investigator, compliance,
human disposition, rule tuning and evaluation - everything except the LLM itself.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile

import duckdb
import pandas as pd
import sqlglot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "python"))

import offline_pipeline as op  # noqa: E402

LOG = []


class Row(tuple):
    def __new__(cls, values, names):
        r = super().__new__(cls, values)
        r._names = [n.lower() for n in names]
        return r

    def __getitem__(self, k):
        if isinstance(k, str):
            return tuple.__getitem__(self, self._names.index(k.lower()))
        return tuple.__getitem__(self, k)


class Res:
    def __init__(self, df):
        self.df = df

    def to_pandas(self):
        return self.df

    def collect(self):
        return [Row(tuple(r), list(self.df.columns)) for r in self.df.itertuples(index=False)]


class FakeFile:
    def __init__(self):
        self.dir = tempfile.mkdtemp()

    def put(self, local, stage, auto_compress=False, overwrite=True):
        shutil.copy(local, os.path.join(self.dir, os.path.basename(local)))

    def get(self, stage_path, local_dir):
        os.makedirs(local_dir, exist_ok=True)
        src = os.path.join(self.dir, stage_path.split("/")[-1])
        if not os.path.exists(src):
            raise FileNotFoundError(src)
        shutil.copy(src, local_dir)


def to_duck(sql: str) -> str:
    s = sql.strip()
    s = re.sub(r"CREATE OR REPLACE TEMPORARY TABLE", "CREATE OR REPLACE TABLE", s, flags=re.I)
    s = re.sub(r"CREATE OR REPLACE TRANSIENT TABLE", "CREATE OR REPLACE TABLE", s, flags=re.I)
    s = s.replace("SNOWFLAKE.CORTEX.", "")
    s = re.sub(r"(\w+)\s*=>\s*", "", s)  # named args -> positional (UDF stand-ins)
    out = sqlglot.transpile(s, read="snowflake", write="duckdb")[0]
    return out


class FakeSession:
    def __init__(self, con):
        self.con = con
        self.file = FakeFile()

    def sql(self, q, params=None):
        s = q.strip()
        up = s.upper()
        if up.startswith("ALTER DYNAMIC TABLE"):
            return Res(pd.DataFrame({"status": ["ok"]}))
        if "SEARCH_PREVIEW" in up:
            raise RuntimeError("Cortex Search not available in DuckDB")
        try:
            d = to_duck(s)
            df = self.con.execute(d).df()
        except Exception as e:
            LOG.append((s[:300], str(e)[:300]))
            raise
        df.columns = [c.upper() for c in df.columns]
        return Res(df)

    def write_pandas(self, pdf, table_name, database, schema, overwrite=False, **kw):
        fq = f"{database}.{schema}.{table_name}"
        if overwrite:
            self.con.execute(f"DELETE FROM {fq}")
        self.con.register("wp", pdf)
        self.con.execute(f"INSERT INTO {fq} BY NAME SELECT * FROM wp")
        self.con.unregister("wp")


# ------------------------------------------------------------------ Cortex stand-ins
def fake_complete(model, prompt):
    p = str(prompt)
    if "EVIDENCE PACK" in p:
        return json.dumps({"headline": "Mule layering ring", "typology": "MULE_LAYERING", "hypothesis": "h",
                           "key_findings": ["Rapid pass-through"], "confidence": 0.86,
                           "recommended_actions": ["File STR"], "information_gaps": []})
    if "strict QA reviewer" in p:
        return json.dumps({"faithfulness": 4, "completeness": 4, "issues": []})
    if "Suspicious Transaction" in p:
        ids = re.findall(r"\[([A-Z]{3}-\d{2}#\d+)\]", p)
        return "```json\n" + json.dumps({"summary_of_suspicion": "s", "narrative": "Funds of 1,00,000 moved.",
                                         "grounds_of_suspicion": [{"indicator": "pass-through", "evidence": "e",
                                                                   "policy_ref": ids[0] if ids else "POL-02#2"}],
                                         "citations": ids[:2]}) + "\n```"
    return "OK - triage rationale."


def build():
    o = op.run(6000, 16, 3, verbose=False)
    f = o["frames"]
    con = duckdb.connect()
    con.create_function("ai_complete", fake_complete, [str, str], str)
    con.execute("ATTACH ':memory:' AS MULEWATCH")
    for sch in ("RAW", "CURATED", "ANALYTICS", "AI", "GOV", "APP"):
        con.execute(f"CREATE SCHEMA MULEWATCH.{sch}")
    con.execute("USE MULEWATCH")
    ddl = open(os.path.join(ROOT, "sql", "01_tables.sql")).read()
    for stmt in ddl.split(";"):
        st = stmt.strip()
        if not st.upper().lstrip("-\n =").startswith(("CREATE", "--")) and "CREATE OR REPLACE TABLE" not in st:
            continue
        m = re.search(r"CREATE OR REPLACE TABLE[\s\S]*", st)
        if not m:
            continue
        st = m.group(0)
        st = re.sub(r"\)\s*CLUSTER BY \(TO_DATE\(TXN_TS\)\)", ")", st)
        st = re.sub(r"COMMENT\s*=\s*'[^']*'", "", st)
        st = re.sub(r"PRIMARY KEY", "", st)
        st = st.replace("VARIANT", "JSON").replace("DEFAULT CURRENT_TIMESTAMP()", "")
        con.execute(sqlglot.transpile(st, read="snowflake", write="duckdb")[0])
    con.execute("CREATE TABLE APP.CONFIG (KEY VARCHAR, VALUE VARCHAR, DESCRIPTION VARCHAR)")
    for k, v in op.config_defaults(os.path.join(ROOT, "sql", "00_setup.sql")).items():
        con.execute("INSERT INTO APP.CONFIG VALUES (?, ?, '')", [k, v])
    con.execute("UPDATE APP.CONFIG SET VALUE = 'fake-llm' WHERE KEY = 'ACTIVE_LLM'")
    cfg = op.rule_config(os.path.join(ROOT, "sql", "01_tables.sql"))
    cfg["UPDATED_AT"] = pd.Timestamp.now()
    sess = FakeSession(con)
    sess.write_pandas(cfg, "RULE_CONFIG", "MULEWATCH", "ANALYTICS")
    for key, tbl in [("customers", "RAW.CUSTOMERS"), ("accounts", "RAW.ACCOUNTS"), ("transactions", "RAW.TRANSACTIONS"),
                     ("kyc_notes", "RAW.KYC_NOTES"), ("complaints", "RAW.FRAUD_COMPLAINTS"),
                     ("ground_truth", "GOV.GROUND_TRUTH"), ("historical_labels", "ANALYTICS.HISTORICAL_LABELS")]:
        df = f[key].copy()
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: None if v is None else (v if isinstance(v, (bool, int, float)) else str(v)))
        sch, t = tbl.split(".")
        sess.write_pandas(df, t, "MULEWATCH", sch)
    for name in ("TXN_ENRICHED", "DEVICE_EVENTS_FLAT", "ACCOUNT_FEATURES", "DAILY_CHANNEL_STATS"):
        df = o["con"].execute(f"SELECT * FROM CURATED.{name}").df()
        con.register("t", df)
        con.execute(f"CREATE TABLE MULEWATCH.CURATED.{name} AS SELECT * FROM t")
        con.unregister("t")
    import pipeline
    docs = pd.DataFrame(sum([pipeline.chunk_markdown(open(os.path.join(ROOT, "docs", "policies", x)).read(), x)
                             for x in sorted(os.listdir(os.path.join(ROOT, "docs", "policies")))], []))
    docs["LOADED_AT"] = pd.Timestamp.now()
    sess.write_pandas(docs, "DOCS", "MULEWATCH", "RAW")
    return sess


def main():
    import agents
    import evaluate
    import rings
    import scoring
    s = build()
    results = {}

    def step(name, fn):
        try:
            results[name] = fn()
            print(f"✔ {name}: {json.dumps(results[name], default=str)[:220]}")
            return True
        except Exception as e:
            print(f"✘ {name}: {type(e).__name__}: {str(e)[:300]}")
            return False

    ok = True
    ok &= step("TRAIN_MULE_MODEL", lambda: scoring.train_model_sp(s))
    ok &= step("SCORE_ACCOUNTS", lambda: scoring.score_accounts_sp(s))
    ok &= step("DETECT_RINGS_AND_CASES", lambda: rings.detect_rings_sp(s))
    ok &= step("DETECT_RINGS_AND_CASES (idempotent rerun)", lambda: rings.detect_rings_sp(s))
    ok &= step("TRIAGE_AGENT", lambda: agents.triage_sp(s))
    case = s.sql("SELECT CASE_ID FROM MULEWATCH.ANALYTICS.CASES WHERE CASE_TYPE = 'RING' "
                 "ORDER BY PRIORITY_SCORE DESC NULLS LAST LIMIT 1").collect()[0][0]
    ok &= step("INVESTIGATOR_AGENT", lambda: agents.investigate_sp(s, case))
    ok &= step("COMPLIANCE_AGENT", lambda: agents.compliance_sp(s, case))
    ok &= step("GET_CASE_BRIEF", lambda: json.loads(agents.case_brief_sp(s, case))["status"])
    ok &= step("RECORD_DISPOSITION", lambda: agents.record_disposition_sp(s, case, "STR_FILED", "qa@test", "ok"))
    ok &= step("TUNE_RULES", lambda: agents.tune_rules_sp(s, 3))
    ok &= step("EVALUATE_DETECTION", lambda: evaluate.evaluate_sp(s))
    ev = s.sql(f"SELECT EVIDENCE_TYPE FROM MULEWATCH.ANALYTICS.CASE_EVIDENCE WHERE CASE_ID = '{case}'").to_pandas()
    str_ = s.sql("SELECT CITATION_VALIDITY, FAITHFULNESS_SCORE, JUDGE_NOTES FROM MULEWATCH.ANALYTICS.STR_DRAFTS").to_pandas()
    print("evidence types:", sorted(ev.EVIDENCE_TYPE))
    print("str qa:", str_.to_dict("records"))
    n_cases = s.sql("SELECT STATUS, COUNT(*) N FROM MULEWATCH.ANALYTICS.CASES GROUP BY 1").to_pandas()
    print("case statuses:", n_cases.to_dict("records"))
    if LOG:
        print("\nSQL errors encountered:")
        for q, e in LOG:
            print(" -", q.replace("\n", " ")[:160], "\n   =>", e[:200])
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
