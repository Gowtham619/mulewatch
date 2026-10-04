"""MuleWatch - Agentic mule-ring investigator (Streamlit in Snowflake).

Runs inside Snowflake (Streamlit in Snowflake) or locally / on Streamlit Community Cloud with a
[connections.snowflake] block in .streamlit/secrets.toml.
"""
from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st

DB = "MULEWATCH"
st.set_page_config(page_title="MuleWatch", page_icon="🛡️", layout="wide")

ROLE_COLORS = {"COLLECTOR": "#f59e0b", "LAYER": "#a78bfa", "CASH_OUT": "#ef4444", "DEPOSITOR": "#f59e0b",
               "CONSOLIDATOR": "#ef4444", "CIRCULAR_PARTY": "#3b82f6", "ASSOCIATE": "#9ca3af", "SUBJECT": "#ef4444"}
PRIORITY_ICON = {"P1": "🔴", "P2": "🟠", "P3": "🟡"}


# ------------------------------------------------------------------ connection
@st.cache_resource
def get_session():
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session()
    except Exception:
        from snowflake.snowpark import Session
        return Session.builder.configs(dict(st.secrets["connections"]["snowflake"])).create()


session = get_session()


@st.cache_data(ttl=60, show_spinner=False)
def run(sql: str) -> pd.DataFrame:
    return session.sql(sql).to_pandas()


def run_safe(sql: str) -> pd.DataFrame:
    try:
        return run(sql)
    except Exception as e:
        st.caption(f"⚠️ {str(e)[:160]}")
        return pd.DataFrame()


def call(sql: str, params=None):
    res = session.sql(sql, params=params).collect() if params else session.sql(sql).collect()
    st.cache_data.clear()
    return res


def inr(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "-"
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:,.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:,.2f} L"
    return f"₹{x:,.0f}"


