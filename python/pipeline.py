"""Orchestration entry points: document loader and the end-to-end pipeline."""
from __future__ import annotations

import re
import time

import pandas as pd

from common import DB, audit, write_df

DOC_STAGE = f"@{DB}.RAW.DOCS_STAGE"


def chunk_markdown(text: str, source: str, max_chars: int = 1800) -> list[dict]:
    """Split a policy markdown file into section chunks. Header lines:
         # Title
         doc_id: POL-01
         doc_type: POLICY"""
    title = re.search(r"^#\s+(.+)$", text, re.M)
    doc_id = re.search(r"^doc_id:\s*(\S+)", text, re.M)
    doc_type = re.search(r"^doc_type:\s*(\S+)", text, re.M)
    title = title.group(1).strip() if title else source
    doc_id = doc_id.group(1).strip() if doc_id else source.split(".")[0].upper()
    doc_type = doc_type.group(1).strip() if doc_type else "POLICY"
    parts = re.split(r"^##\s+", text, flags=re.M)
    chunks, n = [], 0
    for p in parts[1:]:
        section, _, body = p.partition("\n")
        body = body.strip()
        paras, buf = body.split("\n\n"), ""
        for para in paras + [None]:
            if para is None or (len(buf) + len(para) > max_chars and buf):
                if buf.strip():
                    n += 1
                    chunks.append(dict(DOC_ID=doc_id, CHUNK_ID=f"{doc_id}#{n}", TITLE=title, DOC_TYPE=doc_type,
                                       SECTION=section.strip(), CHUNK_TEXT=f"{title} - {section.strip()}\n{buf.strip()}",
                                       SOURCE_FILE=source))
                buf = ""
            if para is not None:
                buf += para + "\n\n"
    return chunks


def load_docs_sp(session) -> dict:
    """CALL MULEWATCH.APP.LOAD_DOCS()  - reads every .md on RAW.DOCS_STAGE and chunks it into RAW.DOCS."""
    t0 = time.time()
    files = [r["name"] for r in session.sql(f"LIST {DOC_STAGE}").collect() if r["name"].endswith(".md")]
    chunks = []
    for f in files:
        rel = f.split("/", 1)[1] if "/" in f else f
        try:
            with session.file.get_stream(f"{DOC_STAGE}/{rel}") as fh:
                text = fh.read().decode("utf-8")
        except Exception:  # fallback: read the whole file through SQL
            text = session.sql(f"SELECT $1 FROM {DOC_STAGE}/{rel} "
                               f"(FILE_FORMAT => '{DB}.RAW.FF_WHOLE_FILE')").collect()[0][0]
        chunks += chunk_markdown(text, rel.split("/")[-1])
    df = pd.DataFrame(chunks)
    df["LOADED_AT"] = pd.Timestamp.utcnow().tz_localize(None).floor("s")
    write_df(session, df, f"{DB}.RAW.DOCS", overwrite=True)
    res = {"files": len(files), "chunks": len(df)}
    audit(session, "DOC_LOADER", "LOAD_DOCS", details=res, started=t0)
    return res


def run_full_pipeline_sp(session, investigate_top_n: int = 3) -> dict:
    """CALL MULEWATCH.APP.RUN_FULL_PIPELINE(3)
    refresh features -> score -> rings & cases -> AI enrichment -> triage -> investigate+STR -> evaluate"""
    import agents
    import enrich
    import evaluate
    import rings
    import scoring
    t0 = time.time()
    out = {}
    for dt in ("ACCOUNT_FEATURES", "DAILY_CHANNEL_STATS"):
        try:
            session.sql(f"ALTER DYNAMIC TABLE {DB}.CURATED.{dt} REFRESH").collect()
        except Exception as e:
            out[f"refresh_{dt}"] = str(e)[:200]
    steps = [("score", lambda: scoring.score_accounts_sp(session)),
             ("rings", lambda: rings.detect_rings_sp(session)),
             ("enrich", lambda: enrich.enrich_sp(session)),
             ("triage", lambda: agents.triage_sp(session)),
             ("investigate", lambda: agents.run_investigations_sp(session, int(investigate_top_n))),
             ("evaluate", lambda: evaluate.evaluate_sp(session))]
    for name, fn in steps:
        s = time.time()
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = {"error": str(e)[:500]}
            audit(session, "ORCHESTRATOR", f"STEP_FAILED_{name.upper()}", status="ERROR", details={"error": str(e)[:1000]})
        out[name + "_seconds"] = round(time.time() - s, 1)
    audit(session, "ORCHESTRATOR", "RUN_FULL_PIPELINE", details={"steps": list(out)}, started=t0)
    return out
