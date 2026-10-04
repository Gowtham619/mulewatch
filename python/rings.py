"""Detection layer 3: money + device graph -> mule rings, roles, ring score, and case management."""
from __future__ import annotations

import math
import time

import networkx as nx
import numpy as np
import pandas as pd

from scoring import band, blend


def build_rings(risk: pd.DataFrame, transfers: pd.DataFrame, device_pairs: pd.DataFrame,
                seed_threshold: float = 35.0, min_size: int = 3, min_edge_amount: float = 10_000):
    """Pure function.
    risk:        ACCOUNT_ID, FINAL_SCORE (prelim), COMPLAINT_CNT, CASH_OUT_RATIO, VDA_OUT_30D, TOTAL_IN_30D,
                 TOTAL_OUT_30D, CASH_OUT_30D, NEAR_THRESHOLD_CASH_CNT_30D, IN_CIRCULAR_FLOW, REGION, LAST_TXN_TS
    transfers:   SRC, DST, AMOUNT, TXN_COUNT   (internal->internal, trailing 30d)
    device_pairs: A, B, DEVICE_ID
    Returns (rings_df, members_df, edges_df)."""
    r = risk.set_index("ACCOUNT_ID")
    num = r.select_dtypes(include="number").columns
    r[num] = r[num].fillna(0)
    score = r["FINAL_SCORE"].fillna(0)
    seeds = set(r.index[(score >= seed_threshold) | (r["COMPLAINT_CNT"].fillna(0) >= 1)])
    eligible = set(r.index[score >= seed_threshold * 0.5]) | seeds

    g = nx.Graph()
    tr = transfers[(transfers.AMOUNT >= min_edge_amount)
                   & transfers.SRC.isin(eligible) & transfers.DST.isin(eligible)
                   & (transfers.SRC.isin(seeds) | transfers.DST.isin(seeds))]
    for e in tr.itertuples(index=False):
        g.add_edge(e.SRC, e.DST)
    dp = device_pairs[device_pairs.A.isin(eligible) & device_pairs.B.isin(eligible)
                      & (device_pairs.A.isin(seeds) | device_pairs.B.isin(seeds))]
    for e in dp.itertuples(index=False):
        g.add_edge(e.A, e.B)

    rings, members, edges = [], [], []
    for comp in nx.connected_components(g):
        if len(comp) < min_size:
            continue
        comp = sorted(comp)
        ring_id = f"RING-{comp[0]}"
        cs = set(comp)
        t_in = transfers[transfers.DST.isin(cs) & transfers.SRC.isin(cs)]
        # a ring needs money moving between members and at least two independently suspicious accounts
        if len(cs & seeds) < 2 or len(t_in[t_in.AMOUNT >= min_edge_amount]) == 0:
            continue
        ring_in = t_in.groupby("DST").AMOUNT.sum()
        ring_out = t_in.groupby("SRC").AMOUNT.sum()
        roles = {}
        for a in comp:
            row = r.loc[a]
            rin, rout = float(ring_in.get(a, 0)), float(ring_out.get(a, 0))
            tin, tout = float(row.TOTAL_IN_30D or 0), float(row.TOTAL_OUT_30D or 0)
            if (row.NEAR_THRESHOLD_CASH_CNT_30D or 0) >= 2:
                role = "DEPOSITOR"
            elif (row.IN_CIRCULAR_FLOW or 0) >= 1:
                role = "CIRCULAR_PARTY"
            elif (row.CASH_OUT_RATIO or 0) >= .4 or (row.VDA_OUT_30D or 0) >= .3 * max(tout, 1):
                role = "CASH_OUT"
            elif rout > 0 and rin < .3 * max(tin, 1):
                role = "COLLECTOR"
            elif rin > 0 and rout > 0:
                role = "LAYER"
            elif rin > 0:
                role = "CONSOLIDATOR"
            else:
                role = "ASSOCIATE"
            roles[a] = role
            members.append(dict(RING_ID=ring_id, ACCOUNT_ID=a, ROLE=role, PRELIM_SCORE=float(score.get(a, 0)),
                                IN_30D=tin, OUT_30D=tout))
        rc = pd.Series(roles).value_counts()
        if rc.get("DEPOSITOR", 0) >= 2:
            typ = "STRUCTURING"
        elif rc.get("CIRCULAR_PARTY", 0) >= 2:
            typ = "ROUND_TRIPPING"
        else:
            typ = "MULE_LAYERING"
        sub = r.loc[comp]
        ext_in = float((sub.TOTAL_IN_30D.fillna(0) - pd.Series(ring_in).reindex(comp).fillna(0).values).clip(lower=0).sum())
        n_dev = int(dp[dp.A.isin(cs) & dp.B.isin(cs)].DEVICE_ID.nunique())
        complaints = int(sub.COMPLAINT_CNT.fillna(0).sum())
        cash_out = float(sub.CASH_OUT_30D.fillna(0).sum())
        vda = float(sub.VDA_OUT_30D.fillna(0).sum())
        top = np.sort(score.reindex(comp).fillna(0).values)[::-1]
        base = float(top[: max(1, math.ceil(len(top) / 2))].mean())
        bonus = 6 * math.log2(len(comp)) + 5 * min(complaints, 4) + 8 * (n_dev > 0) + 8 * (cash_out + vda > 100_000)
        rings.append(dict(
            RING_ID=ring_id, TYPOLOGY=typ, MEMBER_COUNT=len(comp),
            COLLECTORS=int(rc.get("COLLECTOR", 0) + rc.get("DEPOSITOR", 0)),
            LAYERS=int(rc.get("LAYER", 0) + rc.get("CIRCULAR_PARTY", 0)),
            CASH_OUT_NODES=int(rc.get("CASH_OUT", 0) + rc.get("CONSOLIDATOR", 0)),
            TOTAL_EXTERNAL_IN_30D=ext_in, INTERNAL_FLOW_30D=float(t_in.AMOUNT.sum()), CASH_OUT_30D=cash_out,
            VDA_OUT_30D=vda, COMPLAINT_CNT=complaints, SHARED_DEVICES=n_dev,
            RING_SCORE=round(min(100.0, base + bonus), 2), PRIMARY_REGION=sub.REGION.mode().iat[0],
            LAST_ACTIVITY_TS=pd.to_datetime(sub.LAST_TXN_TS).max()))
        for e in t_in.itertuples(index=False):
            edges.append(dict(RING_ID=ring_id, SRC=e.SRC, DST=e.DST, EDGE_TYPE="TRANSFER",
                              AMOUNT=float(e.AMOUNT), TXN_COUNT=int(e.TXN_COUNT)))
        for e in dp[dp.A.isin(cs) & dp.B.isin(cs)].drop_duplicates(["A", "B"]).itertuples(index=False):
            edges.append(dict(RING_ID=ring_id, SRC=e.A, DST=e.B, EDGE_TYPE="SHARED_DEVICE", AMOUNT=0.0, TXN_COUNT=0))
    cols_r = ["RING_ID", "TYPOLOGY", "MEMBER_COUNT", "COLLECTORS", "LAYERS", "CASH_OUT_NODES", "TOTAL_EXTERNAL_IN_30D",
              "INTERNAL_FLOW_30D", "CASH_OUT_30D", "VDA_OUT_30D", "COMPLAINT_CNT", "SHARED_DEVICES", "RING_SCORE",
              "PRIMARY_REGION", "LAST_ACTIVITY_TS"]
    return (pd.DataFrame(rings, columns=cols_r),
            pd.DataFrame(members, columns=["RING_ID", "ACCOUNT_ID", "ROLE", "PRELIM_SCORE", "IN_30D", "OUT_30D"]),
            pd.DataFrame(edges, columns=["RING_ID", "SRC", "DST", "EDGE_TYPE", "AMOUNT", "TXN_COUNT"]))