def as_obj(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return {}
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return {"raw": str(v)}


def has(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip() != ""


def viewer() -> str:
    for attr in ("user", "experimental_user"):
        try:
            u = getattr(st, attr)
            email = u.get("email") if hasattr(u, "get") else getattr(u, "email", None)
            if email:
                return email
        except Exception:
            pass
    return "fcu.analyst@arcadia.example"


# ------------------------------------------------------------------ sidebar
st.sidebar.markdown("## 🛡️ MuleWatch")
st.sidebar.caption("Agentic mule-ring investigator · Arcadia Bank FCU (synthetic data)")
PAGES = ["Command Center", "Case Queue", "Investigation", "Ask MuleWatch", "Model & Governance"]
if "page" not in st.session_state:
    st.session_state.page = PAGES[0]
page = st.sidebar.radio("Navigate", PAGES, index=PAGES.index(st.session_state.page), label_visibility="collapsed")
st.session_state.page = page

with st.sidebar.expander("⚙️ Live demo controls", expanded=False):
    if st.button("⚡ Inject a live mule-ring attack", use_container_width=True,
                 help="Appends live UPI traffic with a brand-new mule ring striking now"):
        with st.spinner("Simulating traffic..."):
            r = call(f"CALL {DB}.APP.SIMULATE_STREAM(1500, 1.0)")
        st.success(f"Injected: {as_obj(r[0][0]).get('injected_ring')}")
    n_inv = st.number_input("Cases to auto-investigate", 0, 10, 2)
    if st.button("▶️ Run detection pipeline", use_container_width=True,
                 help="Refresh features → score → rings → AI enrichment → triage → investigate → evaluate"):
        with st.spinner("Running the full agentic pipeline (1-4 min)..."):
            r = call(f"CALL {DB}.APP.RUN_FULL_PIPELINE({int(n_inv)})")
        st.success("Pipeline finished")
        st.json(as_obj(r[0][0]), expanded=False)
last = run_safe(f"""SELECT MAX(EVENT_TS) AS LAST_RUN FROM {DB}.GOV.AGENT_AUDIT_LOG WHERE ACTION = 'RUN_FULL_PIPELINE'""")
if len(last) and has(last["LAST_RUN"].iloc[0]):
    st.sidebar.caption(f"Last pipeline run: {pd.to_datetime(last['LAST_RUN'].iloc[0]):%d %b %H:%M} UTC")


# ================================================================== COMMAND CENTER
def page_command_center():
    st.title("Command Center")
    k = run_safe(f"""
        SELECT
          (SELECT COUNT(*) FROM {DB}.ANALYTICS.CASES WHERE STATUS NOT LIKE 'CLOSED%') OPEN_CASES,
          (SELECT COUNT(*) FROM {DB}.ANALYTICS.CASES WHERE STATUS NOT LIKE 'CLOSED%' AND PRIORITY = 'P1') P1,
          (SELECT SUM(AMOUNT_AT_RISK) FROM {DB}.ANALYTICS.CASES WHERE STATUS NOT LIKE 'CLOSED%') AT_RISK,
          (SELECT COUNT(*) FROM {DB}.ANALYTICS.RINGS) RINGS,
          (SELECT COUNT(*) FROM {DB}.ANALYTICS.ACCOUNT_RISK WHERE IS_ALERTED) ALERTED,
          (SELECT COUNT(*) FROM {DB}.ANALYTICS.STR_DRAFTS WHERE STATUS = 'DRAFT') STR_PENDING""")
    ev = run_safe(f"""SELECT DETECTOR, PRECISION, RECALL FROM {DB}.ANALYTICS.EVAL_RESULTS
                      QUALIFY RUN_TS = MAX(RUN_TS) OVER ()""")
    if len(k):
        r = k.iloc[0]
        c = st.columns(6)
        c[0].metric("Open cases", int(r.OPEN_CASES))
        c[1].metric("🔴 P1 cases", int(r.P1))
        c[2].metric("Funds at risk (30d)", inr(r.AT_RISK))
        c[3].metric("Mule rings detected", int(r.RINGS))
        c[4].metric("Alerted accounts", int(r.ALERTED))
        c[5].metric("STRs awaiting approval", int(r.STR_PENDING))
    if len(ev):
        b = ev.set_index("DETECTOR")
        c = st.columns(4)
        if "BLENDED_ALERTS" in b.index:
            c[0].metric("Detection precision", f"{b.loc['BLENDED_ALERTS', 'PRECISION']:.0%}",
                        delta=f"{b.loc['BLENDED_ALERTS', 'PRECISION'] - b.loc['RULES_ONLY', 'PRECISION']:+.0%} vs rules only"
                        if "RULES_ONLY" in b.index else None)
            c[1].metric("Detection recall", f"{b.loc['BLENDED_ALERTS', 'RECALL']:.0%}")
        if "RING_RECOVERY" in b.index:
            c[2].metric("Held-out rings recovered", f"{b.loc['RING_RECOVERY', 'RECALL']:.0%}")
            c[3].metric("Ring purity", f"{b.loc['RING_RECOVERY', 'PRECISION']:.0%}")
        st.caption("Measured on held-out synthetic rings never used for training (ANALYTICS.EVAL_RESULTS).")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Money movement (last 30 days)")
        d = run_safe(f"""SELECT TXN_DATE, CHANNEL, SUM(TOTAL_AMOUNT) AMT FROM {DB}.CURATED.DAILY_CHANNEL_STATS
                         WHERE TXN_DATE >= DATEADD('day', -30, (SELECT MAX(TXN_DATE) FROM {DB}.CURATED.DAILY_CHANNEL_STATS))
                         GROUP BY 1, 2 ORDER BY 1""")
        if len(d):
            st.altair_chart(alt.Chart(d).mark_area(opacity=.85).encode(
                x=alt.X("TXN_DATE:T", title=None), y=alt.Y("AMT:Q", title="INR", stack=True),
                color=alt.Color("CHANNEL:N", legend=alt.Legend(orient="bottom")),
                tooltip=["TXN_DATE:T", "CHANNEL", alt.Tooltip("AMT:Q", format=",.0f")]).properties(height=260),
                use_container_width=True)
    with right:
        st.subheader("Victim complaints by scam type")
        cp = run_safe(f"""SELECT SCAM_TYPE, COUNT(*) N, SUM(AMOUNT) AMT FROM {DB}.ANALYTICS.V_COMPLAINTS
                          GROUP BY 1 ORDER BY 2 DESC""")
        if len(cp):
            st.altair_chart(alt.Chart(cp).mark_bar().encode(
                x=alt.X("N:Q", title="complaints"), y=alt.Y("SCAM_TYPE:N", sort="-x", title=None),
                tooltip=["SCAM_TYPE", "N", alt.Tooltip("AMT:Q", format=",.0f")]).properties(height=260),
                use_container_width=True)

    st.subheader("Detected rings")
    rg = run_safe(f"""SELECT r.RING_ID, r.TYPOLOGY, r.MEMBER_COUNT, r.RING_SCORE, r.TOTAL_EXTERNAL_IN_30D,
                             r.CASH_OUT_30D, r.VDA_OUT_30D, r.COMPLAINT_CNT, r.SHARED_DEVICES, r.PRIMARY_REGION,
                             c.CASE_ID, c.PRIORITY, c.STATUS
                      FROM {DB}.ANALYTICS.RINGS r LEFT JOIN {DB}.ANALYTICS.CASES c ON c.RING_ID = r.RING_ID
                      ORDER BY r.RING_SCORE DESC, r.TOTAL_EXTERNAL_IN_30D DESC""")
    if len(rg):
        st.dataframe(rg, use_container_width=True, hide_index=True, column_config={
            "RING_SCORE": st.column_config.ProgressColumn("Ring score", min_value=0, max_value=100, format="%.0f"),
            "TOTAL_EXTERNAL_IN_30D": st.column_config.NumberColumn("Inflow 30d (₹)", format="%.0f"),
            "CASH_OUT_30D": st.column_config.NumberColumn("Cash-out (₹)", format="%.0f"),
            "VDA_OUT_30D": st.column_config.NumberColumn("Crypto (₹)", format="%.0f")})
    else:
        st.info("No rings yet - run the detection pipeline from the sidebar.")


# ================================================================== CASE QUEUE
def page_queue():
    st.title("Case Queue")
    c1, c2, c3, c4 = st.columns(4)
    status = c1.multiselect("Status", ["NEW", "TRIAGED", "INVESTIGATED", "PENDING_REVIEW", "ESCALATED",
                                       "CLOSED_STR_FILED", "CLOSED_FALSE_POSITIVE"],
                            default=["NEW", "TRIAGED", "INVESTIGATED", "PENDING_REVIEW", "ESCALATED"])
    prio = c2.multiselect("Priority", ["P1", "P2", "P3"], default=["P1", "P2", "P3"])
    typ = c3.multiselect("Typology", ["MULE_LAYERING", "STRUCTURING", "ROUND_TRIPPING"],
                         default=["MULE_LAYERING", "STRUCTURING", "ROUND_TRIPPING"])
    ctype = c4.multiselect("Type", ["RING", "ACCOUNT"], default=["RING", "ACCOUNT"])
    df = run_safe(f"""SELECT CASE_ID, PRIORITY, STATUS, CASE_TYPE, TYPOLOGY, REGION, MEMBER_COUNT, AMOUNT_AT_RISK,
                             PRIORITY_SCORE, CONFIDENCE, ASSIGNED_TO, SLA_DUE_AT, CREATED_AT, TRIAGE_RATIONALE
                      FROM {DB}.ANALYTICS.CASES ORDER BY PRIORITY_SCORE DESC NULLS LAST, AMOUNT_AT_RISK DESC""")
    if df.empty:
        st.info("No cases yet.")
        return
    df["PRIORITY"] = df["PRIORITY"].fillna("—")
    f = df[df.STATUS.isin(status) & (df.PRIORITY.isin(prio) | (df.PRIORITY == "—")) & df.TYPOLOGY.isin(typ)
           & df.CASE_TYPE.isin(ctype)]
    st.caption(f"{len(f)} cases · {inr(f.AMOUNT_AT_RISK.sum())} at risk")
    f = f.assign(P=f.PRIORITY.map(lambda p: f"{PRIORITY_ICON.get(p, '⚪')} {p}"))
    st.dataframe(f[["CASE_ID", "P", "STATUS", "CASE_TYPE", "TYPOLOGY", "REGION", "MEMBER_COUNT", "AMOUNT_AT_RISK",
                    "PRIORITY_SCORE", "CONFIDENCE", "ASSIGNED_TO", "SLA_DUE_AT"]],
                 use_container_width=True, hide_index=True, column_config={
                     "AMOUNT_AT_RISK": st.column_config.NumberColumn("At risk (₹)", format="%.0f"),
                     "PRIORITY_SCORE": st.column_config.ProgressColumn("Priority", min_value=0, max_value=100, format="%.0f"),
                     "CONFIDENCE": st.column_config.NumberColumn("AI confidence", format="%.2f")})
    sel = st.selectbox("Open case", f.CASE_ID.tolist())
    if sel:
        row = f[f.CASE_ID == sel].iloc[0]
        if has(row.TRIAGE_RATIONALE):
            st.info(f"**Triage agent:** {row.TRIAGE_RATIONALE}")
        if st.button("🔎 Open investigation workspace", type="primary"):
            st.session_state.case_id = sel
            st.session_state.page = "Investigation"
            st.rerun()


# ================================================================== INVESTIGATION
def ring_dot(case: pd.Series) -> str | None:
    if not has(case.RING_ID):
        return None
    mem = run(f"""SELECT m.ACCOUNT_ID, m.ROLE, m.IN_30D, m.OUT_30D, r.FINAL_SCORE, f.CASH_OUT_30D, f.VDA_OUT_30D,
                         f.COMPLAINT_CNT
                  FROM {DB}.ANALYTICS.RING_MEMBERS m JOIN {DB}.ANALYTICS.ACCOUNT_RISK r USING (ACCOUNT_ID)
                  JOIN {DB}.CURATED.ACCOUNT_FEATURES f USING (ACCOUNT_ID)
                  WHERE m.RING_ID = '{case.RING_ID}'""")
    edges = run(f"""SELECT SRC, DST, EDGE_TYPE, AMOUNT, TXN_COUNT FROM {DB}.ANALYTICS.RING_EDGES
                    WHERE RING_ID = '{case.RING_ID}'""")
    lines = ["digraph G {", "rankdir=LR; bgcolor=transparent;",
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, fontcolor="#111111"];',
             'edge [fontname="Helvetica", fontsize=9, color="#6b7280", fontcolor="#6b7280"];',
             '"VICTIMS" [label="External remitters\\n(victims)", shape=ellipse, fillcolor="#e5e7eb"];']
    any_cash = any_vda = False
    for m in mem.itertuples(index=False):
        color = ROLE_COLORS.get(m.ROLE, "#9ca3af")
        comp = f"\\n⚠ {int(m.COMPLAINT_CNT)} complaint(s)" if m.COMPLAINT_CNT else ""
        lines.append(f'"{m.ACCOUNT_ID}" [label="{m.ACCOUNT_ID}\\n{m.ROLE}\\nin {inr(m.IN_30D)} · score {m.FINAL_SCORE:.0f}{comp}", '
                     f'fillcolor="{color}"];')
        internal_in = edges[(edges.DST == m.ACCOUNT_ID) & (edges.EDGE_TYPE == "TRANSFER")].AMOUNT.sum()
        if m.IN_30D - internal_in > 10_000:
            lines.append(f'"VICTIMS" -> "{m.ACCOUNT_ID}" [label="{inr(m.IN_30D - internal_in)}", penwidth=1.5];')
        if (m.CASH_OUT_30D or 0) > 0:
            any_cash = True
            lines.append(f'"{m.ACCOUNT_ID}" -> "CASH" [label="{inr(m.CASH_OUT_30D)}", color="#ef4444"];')
        if (m.VDA_OUT_30D or 0) > 0:
            any_vda = True
            lines.append(f'"{m.ACCOUNT_ID}" -> "VDA" [label="{inr(m.VDA_OUT_30D)}", color="#8b5cf6"];')
    if any_cash:
        lines.append('"CASH" [label="ATM / cash", shape=cylinder, fillcolor="#fecaca"];')
    if any_vda:
        lines.append('"VDA" [label="Crypto exchange", shape=cylinder, fillcolor="#ddd6fe"];')
    for e in edges.itertuples(index=False):
        if e.EDGE_TYPE == "TRANSFER":
            lines.append(f'"{e.SRC}" -> "{e.DST}" [label="{inr(e.AMOUNT)} ({int(e.TXN_COUNT)})", penwidth=2];')
        else:
            lines.append(f'"{e.SRC}" -> "{e.DST}" [style=dashed, dir=none, color="#2563eb", label="same device"];')
    lines.append("}")
    return "\n".join(lines)


def page_investigation():
    st.title("Investigation workspace")
    ids = run_safe(f"SELECT CASE_ID FROM {DB}.ANALYTICS.CASES ORDER BY PRIORITY_SCORE DESC NULLS LAST")
    if ids.empty:
        st.info("No cases yet.")
        return
    opts = ids.CASE_ID.tolist()
    default = st.session_state.get("case_id", opts[0])
    case_id = st.selectbox("Case", opts, index=opts.index(default) if default in opts else 0)
    st.session_state.case_id = case_id
    case = run(f"SELECT * FROM {DB}.ANALYTICS.CASES WHERE CASE_ID = '{case_id}'").iloc[0]

    h = st.columns([3, 1, 1, 1, 1])
    h[0].markdown(f"### {PRIORITY_ICON.get(case.PRIORITY, '⚪')} {case.CASE_ID} · {case.TYPOLOGY}")
    h[1].metric("Status", case.STATUS)
    h[2].metric("At risk", inr(case.AMOUNT_AT_RISK))
    h[3].metric("Accounts", int(case.MEMBER_COUNT or 1))
    h[4].metric("AI confidence", "-" if pd.isna(case.CONFIDENCE) else f"{case.CONFIDENCE:.0%}")
    if has(case.TRIAGE_RATIONALE):
        st.info(f"**Triage agent:** {case.TRIAGE_RATIONALE}")

    a1, a2, _ = st.columns([1, 1, 3])
    if a1.button("🕵️ Run investigator agent", use_container_width=True):
        with st.spinner("Collecting evidence from 8 sources and reasoning..."):
            call(f"CALL {DB}.APP.INVESTIGATOR_AGENT('{case_id}')")
        st.rerun()
    if a2.button("📝 Draft STR (compliance agent)", use_container_width=True):
        with st.spinner("Retrieving policy with Cortex Search, drafting and self-checking the STR..."):
            call(f"CALL {DB}.APP.COMPLIANCE_AGENT('{case_id}')")
        st.rerun()

    t_graph, t_find, t_ev, t_str = st.tabs(["🕸️ Network", "🧠 AI findings", "📂 Evidence", "📝 STR draft"])
    with t_graph:
        dot = ring_dot(case)
        if dot:
            st.graphviz_chart(dot, use_container_width=True)
            st.caption("Orange = collector · purple = layer · red = cash-out · blue dashed = shared device")
        else:
            st.write(f"Single-account case: **{case.PRIMARY_ACCOUNT_ID}**")
        tl = run_safe(f"""
            WITH m AS (SELECT ACCOUNT_ID FROM {DB}.ANALYTICS.RING_MEMBERS WHERE RING_ID = '{case.RING_ID if has(case.RING_ID) else ''}'
                       UNION SELECT '{case.PRIMARY_ACCOUNT_ID}')
            SELECT TXN_TS, CHANNEL, AMOUNT, SRC_ACCOUNT_ID, DST_ACCOUNT_ID,
                   IFF(DST_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM m), 'credit', 'debit') AS LEG
            FROM {DB}.CURATED.TXN_ENRICHED
            WHERE (SRC_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM m) OR DST_ACCOUNT_ID IN (SELECT ACCOUNT_ID FROM m))
              AND AMOUNT >= 2000
              AND TXN_TS > DATEADD('day', -30, (SELECT MAX(TXN_TS) FROM {DB}.CURATED.TXN_ENRICHED))""")
        if len(tl):
            st.markdown("**Money timeline (30 days)**")
            st.altair_chart(alt.Chart(tl).mark_circle(opacity=.75).encode(
                x=alt.X("TXN_TS:T", title=None), y=alt.Y("AMOUNT:Q", scale=alt.Scale(type="log"), title="INR (log)"),
                color=alt.Color("CHANNEL:N"), shape="LEG:N", size=alt.value(60),
                tooltip=["TXN_TS:T", "CHANNEL", "LEG", alt.Tooltip("AMOUNT:Q", format=",.0f"), "SRC_ACCOUNT_ID",
                         "DST_ACCOUNT_ID"]).properties(height=260).interactive(), use_container_width=True)

    with t_find:
        s = as_obj(case.INVESTIGATION_SUMMARY)
        if not s:
            st.info("Not investigated yet - click **Run investigator agent**.")
        else:
            st.markdown(f"#### {s.get('headline', '')}")
            st.write(s.get("hypothesis", ""))
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Key findings**")
                for x in s.get("key_findings", []) or []:
                    st.markdown(f"- {x}")
                st.markdown("**Exculpatory factors**")
                for x in s.get("exculpatory_factors", []) or ["None identified"]:
                    st.markdown(f"- {x}")
            with c2:
                st.markdown("**Recommended actions**")
                for x in s.get("recommended_actions", []) or []:
                    st.markdown(f"- {x}")
                st.markdown("**Information gaps**")
                for x in s.get("information_gaps", []) or []:
                    st.markdown(f"- {x}")

    with t_ev:
        ev = run_safe(f"""SELECT EVIDENCE_TYPE, TITLE, PAYLOAD, CREATED_AT FROM {DB}.ANALYTICS.CASE_EVIDENCE
                          WHERE CASE_ID = '{case_id}' ORDER BY EVIDENCE_TYPE""")
        if ev.empty:
            st.info("Evidence is collected by the investigator agent.")
        for e in ev.itertuples(index=False):
            p = as_obj(e.PAYLOAD)
            with st.expander(e.TITLE, expanded=e.EVIDENCE_TYPE in ("MONEY_TRAIL", "MEMBERS")):
                if e.EVIDENCE_TYPE == "MEMBERS" and isinstance(p, list):
                    st.dataframe(pd.DataFrame(p), use_container_width=True, hide_index=True)
                elif e.EVIDENCE_TYPE == "MONEY_TRAIL" and isinstance(p, dict):
                    c = st.columns(4)
                    c[0].metric("External credits", p.get("external_credits"))
                    c[1].metric("From remitters", p.get("distinct_external_remitters"))
                    c[2].metric("Cash withdrawn", inr(p.get("cash_withdrawn")))
                    dwell = p.get("median_dwell_minutes")
                    c[3].metric("Median dwell", "-" if dwell is None else f"{dwell:.0f} min")
                    if p.get("top_internal_flows"):
                        st.dataframe(pd.DataFrame(p["top_internal_flows"]), use_container_width=True, hide_index=True)
                else:
                    st.json(p, expanded=True)

    with t_str:
        dr = run_safe(f"""SELECT * FROM {DB}.ANALYTICS.STR_DRAFTS WHERE CASE_ID = '{case_id}'
                          ORDER BY VERSION DESC LIMIT 1""")
        if dr.empty:
            st.info("No STR draft yet - click **Draft STR**.")
        else:
            d = dr.iloc[0]
            draft = as_obj(d.DRAFT)
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Version", int(d.VERSION))
            q2.metric("Citation validity", f"{d.CITATION_VALIDITY:.0%}" if pd.notna(d.CITATION_VALIDITY) else "-")
            q3.metric("Faithfulness (AI judge)", f"{d.FAITHFULNESS_SCORE:.0f}/5" if pd.notna(d.FAITHFULNESS_SCORE) else "-")
            q4.metric("Status", d.STATUS)
            st.caption(f"Model: {d.MODEL} · QA: {d.JUDGE_NOTES}")
            st.markdown("**Summary of suspicion**")
            st.write(draft.get("summary_of_suspicion", ""))
            g = draft.get("grounds_of_suspicion") or []
            if g:
                st.markdown("**Grounds of suspicion**")
                st.dataframe(pd.DataFrame(g), use_container_width=True, hide_index=True)
            edited = st.text_area("Narrative (editable before approval)", d.NARRATIVE or "", height=320)
            cites = as_obj(d.CITATIONS) or []
            if cites:
                with st.expander(f"📚 Policy sources cited ({len(cites)})"):
                    ids = ",".join(f"'{c}'" for c in cites if isinstance(c, str))
                    docs = run_safe(f"SELECT CHUNK_ID, TITLE, SECTION, CHUNK_TEXT FROM {DB}.RAW.DOCS WHERE CHUNK_ID IN ({ids})") \
                        if ids else pd.DataFrame()
                    for x in docs.itertuples(index=False):
                        st.markdown(f"**[{x.CHUNK_ID}] {x.TITLE} — {x.SECTION}**")
                        st.caption(x.CHUNK_TEXT[:800])
            if edited != (d.NARRATIVE or "") and st.button("💾 Save narrative edits"):
                call(f"UPDATE {DB}.ANALYTICS.STR_DRAFTS SET NARRATIVE = ?, STATUS = 'EDITED' "
                     f"WHERE CASE_ID = ? AND VERSION = ?", params=[edited, case_id, int(d.VERSION)])
                st.success("Saved")
            st.divider()
            st.markdown("**Human decision** (required - the AI never files on its own)")
            reviewer = st.text_input("Reviewer", viewer())
            notes = st.text_input("Notes", "")
            b = st.columns(4)
            actions = [("✅ Approve & file STR", "STR_FILED"), ("🚔 Escalate to LEA", "ESCALATED_LEA"),
                       ("❎ False positive", "FALSE_POSITIVE"), ("↩️ Needs more info", "NEEDS_MORE_INFO")]
            for col, (label, disp) in zip(b, actions):
                if col.button(label, use_container_width=True):
                    call(f"CALL {DB}.APP.RECORD_DISPOSITION(?, ?, ?, ?)", params=[case_id, disp, reviewer, notes])
                    st.success(f"Recorded {disp}. Outcome feeds the learning loop (rule weights + model).")
                    st.rerun()


# ================================================================== ASK MULEWATCH
def extract_agent(resp: dict):
    texts, sqls, tables = [], [], []

    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "text" and isinstance(o.get("text"), str):
                texts.append(o["text"])
            if isinstance(o.get("sql"), str):
                sqls.append(o["sql"])
            rs = o.get("result_set") or o.get("resultSet")
            if isinstance(rs, dict) and rs.get("data"):
                cols = [c.get("name") for c in (rs.get("resultSetMetaData", {}).get("rowType") or [])]
                try:
                    tables.append(pd.DataFrame(rs["data"], columns=cols or None))
                except Exception:
                    pass
            for k, v in o.items():
                if k != "text":
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(resp.get("content", resp))
    return "\n\n".join(texts).strip(), sqls, tables


def page_ask():
    st.title("Ask MuleWatch")
    st.caption("Cortex Agent orchestrating Cortex Analyst (semantic view), Cortex Search (policies, media, complaints) "
               "and custom case tools.")
    if "chat" not in st.session_state:
        st.session_state.chat = []
    examples = ["How many open cases do we have by priority and what is the amount at risk?",
                "Which rings withdrew the most cash in the last 30 days?",
                "According to our policy, what are the red flags of a money mule account?",
                "Show complaints about digital arrest scams",
                "What is the STR filing timeline once suspicion is concluded?"]
    ex = st.columns(len(examples))
    clicked = None
    for c, e in zip(ex, examples):
        if c.button(e[:42] + "…", help=e, use_container_width=True):
            clicked = e
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            for s in m.get("sql", []):
                with st.expander("SQL generated by Cortex Analyst"):
                    st.code(s, language="sql")
    prompt = st.chat_input("Ask about cases, rings, accounts, complaints or policy…") or clicked
    if not prompt:
        return
    st.session_state.chat.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    msgs = [{"role": m["role"], "content": [{"type": "text", "text": m["text"]}]} for m in st.session_state.chat[-8:]]
    body = json.dumps({"messages": msgs})
    with st.chat_message("assistant"):
        with st.spinner("Thinking with tools…"):
            try:
                raw = session.sql(f"SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN('{DB}.AI.MULEWATCH_COPILOT', ?)",
                                  params=[body]).collect()[0][0]
                text, sqls, tables = extract_agent(as_obj(raw))
            except Exception as e:
                st.caption(f"Agent unavailable ({str(e)[:120]}); using fallback.")
                text = session.sql(f"CALL {DB}.APP.ASK_FALLBACK(?)", params=[prompt]).collect()[0][0]
                sqls, tables = [], []
        st.markdown(text or "_No answer returned._")
        for s in sqls:
            with st.expander("SQL generated by Cortex Analyst"):
                st.code(s, language="sql")
        for t in tables[:2]:
            st.dataframe(t, use_container_width=True, hide_index=True)
    st.session_state.chat.append({"role": "assistant", "text": text or "", "sql": sqls})


# ================================================================== MODEL & GOVERNANCE
def page_governance():
    st.title("Model, learning loop & governance")
    t1, t2, t3, t4 = st.tabs(["📈 Detection quality", "🔁 Learning loop", "🧾 AI audit trail", "🔐 Data governance"])
    with t1:
        ev = run_safe(f"SELECT * FROM {DB}.ANALYTICS.EVAL_RESULTS ORDER BY RUN_TS")
        if len(ev):
            latest = ev[ev.RUN_TS == ev.RUN_TS.max()]
            st.dataframe(latest[["DETECTOR", "PRECISION", "RECALL", "F1", "TP", "FP", "FN", "ALERTED"]],
                         use_container_width=True, hide_index=True)
            lm = latest.melt(id_vars="DETECTOR", value_vars=["PRECISION", "RECALL"])
            st.altair_chart(alt.Chart(lm).mark_bar().encode(
                x=alt.X("DETECTOR:N", title=None), xOffset="variable:N", y=alt.Y("value:Q", scale=alt.Scale(domain=[0, 1])),
                color="variable:N", tooltip=["DETECTOR", "variable", alt.Tooltip("value:Q", format=".2f")]
            ).properties(height=260), use_container_width=True)
            st.caption("RULES_ONLY is the legacy rule-based baseline; BLENDED adds the ML model and ring graph.")
        ml = run_safe(f"SELECT * FROM {DB}.ANALYTICS.MODEL_LOG ORDER BY TRAINED_AT DESC")
        if len(ml):
            st.markdown("**Model versions** (Snowflake Model Registry: `ANALYTICS.MULE_CLASSIFIER`)")
            st.dataframe(ml, use_container_width=True, hide_index=True)
    with t2:
        st.markdown("Analyst decisions become labels → rule weights are re-tuned and the model retrained (task `MW_LEARN`).")
        cfg = run_safe(f"""SELECT RULE_ID, RULE_NAME, FEATURE, OPERATOR, THRESHOLD, BASE_WEIGHT, WEIGHT, DESCRIPTION
                           FROM {DB}.ANALYTICS.RULE_CONFIG ORDER BY RULE_ID""")
        st.dataframe(cfg, use_container_width=True, hide_index=True)
        fb = run_safe(f"""SELECT DISPOSITION, COUNT(*) N FROM {DB}.ANALYTICS.CASE_FEEDBACK GROUP BY 1""")
        if len(fb):
            st.dataframe(fb, hide_index=True)
        if st.button("Run learning loop now"):
            with st.spinner("Re-tuning rules and retraining the model…"):
                r = call(f"CALL {DB}.APP.LEARN_FROM_FEEDBACK()")
            st.json(as_obj(r[0][0]), expanded=False)
        hist = run_safe(f"SELECT * FROM {DB}.ANALYTICS.RULE_CONFIG_HISTORY ORDER BY CHANGED_AT DESC LIMIT 50")
        if len(hist):
            st.dataframe(hist, use_container_width=True, hide_index=True)
    with t3:
        au = run_safe(f"""SELECT EVENT_TS, AGENT, ACTION, CASE_ID, MODEL, STATUS, DURATION_MS, INVOKED_BY, DETAILS
                          FROM {DB}.GOV.AGENT_AUDIT_LOG ORDER BY EVENT_TS DESC LIMIT 200""")
        st.dataframe(au, use_container_width=True, hide_index=True)
    with t4:
        st.markdown("""
- **Dynamic masking** (tag `GOV.PII` → policy `GOV.MASK_PII`): analysts see `A***** Sharma`, `XXXXXX1234`.
- **Row access policy** `GOV.RAP_REGION`: analysts see only cases/accounts of their mapped regions.
- **Ground truth** (`GOV.GROUND_TRUTH`) is never read by detection code and not granted to analysts.
- **Human-in-the-loop**: STRs are drafts until a named reviewer approves; every AI action is logged.
""")
        pol = run_safe(f"""SELECT POLICY_NAME, POLICY_KIND, REF_ENTITY_NAME, REF_COLUMN_NAME, TAG_NAME
                           FROM TABLE({DB}.INFORMATION_SCHEMA.POLICY_REFERENCES(
                             REF_ENTITY_NAME => '{DB}.RAW.CUSTOMERS', REF_ENTITY_DOMAIN => 'table'))
                           UNION ALL
                           SELECT POLICY_NAME, POLICY_KIND, REF_ENTITY_NAME, REF_COLUMN_NAME, TAG_NAME
                           FROM TABLE({DB}.INFORMATION_SCHEMA.POLICY_REFERENCES(
                             REF_ENTITY_NAME => '{DB}.ANALYTICS.CASES', REF_ENTITY_DOMAIN => 'table'))""")
        if len(pol):
            st.dataframe(pol, use_container_width=True, hide_index=True)


{"Command Center": page_command_center, "Case Queue": page_queue, "Investigation": page_investigation,
 "Ask MuleWatch": page_ask, "Model & Governance": page_governance}[page]()
