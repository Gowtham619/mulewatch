"""Offline replica of the Snowflake pipeline (no Snowflake account needed).

* generates the synthetic world,
* runs the REAL dynamic-table SQL from sql/03_dynamic_tables.sql, transpiled Snowflake -> DuckDB,
* runs the REAL rules config from sql/01_tables.sql, the ML trainer, ring detector and evaluator.

Usage:  python tests/offline_pipeline.py [n_customers] [n_rings]
"""
from __future__ import annotations

import os
import re
import sys
import time

import duckdb
import numpy as np
import pandas as pd
import sqlglot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))

import datagen  # noqa: E402
import evaluate  # noqa: E402
import rings  # noqa: E402
import scoring  # noqa: E402

SKIP_DT = {"DEVICE_EVENTS_FLAT", "PAYMENT_MSG_FLAT"}  # JSON flattening built directly below


def dynamic_table_queries(path):
    sql = open(path).read()
    out = []
    for m in re.finditer(r"CREATE OR REPLACE DYNAMIC TABLE (\w+)\.(\w+)(.*?);\s*(?=\n--|\nCREATE|\Z)", sql, re.S):
        schema, name, body = m.group(1), m.group(2), m.group(3)
        q = re.split(r"\nAS\n", body, maxsplit=1)[1]
        out.append((schema, name, q))
    return out


def rule_config(path):
    sql = open(path).read()
    block = sql.split("INSERT INTO ANALYTICS.RULE_CONFIG", 1)[1].split(";", 1)[0]
    rows = re.findall(r"\('(R\d+)','(\w+)','(\w+)','([<>=]+)',([\d.]+),([\d.]+),([\d.]+),'(\w+)','([^']*)'\)", block)
    return pd.DataFrame([dict(RULE_ID=r[0], RULE_NAME=r[1], FEATURE=r[2], OPERATOR=r[3], THRESHOLD=float(r[4]),
                              MIN_TOTAL_IN_30D=float(r[5]), BASE_WEIGHT=float(r[6]), WEIGHT=float(r[6]),
                              TYPOLOGY=r[7], DESCRIPTION=r[8], ENABLED=True) for r in rows])


def config_defaults(path):
    sql = open(path).read()
    return dict(re.findall(r"\('(\w+)',\s*'([^']*)',", sql))