def stabilise_ids(rings, members, edges, previous_members: pd.DataFrame):
    """Keep ring ids stable across runs: reuse an old id when >=50% of members overlap."""
    if previous_members is None or len(previous_members) == 0 or len(rings) == 0:
        return rings, members, edges
    prev = previous_members.groupby("RING_ID").ACCOUNT_ID.apply(set).to_dict()
    mapping = {}
    for rid, g in members.groupby("RING_ID"):
        cur = set(g.ACCOUNT_ID)
        best, best_ov = None, 0.0
        for pid, ps in prev.items():
            ov = len(cur & ps) / max(1, min(len(cur), len(ps)))
            if ov > best_ov:
                best, best_ov = pid, ov
        if best is not None and best_ov >= .5 and best not in mapping.values():
            mapping[rid] = best
    for df in (rings, members, edges):
        df["RING_ID"] = df["RING_ID"].map(lambda x: mapping.get(x, x))
    return rings, members, edges


# ----------------------------------------------------------------------------- SP handler
def detect_rings_sp(session) -> dict:
    """CALL MULEWATCH.APP.DETECT_RINGS_AND_CASES()"""
    from common import DB, audit, cfg_float, to_pandas, write_df
    t0 = time.time()
    seed_thr = cfg_float(session, "RING_SEED_THRESHOLD", 35)
    alert_thr = cfg_float(session, "ALERT_THRESHOLD", 55)
    risk = to_pandas(session, f"""
        SELECT r.*, f.COMPLAINT_CNT, f.CASH_OUT_RATIO, f.VDA_OUT_30D, f.CASH_OUT_30D,
               f.NEAR_THRESHOLD_CASH_CNT_30D, f.IN_CIRCULAR_FLOW, f.LAST_TXN_TS
        FROM {DB}.ANALYTICS.ACCOUNT_RISK r JOIN {DB}.CURATED.ACCOUNT_FEATURES f USING (ACCOUNT_ID)""")
    # preliminary score = rules + ML only (recomputed so repeated runs never compound graph evidence)
    wr, wm, wg = (cfg_float(session, k, d) for k, d in (("WEIGHT_RULES", .5), ("WEIGHT_ML", .35), ("WEIGHT_GRAPH", .15)))
    ml = None if risk.ML_SCORE.isna().all() else risk.ML_SCORE.fillna(0).values
    risk["FINAL_SCORE"] = blend(risk.RULE_SCORE.values, ml, np.zeros(len(risk)), wr, wm, wg)
    eligible = risk[(risk.FINAL_SCORE >= seed_thr * .5) | (risk.COMPLAINT_CNT >= 1)][["ACCOUNT_ID"]]
    session.sql(f"CREATE OR REPLACE TRANSIENT TABLE {DB}.ANALYTICS.TMP_ELIGIBLE (ACCOUNT_ID VARCHAR)").collect()
    write_df(session, eligible, f"{DB}.ANALYTICS.TMP_ELIGIBLE")
    transfers = to_pandas(session, f"""
        WITH anchor AS (SELECT MAX(TXN_TS) AS AS_OF FROM {DB}.CURATED.TXN_ENRICHED)
        SELECT t.SRC_ACCOUNT_ID AS SRC, t.DST_ACCOUNT_ID AS DST, SUM(t.AMOUNT) AS AMOUNT, COUNT(*) AS TXN_COUNT
        FROM {DB}.CURATED.TXN_ENRICHED t, anchor
        WHERE t.SRC_INTERNAL AND t.DST_INTERNAL AND t.TXN_TS > DATEADD('day', -30, anchor.AS_OF)
          AND t.SRC_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {DB}.ANALYTICS.TMP_ELIGIBLE)
          AND t.DST_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {DB}.ANALYTICS.TMP_ELIGIBLE)
        GROUP BY 1, 2""")
    device_pairs = to_pandas(session, f"""
        WITH d AS (SELECT DISTINCT DEVICE_ID, ACCOUNT_ID FROM {DB}.CURATED.DEVICE_EVENTS_FLAT
                   WHERE ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM {DB}.ANALYTICS.TMP_ELIGIBLE))
        SELECT d1.ACCOUNT_ID AS A, d2.ACCOUNT_ID AS B, d1.DEVICE_ID
        FROM d d1 JOIN d d2 ON d1.DEVICE_ID = d2.DEVICE_ID AND d1.ACCOUNT_ID < d2.ACCOUNT_ID""")
    rings, members, edges = build_rings(risk, transfers, device_pairs, seed_threshold=seed_thr)
    prev = to_pandas(session, f"""SELECT m.RING_ID, m.ACCOUNT_ID FROM {DB}.ANALYTICS.RING_MEMBERS m
                                  JOIN {DB}.ANALYTICS.CASES c ON c.RING_ID = m.RING_ID""")
    rings, members, edges = stabilise_ids(rings, members, edges, prev)
    now = pd.Timestamp.utcnow().tz_localize(None).floor("s")
    rings["DETECTED_AT"] = now
    members["DETECTED_AT"] = now

    # --- final blended score (graph evidence added)
    graph = members.merge(rings[["RING_ID", "RING_SCORE"]], on="RING_ID").groupby("ACCOUNT_ID").agg(
        GRAPH=("RING_SCORE", "max"), RID=("RING_ID", "first"))
    risk = risk.drop(columns=["RING_ID"]).merge(graph, left_on="ACCOUNT_ID", right_index=True, how="left")
    risk["GRAPH_SCORE"] = risk["GRAPH"].fillna(0.0)
    risk["RING_ID"] = risk["RID"]
    risk["FINAL_SCORE"] = np.round(blend(risk.RULE_SCORE.values, ml, risk.GRAPH_SCORE.values, wr, wm, wg), 2)
    risk["RISK_BAND"] = band(risk.FINAL_SCORE)
    risk["IS_ALERTED"] = risk.FINAL_SCORE >= alert_thr
    risk["SCORED_AT"] = now
    keep = ["ACCOUNT_ID", "CUSTOMER_ID", "REGION", "ACCOUNT_TYPE", "OCCUPATION", "ACCOUNT_AGE_DAYS", "TOTAL_IN_30D",
            "TOTAL_OUT_30D", "RULE_SCORE", "ML_SCORE", "GRAPH_SCORE", "FINAL_SCORE", "RISK_BAND", "RULES_HIT",
            "RULE_COUNT", "RING_ID", "IS_ALERTED", "SCORED_AT"]
    write_df(session, risk[keep], f"{DB}.ANALYTICS.ACCOUNT_RISK", overwrite=True)
    write_df(session, rings, f"{DB}.ANALYTICS.RINGS", overwrite=True)
    write_df(session, members, f"{DB}.ANALYTICS.RING_MEMBERS", overwrite=True)
    write_df(session, edges, f"{DB}.ANALYTICS.RING_EDGES", overwrite=True)

    created, updated = _upsert_cases(session, rings, members, risk, alert_thr, now)
    res = {"rings": int(len(rings)), "ring_members": int(len(members)), "alerted_accounts": int(risk.IS_ALERTED.sum()),
           "cases_created": created, "cases_updated": updated}
    audit(session, "RING_DETECTOR", "DETECT_RINGS_AND_CASES", details=res, started=t0)
    return res


