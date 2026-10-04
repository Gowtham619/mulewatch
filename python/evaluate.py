"""Measure detection quality against held-out synthetic ground truth.

Accounts that were used as training labels (HISTORICAL_LABELS / FEEDBACK_LABELS) are excluded,
so the numbers reflect performance on rings the system has never seen.
"""
from __future__ import annotations

import time

import pandas as pd


def _prf(pred: pd.Series, truth: pd.Series):
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return dict(PRECISION=round(p, 4), RECALL=round(r, 4), F1=round(f1, 4), TP=tp, FP=fp, FN=fn,
                ALERTED=int(pred.sum()))


def compute_metrics(risk: pd.DataFrame, members: pd.DataFrame, gt: pd.DataFrame, train_ids: set,
                    alert_threshold: float = 55.0) -> list[dict]:
    d = risk.merge(gt[["ACCOUNT_ID", "IS_MULE", "RING_KEY"]], on="ACCOUNT_ID", how="left")
    d = d[~d.ACCOUNT_ID.isin(train_ids)]
    truth = d.IS_MULE.fillna(False).astype(bool)
    out = []
    out.append(dict(DETECTOR="RULES_ONLY", **_prf(d.RULE_SCORE.fillna(0) >= alert_threshold, truth)))
    if d.ML_SCORE.notna().any():
        out.append(dict(DETECTOR="ML_ONLY", **_prf(d.ML_SCORE.fillna(0) >= 50, truth)))
    out.append(dict(DETECTOR="BLENDED_ALERTS", **_prf(d.IS_ALERTED.fillna(False).astype(bool), truth)))
    # ring-level: a held-out true ring is "recovered" if >=50% of its members sit in one detected ring
    held = gt[gt.IS_MULE.astype(bool) & ~gt.ACCOUNT_ID.isin(train_ids)]
    rec = 0
    keys = held.RING_KEY.dropna().unique()
    m = members.set_index("ACCOUNT_ID").RING_ID if len(members) else pd.Series(dtype=str)
    for k in keys:
        accs = held[held.RING_KEY == k].ACCOUNT_ID
        found = m.reindex(accs).dropna()
        if len(found) and found.value_counts().iloc[0] >= .5 * len(accs):
            rec += 1
    det_prec = 0.0
    if len(members):
        mm = members.merge(gt[["ACCOUNT_ID", "IS_MULE"]], on="ACCOUNT_ID", how="left")
        purity = mm.groupby("RING_ID").IS_MULE.apply(lambda s: s.fillna(False).astype(bool).mean())
        det_prec = float((purity >= .5).mean())
    out.append(dict(DETECTOR="RING_RECOVERY", PRECISION=round(det_prec, 4),
                    RECALL=round(rec / len(keys), 4) if len(keys) else 0.0,
                    F1=None, TP=rec, FP=int(len(purity) - (purity >= .5).sum()) if len(members) else 0,
                    FN=int(len(keys) - rec), ALERTED=int(members.RING_ID.nunique()) if len(members) else 0))
    return out


def evaluate_sp(session) -> dict:
    """CALL MULEWATCH.APP.EVALUATE_DETECTION()"""
    from common import DB, audit, cfg_float, to_pandas, write_df
    t0 = time.time()
    risk = to_pandas(session, f"SELECT * FROM {DB}.ANALYTICS.ACCOUNT_RISK")
    members = to_pandas(session, f"SELECT * FROM {DB}.ANALYTICS.RING_MEMBERS")
    gt = to_pandas(session, f"SELECT * FROM {DB}.GOV.GROUND_TRUTH")
    train = set(to_pandas(session, f"""SELECT ACCOUNT_ID FROM {DB}.ANALYTICS.HISTORICAL_LABELS
                                       UNION SELECT ACCOUNT_ID FROM {DB}.ANALYTICS.FEEDBACK_LABELS""").ACCOUNT_ID)
    rows = compute_metrics(risk, members, gt, train, cfg_float(session, "ALERT_THRESHOLD", 55))
    df = pd.DataFrame(rows)
    df.insert(0, "RUN_TS", pd.Timestamp.utcnow().tz_localize(None).floor("s"))
    df["NOTES"] = f"held-out accounts: {len(risk) - len(train & set(risk.ACCOUNT_ID))}"
    write_df(session, df[["RUN_TS", "DETECTOR", "PRECISION", "RECALL", "F1", "TP", "FP", "FN", "ALERTED", "NOTES"]],
             f"{DB}.ANALYTICS.EVAL_RESULTS")
    res = {r["DETECTOR"]: {"precision": r["PRECISION"], "recall": r["RECALL"]} for r in rows}
    audit(session, "EVALUATOR", "EVALUATE_DETECTION", details=res, started=t0)
    return res
