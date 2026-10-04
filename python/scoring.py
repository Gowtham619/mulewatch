"""Detection layer 1 + 2: configurable rules engine and ML classifier, blended into one score.

Pure functions (evaluate_rules, build_matrix, train_model, blend) are unit-tested offline;
the *_sp functions are the Snowflake stored-procedure handlers.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

ML_FEATURES = [
    "ACCOUNT_AGE_DAYS", "TOTAL_IN_30D", "TOTAL_OUT_30D", "N_IN_30D", "N_OUT_30D", "DISTINCT_IN_CP_30D",
    "DISTINCT_OUT_CP_30D", "CASH_OUT_30D", "NEAR_THRESHOLD_CASH_CNT_30D", "VDA_OUT_30D", "MAX_SINGLE_CREDIT_30D",
    "N_TXN_30D", "SAME_DAY_PASS_THROUGH_RATIO", "CASH_OUT_RATIO", "NIGHT_TXN_RATIO", "INCOME_MULTIPLE",
    "NEW_ACCT_HIGH_VALUE", "SHARED_DEVICE_PEERS", "COMPLAINT_CNT", "IN_CIRCULAR_FLOW", "VAGUE_PURPOSE_CNT_30D",
    "ANNUAL_INCOME_DECLARED", "LOW_INCOME_OCCUPATION", "DIGITAL_ONBOARDING", "IS_CURRENT_ACCOUNT",
]
LOW_INCOME_OCC = {"Student", "Homemaker", "Gig worker", "Farmer"}
MODEL_FILE = "mule_model.joblib"
MODEL_STAGE = "@MULEWATCH.ANALYTICS.MODEL_STAGE"

_OPS = {">=": np.greater_equal, ">": np.greater, "<=": np.less_equal, "<": np.less, "=": np.equal}


# ----------------------------------------------------------------------------- rules
def evaluate_rules(feats: pd.DataFrame, cfg: pd.DataFrame, k: float = 3.0) -> pd.DataFrame:
    """Return RULE_SCORE (0-100), RULES_HIT (csv) and RULE_COUNT for each account."""
    f = feats.copy()
    f.columns = [c.upper() for c in f.columns]
    cfg = cfg.copy()
    cfg.columns = [c.upper() for c in cfg.columns]
    cfg = cfg[cfg["ENABLED"].astype(bool)]
    weight_sum = np.zeros(len(f))
    hits = [[] for _ in range(len(f))]
    tin = pd.to_numeric(f.get("TOTAL_IN_30D", 0), errors="coerce").fillna(0).values
    for rule in cfg.itertuples(index=False):
        if rule.FEATURE not in f.columns:
            continue
        x = pd.to_numeric(f[rule.FEATURE], errors="coerce").fillna(0).values
        mask = _OPS.get(rule.OPERATOR, np.greater_equal)(x, float(rule.THRESHOLD)) & (tin >= float(rule.MIN_TOTAL_IN_30D or 0))
        weight_sum += mask * float(rule.WEIGHT)
        for i in np.flatnonzero(mask):
            hits[i].append(rule.RULE_NAME)
    return pd.DataFrame({
        "ACCOUNT_ID": f["ACCOUNT_ID"].values,
        "RULE_SCORE": np.round(100 * (1 - np.exp(-weight_sum / k)), 2),
        "RULES_HIT": [",".join(h) for h in hits],
        "RULE_COUNT": [len(h) for h in hits],
    })


# ----------------------------------------------------------------------------- ml
def build_matrix(feats: pd.DataFrame) -> pd.DataFrame:
    f = feats.copy()
    f.columns = [c.upper() for c in f.columns]
    f["LOW_INCOME_OCCUPATION"] = f["OCCUPATION_DECLARED"].isin(LOW_INCOME_OCC).astype(int)
    f["DIGITAL_ONBOARDING"] = f["ONBOARD_CHANNEL"].isin(["VIDEO_KYC", "DIGITAL"]).astype(int)
    f["IS_CURRENT_ACCOUNT"] = (f["ACCOUNT_TYPE"] == "CURRENT").astype(int)
    X = f.reindex(columns=ML_FEATURES)
    return X.apply(pd.to_numeric, errors="coerce").fillna(0).astype(float)


def train_model(X: pd.DataFrame, y: np.ndarray, seed: int = 0):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split
    y = np.asarray(y).astype(int)
    auc = None
    if y.sum() >= 10 and (len(y) - y.sum()) >= 10:
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=.25, stratify=y, random_state=seed)
        m = HistGradientBoostingClassifier(max_iter=250, learning_rate=.08, max_leaf_nodes=15,
                                           l2_regularization=1.0, class_weight="balanced", random_state=seed)
        m.fit(Xtr, ytr)
        auc = float(roc_auc_score(yte, m.predict_proba(Xte)[:, 1]))
    model = HistGradientBoostingClassifier(max_iter=250, learning_rate=.08, max_leaf_nodes=15,
                                           l2_regularization=1.0, class_weight="balanced", random_state=seed)
    model.fit(X, y)
    return model, auc


def blend(rule, ml, graph, w_rule=.5, w_ml=.35, w_graph=.15):
    rule = np.asarray(rule, dtype=float)
    graph = np.asarray(graph, dtype=float)
    if ml is None:
        tot = w_rule + w_graph
        return (w_rule * rule + w_graph * graph) / tot
    ml = np.asarray(ml, dtype=float)
    # Graph evidence can only raise a score: accounts outside rings are not penalised
    base = (w_rule * rule + w_ml * ml) / (w_rule + w_ml)
    with_graph = (w_rule * rule + w_ml * ml + w_graph * graph) / (w_rule + w_ml + w_graph)
    return np.maximum(base, with_graph)


def band(score):
    s = np.asarray(score, dtype=float)
    return np.select([s >= 80, s >= 55, s >= 35], ["CRITICAL", "HIGH", "MEDIUM"], "LOW")


# ----------------------------------------------------------------------------- SP handlers
def _load_model(session):
    import joblib
    d = "/tmp/mw_model"
    os.makedirs(d, exist_ok=True)
    try:
        session.file.get(f"{MODEL_STAGE}/{MODEL_FILE}", d)
        return joblib.load(os.path.join(d, MODEL_FILE))
    except Exception:
        return None


def score_accounts_sp(session) -> dict:
    """CALL MULEWATCH.APP.SCORE_ACCOUNTS()  - rules + ML, preliminary blend (graph added by DETECT_RINGS)."""
    from common import DB, audit, cfg_float, to_pandas, write_df
    t0 = time.time()
    feats = to_pandas(session, f"SELECT * FROM {DB}.CURATED.ACCOUNT_FEATURES")
    cfg = to_pandas(session, f"SELECT * FROM {DB}.ANALYTICS.RULE_CONFIG")
    rules = evaluate_rules(feats, cfg)
    bundle = _load_model(session)
    ml = None
    if bundle is not None:
        ml = np.round(bundle["model"].predict_proba(build_matrix(feats)[bundle["features"]])[:, 1] * 100, 2)
    wr, wm, wg = (cfg_float(session, k, d) for k, d in (("WEIGHT_RULES", .5), ("WEIGHT_ML", .35), ("WEIGHT_GRAPH", .15)))
    prelim = blend(rules.RULE_SCORE.values, ml, np.zeros(len(feats)), wr, wm, wg)
    thr = cfg_float(session, "ALERT_THRESHOLD", 55)
    out = pd.DataFrame({
        "ACCOUNT_ID": feats.ACCOUNT_ID, "CUSTOMER_ID": feats.CUSTOMER_ID, "REGION": feats.REGION,
        "ACCOUNT_TYPE": feats.ACCOUNT_TYPE, "OCCUPATION": feats.OCCUPATION_DECLARED,
        "ACCOUNT_AGE_DAYS": feats.ACCOUNT_AGE_DAYS, "TOTAL_IN_30D": feats.TOTAL_IN_30D,
        "TOTAL_OUT_30D": feats.TOTAL_OUT_30D, "RULE_SCORE": rules.RULE_SCORE.values,
        "ML_SCORE": ml if ml is not None else np.nan, "GRAPH_SCORE": 0.0, "FINAL_SCORE": np.round(prelim, 2),
        "RISK_BAND": band(prelim), "RULES_HIT": rules.RULES_HIT.values, "RULE_COUNT": rules.RULE_COUNT.values,
        "RING_ID": None, "IS_ALERTED": prelim >= thr,
        "SCORED_AT": pd.Timestamp.utcnow().tz_localize(None).floor("s"),
    })
    write_df(session, out, f"{DB}.ANALYTICS.ACCOUNT_RISK", overwrite=True)
    res = {"accounts": int(len(out)), "prelim_alerts": int(out.IS_ALERTED.sum()), "ml_used": ml is not None}
    audit(session, "SCORING_ENGINE", "SCORE_ACCOUNTS", details=res, started=t0)
    return res


def train_model_sp(session) -> dict:
    """CALL MULEWATCH.APP.TRAIN_MULE_MODEL()
    Labels = outcomes of past investigations + analyst dispositions captured in the app (feedback loop)."""
    import joblib
    from common import DB, audit, to_pandas
    t0 = time.time()
    feats = to_pandas(session, f"SELECT * FROM {DB}.CURATED.ACCOUNT_FEATURES")
    labels = to_pandas(session, f"""
        WITH l AS (
          SELECT ACCOUNT_ID, LABEL, 1 AS PRIO FROM {DB}.ANALYTICS.FEEDBACK_LABELS
          UNION ALL
          SELECT ACCOUNT_ID, LABEL, 2 FROM {DB}.ANALYTICS.HISTORICAL_LABELS)
        SELECT ACCOUNT_ID, LABEL FROM l QUALIFY ROW_NUMBER() OVER (PARTITION BY ACCOUNT_ID ORDER BY PRIO) = 1""")
    df = feats.merge(labels, on="ACCOUNT_ID")
    X = build_matrix(df)
    y = df["LABEL"].astype(int).values
    model, auc = train_model(X, y)
    path = f"/tmp/{MODEL_FILE}"
    joblib.dump({"model": model, "features": ML_FEATURES}, path)
    session.file.put(path, MODEL_STAGE, auto_compress=False, overwrite=True)
    version = "V" + pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S")
    registry_status = "SKIPPED"
    try:  # Snowflake Model Registry (governed, versioned, servable)
        from snowflake.ml.registry import Registry
        reg = Registry(session=session, database_name=DB, schema_name="ANALYTICS")
        mv = reg.log_model(model, model_name="MULE_CLASSIFIER", version_name=version,
                           sample_input_data=X.head(50), comment="Mule account classifier (HistGradientBoosting)")
        if auc is not None:
            mv.set_metric("auc_holdout", auc)
        registry_status = "REGISTERED"
    except Exception as e:  # registry is optional; stage copy is authoritative for scoring
        registry_status = f"NOT_REGISTERED: {str(e)[:200]}"
    session.sql(f"""INSERT INTO {DB}.ANALYTICS.MODEL_LOG
        SELECT CURRENT_TIMESTAMP(), 'MULE_CLASSIFIER', '{version}', {auc if auc is not None else 'NULL'},
               {len(y)}, {int(y.sum())}, '{",".join(ML_FEATURES)}', $${registry_status}$$,
               '{MODEL_STAGE}/{MODEL_FILE}'""").collect()
    res = {"version": version, "auc_holdout": auc, "n_train": int(len(y)), "n_pos": int(y.sum()),
           "registry": registry_status}
    audit(session, "ML_TRAINER", "TRAIN_MODEL", details=res, started=t0)
    return res