def _upsert_cases(session, rings, members, risk, alert_thr, now):
    from common import DB, q
    open_cases = session.sql(f"""SELECT CASE_ID, RING_ID, PRIMARY_ACCOUNT_ID, CASE_TYPE FROM {DB}.ANALYTICS.CASES
                                 WHERE STATUS NOT LIKE 'CLOSED%'""").to_pandas()
    closed_keys = set(session.sql(f"""SELECT COALESCE(RING_ID, PRIMARY_ACCOUNT_ID) K FROM {DB}.ANALYTICS.CASES
                                      WHERE STATUS LIKE 'CLOSED%' AND CLOSED_AT > DATEADD('day', -30, CURRENT_TIMESTAMP())"""
                                  ).to_pandas().K)
    seq = int(session.sql(f"SELECT COUNT(*) FROM {DB}.ANALYTICS.CASES").collect()[0][0])
    open_ring = dict(zip(open_cases.RING_ID, open_cases.CASE_ID))
    open_acct = dict(zip(open_cases[open_cases.CASE_TYPE == "ACCOUNT"].PRIMARY_ACCOUNT_ID,
                         open_cases[open_cases.CASE_TYPE == "ACCOUNT"].CASE_ID))
    stmts, created, updated = [], 0, 0
    rk = risk.set_index("ACCOUNT_ID")
    for ring in rings.itertuples(index=False):
        mem = members[members.RING_ID == ring.RING_ID].ACCOUNT_ID
        top = rk.loc[mem].FINAL_SCORE.max()
        if ring.RING_SCORE < alert_thr and top < alert_thr:
            continue
        primary = rk.loc[mem].FINAL_SCORE.idxmax()
        if ring.RING_ID in open_ring:
            stmts.append(f"""UPDATE {DB}.ANALYTICS.CASES SET AMOUNT_AT_RISK = {ring.TOTAL_EXTERNAL_IN_30D},
                MEMBER_COUNT = {ring.MEMBER_COUNT}, TYPOLOGY = {q(ring.TYPOLOGY)}, UPDATED_AT = CURRENT_TIMESTAMP()
                WHERE CASE_ID = {q(open_ring[ring.RING_ID])}""")
            updated += 1
        elif ring.RING_ID not in closed_keys:
            seq += 1
            cid = f"MW-{now:%Y%m%d}-{seq:05d}"
            stmts.append(f"""INSERT INTO {DB}.ANALYTICS.CASES (CASE_ID, CASE_TYPE, RING_ID, PRIMARY_ACCOUNT_ID, REGION,
                TYPOLOGY, STATUS, AMOUNT_AT_RISK, MEMBER_COUNT, CREATED_AT, UPDATED_AT)
                VALUES ({q(cid)}, 'RING', {q(ring.RING_ID)}, {q(primary)}, {q(ring.PRIMARY_REGION)},
                {q(ring.TYPOLOGY)}, 'NEW', {ring.TOTAL_EXTERNAL_IN_30D}, {ring.MEMBER_COUNT},
                CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())""")
            created += 1
    solo = risk[risk.IS_ALERTED & risk.RING_ID.isna()]
    for a in solo.itertuples(index=False):
        if a.ACCOUNT_ID in open_acct or a.ACCOUNT_ID in closed_keys:
            continue
        seq += 1
        cid = f"MW-{now:%Y%m%d}-{seq:05d}"
        typ = "STRUCTURING" if "STRUCTURING" in (a.RULES_HIT or "") else (
            "ROUND_TRIPPING" if "CIRCULAR_FLOW" in (a.RULES_HIT or "") else "MULE_LAYERING")
        stmts.append(f"""INSERT INTO {DB}.ANALYTICS.CASES (CASE_ID, CASE_TYPE, RING_ID, PRIMARY_ACCOUNT_ID, REGION,
            TYPOLOGY, STATUS, AMOUNT_AT_RISK, MEMBER_COUNT, CREATED_AT, UPDATED_AT)
            VALUES ({q(cid)}, 'ACCOUNT', NULL, {q(a.ACCOUNT_ID)}, {q(a.REGION)}, {q(typ)}, 'NEW',
            {float(a.TOTAL_IN_30D or 0)}, 1, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())""")
        created += 1
    for s in stmts:
        session.sql(s).collect()
    return created, updated