def run(n_customers=8000, n_rings=24, seed=11, verbose=True):
    t0 = time.time()
    f = datagen.generate_frames(n_customers=n_customers, n_rings=n_rings, seed=seed)
    con = duckdb.connect()
    con.execute("CREATE SCHEMA RAW; CREATE SCHEMA CURATED;")
    for name, key in [("CUSTOMERS", "customers"), ("ACCOUNTS", "accounts"), ("TRANSACTIONS", "transactions"),
                      ("FRAUD_COMPLAINTS", "complaints")]:
        df = f[key].copy()
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: None if v is None else (str(v) if not isinstance(v, (int, float)) else v))
        if name == "ACCOUNTS":
            df["OPEN_DATE"] = pd.to_datetime(df["OPEN_DATE"])
        con.register("tmp_df", df)
        con.execute(f"CREATE TABLE RAW.{name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
    dev = pd.DataFrame([dict(EVENT_ID=e["event_id"], ACCOUNT_ID=e["account_id"], EVENT_TYPE=e["event_type"],
                             EVENT_TS=pd.Timestamp(e["ts"]), DEVICE_ID=e["device"]["id"],
                             DEVICE_OS=str(e["device"]["os"]), DEVICE_ROOTED=e["device"]["rooted"],
                             IP_ADDRESS=e["network"]["ip"], GEO_CITY=str(e["network"]["geo"]["city"]))
                        for e in f["device_events"]])
    vague = {"personal", "help", "loan return", "family support", "gift", "family"}
    pay = pd.DataFrame([dict(MSG_ID=m["msg_id"], TXN_ID=m["txn_id"], VALUE_TS=pd.Timestamp(m["value_ts"]),
                             AMOUNT=m["amount"]["value"], REMITTER_ACCOUNT=m["remitter"]["account"],
                             BENEFICIARY_ACCOUNT=m["beneficiary"]["account"],
                             REMITTANCE_INFO=str(m["remittance_info"]),
                             IS_VAGUE_PURPOSE=str(m["remittance_info"]).lower() in vague)
                        for m in f["payment_messages"]])
    for name, df in (("DEVICE_EVENTS_FLAT", dev), ("PAYMENT_MSG_FLAT", pay)):
        con.register("tmp_df", df)
        con.execute(f"CREATE TABLE CURATED.{name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")

    for schema, name, q in dynamic_table_queries(os.path.join(ROOT, "sql", "03_dynamic_tables.sql")):
        if name in SKIP_DT:
            continue
        dq = sqlglot.transpile(q, read="snowflake", write="duckdb")[0]
        con.execute(f"CREATE TABLE {schema}.{name} AS {dq}")
        if verbose:
            print(f"  DT {schema}.{name}: {con.execute(f'SELECT COUNT(*) FROM {schema}.{name}').fetchone()[0]} rows")

    feats = con.execute("SELECT * FROM CURATED.ACCOUNT_FEATURES").df()
    cfg = rule_config(os.path.join(ROOT, "sql", "01_tables.sql"))
    defaults = config_defaults(os.path.join(ROOT, "sql", "00_setup.sql"))
    thr = float(defaults["ALERT_THRESHOLD"])
    seed_thr = float(defaults["RING_SEED_THRESHOLD"])
    wr, wm, wg = (float(defaults[k]) for k in ("WEIGHT_RULES", "WEIGHT_ML", "WEIGHT_GRAPH"))

    rules_df = scoring.evaluate_rules(feats, cfg)
    hist = f["historical_labels"]
    lab = feats.merge(hist[["ACCOUNT_ID", "LABEL"]], on="ACCOUNT_ID")
    model, auc = scoring.train_model(scoring.build_matrix(lab), lab.LABEL.values)
    ml = model.predict_proba(scoring.build_matrix(feats)[scoring.ML_FEATURES])[:, 1] * 100
    prelim = scoring.blend(rules_df.RULE_SCORE.values, ml, np.zeros(len(feats)), wr, wm, wg)
    risk = feats[["ACCOUNT_ID", "REGION", "TOTAL_IN_30D", "TOTAL_OUT_30D", "COMPLAINT_CNT", "CASH_OUT_RATIO",
                  "VDA_OUT_30D", "CASH_OUT_30D", "NEAR_THRESHOLD_CASH_CNT_30D", "IN_CIRCULAR_FLOW",
                  "LAST_TXN_TS"]].copy()
    risk["RULE_SCORE"] = rules_df.RULE_SCORE.values
    risk["RULES_HIT"] = rules_df.RULES_HIT.values
    risk["ML_SCORE"] = ml
    risk["FINAL_SCORE"] = prelim

    elig = set(risk[(risk.FINAL_SCORE >= seed_thr * .5) | (risk.COMPLAINT_CNT >= 1)].ACCOUNT_ID)
    te = con.execute("""WITH anchor AS (SELECT MAX(TXN_TS) AS AS_OF FROM CURATED.TXN_ENRICHED)
        SELECT SRC_ACCOUNT_ID SRC, DST_ACCOUNT_ID DST, SUM(AMOUNT) AMOUNT, COUNT(*) TXN_COUNT
        FROM CURATED.TXN_ENRICHED, anchor WHERE SRC_INTERNAL AND DST_INTERNAL
        AND TXN_TS > AS_OF - INTERVAL 30 DAY GROUP BY 1,2""").df()
    te = te[te.SRC.isin(elig) & te.DST.isin(elig)]
    d = dev[dev.ACCOUNT_ID.isin(elig)][["DEVICE_ID", "ACCOUNT_ID"]].drop_duplicates()
    dp = d.merge(d, on="DEVICE_ID")
    dp = dp[dp.ACCOUNT_ID_x < dp.ACCOUNT_ID_y].rename(columns={"ACCOUNT_ID_x": "A", "ACCOUNT_ID_y": "B"})
    rg, mem, edges = rings.build_rings(risk, te, dp[["A", "B", "DEVICE_ID"]], seed_threshold=seed_thr)
    graph = mem.merge(rg[["RING_ID", "RING_SCORE"]], on="RING_ID").groupby("ACCOUNT_ID").RING_SCORE.max()
    risk["GRAPH_SCORE"] = risk.ACCOUNT_ID.map(graph).fillna(0)
    risk["FINAL_SCORE"] = scoring.blend(risk.RULE_SCORE, risk.ML_SCORE, risk.GRAPH_SCORE, wr, wm, wg)
    risk["IS_ALERTED"] = risk.FINAL_SCORE >= thr
    metrics = evaluate.compute_metrics(risk, mem, f["ground_truth"], set(hist.ACCOUNT_ID), thr)
    if verbose:
        print(f"\nAccounts {len(feats):,} | txns {len(f['transactions']):,} | true mules "
              f"{int(f['ground_truth'].IS_MULE.sum())} | rings detected {len(rg)} | ML AUC(holdout) {auc:.3f}")
        print(rg.groupby("TYPOLOGY").size().to_string())
        print(pd.DataFrame(metrics).to_string(index=False))
        print(f"elapsed {time.time() - t0:.1f}s")
    return dict(metrics={m["DETECTOR"]: m for m in metrics}, rings=rg, members=mem, edges=edges, risk=risk, auc=auc,
                features=feats, frames=f, con=con)


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:3]]
    run(*a)
