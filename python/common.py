"""Shared helpers for MuleWatch stored procedures.

Every procedure imports this module (see sql/02_procedures.sql IMPORTS).
Nothing here talks to anything outside Snowflake.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Iterable

DB = "MULEWATCH"


# ----------------------------------------------------------------------------
# SQL helpers
# ----------------------------------------------------------------------------
def q(value: Any) -> str:
    """Render a Python value as a safe SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


def dollar(text: str) -> str:
    """Wrap free text in a $$ literal (strip any $$ inside)."""
    return "$$" + str(text).replace("$$", "$ $") + "$$"


def ident_list(values: Iterable[str]) -> str:
    vals = [q(v) for v in values]
    return ", ".join(vals) if vals else "NULL"


def rows(session, sql: str) -> list:
    return session.sql(sql).collect()


def scalar(session, sql: str, default=None):
    r = session.sql(sql).collect()
    if not r:
        return default
    v = r[0][0]
    return default if v is None else v


def to_pandas(session, sql: str):
    return session.sql(sql).to_pandas()


def write_df(session, pdf, fq_table: str, overwrite: bool = False) -> int:
    """Write a pandas frame into an EXISTING table (column names upper-cased)."""
    if pdf is None or len(pdf) == 0:
        if overwrite:
            session.sql(f"TRUNCATE TABLE {fq_table}").collect()
        return 0
    pdf = pdf.copy()
    pdf.columns = [c.upper() for c in pdf.columns]
    db, schema, table = fq_table.split(".")
    kwargs = dict(table_name=table, database=db, schema=schema,
                  quote_identifiers=False, auto_create_table=False, overwrite=overwrite)
    try:
        session.write_pandas(pdf, use_logical_type=True, **kwargs)
    except TypeError:  # older snowpark without use_logical_type
        session.write_pandas(pdf, **kwargs)
    return len(pdf)


def write_variant(session, records: list, fq_table: str, col: str, ts_col: str | None = None,
                  overwrite: bool = False, batch: int = 5000) -> int:
    """Load python dicts into a VARIANT column via a temp JSON staging table."""
    import pandas as pd
    if overwrite:
        session.sql(f"TRUNCATE TABLE {fq_table}").collect()
    if not records:
        return 0
    tmp = f"{DB}.RAW.TMP_VARIANT_LOAD"
    session.sql(f"CREATE OR REPLACE TRANSIENT TABLE {tmp} (J VARCHAR)").collect()
    for i in range(0, len(records), batch):
        chunk = pd.DataFrame({"J": [json.dumps(r, default=str) for r in records[i:i + batch]]})
        write_df(session, chunk, tmp)
    cols = f"{col}" + (f", {ts_col}" if ts_col else "")
    vals = "PARSE_JSON(J)" + (", CURRENT_TIMESTAMP()" if ts_col else "")
    session.sql(f"INSERT INTO {fq_table} ({cols}) SELECT {vals} FROM {tmp}").collect()
    session.sql(f"DROP TABLE IF EXISTS {tmp}").collect()
    return len(records)


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
def get_config(session, key: str, default: str | None = None) -> str | None:
    v = scalar(session, f"SELECT VALUE FROM {DB}.APP.CONFIG WHERE KEY = {q(key)}")
    return default if v in (None, "") else v


def set_config(session, key: str, value: str) -> None:
    session.sql(
        f"MERGE INTO {DB}.APP.CONFIG t USING (SELECT {q(key)} K, {q(value)} V) s ON t.KEY = s.K "
        f"WHEN MATCHED THEN UPDATE SET VALUE = s.V "
        f"WHEN NOT MATCHED THEN INSERT (KEY, VALUE) VALUES (s.K, s.V)").collect()


def cfg_float(session, key: str, default: float) -> float:
    try:
        return float(get_config(session, key, str(default)))
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# LLM access (Cortex AI_COMPLETE) with automatic model resolution
# ----------------------------------------------------------------------------
def resolve_model(session, force: bool = False) -> str:
    """Return the first preferred model that answers in this account; cache in CONFIG."""
    if not force:
        cached = get_config(session, "ACTIVE_LLM")
        if cached:
            return cached
    prefs = (get_config(session, "LLM_MODELS", "llama3.3-70b") or "").split(",")
    last_err = None
    for m in [p.strip() for p in prefs if p.strip()]:
        try:
            out = scalar(session, f"SELECT AI_COMPLETE({q(m)}, 'Reply with the single word OK')")
            if out:
                set_config(session, "ACTIVE_LLM", m)
                return m
        except Exception as e:  # model not available in region / account
            last_err = e
            continue
    raise RuntimeError(f"No Cortex LLM available. Last error: {last_err}")


def light_model(session) -> str:
    m = get_config(session, "LLM_MODEL_LIGHT")
    if m:
        try:
            if scalar(session, f"SELECT AI_COMPLETE({q(m)}, 'OK?')"):
                return m
        except Exception:
            pass
    return resolve_model(session)


_JSON_RE = re.compile(r"\{.*\}", re.S)


def _repair_truncated(s: str):
    """Best-effort repair of a JSON object cut off by the token limit: drop the unfinished
    tail after the last complete element and close any open brackets."""
    for cut in [len(s)] + [i for i in range(len(s) - 1, 0, -1) if s[i] == ","][:400]:
        cand = s[:cut]
        stack, in_str, esc = [], False, False
        for ch in cand:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]" and stack:
                stack.pop()
        if in_str:
            continue
        try:
            v = json.loads(cand.rstrip().rstrip(",") + "".join(reversed(stack)))
            if isinstance(v, dict):
                v["_truncated"] = cut < len(s) or bool(stack)
                return v
        except Exception:
            continue
    return None


def parse_json_loose(text: Any) -> dict:
    if isinstance(text, dict):
        return text
    if text is None:
        return {}
    s = str(text).strip()
    # AI_COMPLETE may return a JSON-quoted string; unwrap it (possibly twice)
    for _ in range(2):
        if s.startswith('"'):
            try:
                u = json.loads(s)
                if isinstance(u, str):
                    s = u.strip()
                    continue
            except Exception:
                try:  # truncated quoted string: unescape manually
                    s = json.loads(s.rstrip('"') + '"').strip()
                    continue
                except Exception:
                    pass
        break
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip())
    try:
        v = json.loads(s)
        if isinstance(v, str):
            v = json.loads(v)
        return v if isinstance(v, dict) else {"value": v}
    except Exception:
        pass
    start = s.find("{")
    if start >= 0:
        m = _JSON_RE.search(s)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        fixed = _repair_truncated(s[start:])
        if fixed is not None:
            return fixed
    return {"raw": s}


def complete(session, prompt: str, model: str | None = None, max_tokens: int = 1500,
             temperature: float = 0.1) -> str:
    model = model or resolve_model(session)
    sql = (f"SELECT AI_COMPLETE(model => {q(model)}, prompt => {dollar(prompt)}, "
           f"model_parameters => {{'temperature': {temperature}, 'max_tokens': {max_tokens}}})")
    try:
        return str(scalar(session, sql, ""))
    except Exception:
        # Fallback to positional signature
        return str(scalar(session, f"SELECT AI_COMPLETE({q(model)}, {dollar(prompt)})", ""))


def complete_json(session, prompt: str, model: str | None = None, max_tokens: int = 2500) -> dict:
    """Ask for JSON only and parse defensively."""
    p = prompt.strip() + "\n\nReturn ONLY a valid JSON object. No markdown fences, no commentary."
    return parse_json_loose(complete(session, p, model=model, max_tokens=max_tokens))


# ----------------------------------------------------------------------------
# Audit
# ----------------------------------------------------------------------------
def audit(session, agent: str, action: str, case_id: str | None = None, model: str | None = None,
          status: str = "OK", started: float | None = None, details: dict | None = None) -> None:
    dur = int((time.time() - started) * 1000) if started else None
    payload = json.dumps(details or {}, default=str)[:60000]
    try:
        session.sql(
            f"INSERT INTO {DB}.GOV.AGENT_AUDIT_LOG (EVENT_TS, AGENT, ACTION, CASE_ID, MODEL, STATUS, "
            f"DURATION_MS, DETAILS, INVOKED_BY) SELECT CURRENT_TIMESTAMP(), {q(agent)}, {q(action)}, "
            f"{q(case_id)}, {q(model)}, {q(status)}, {q(dur)}, PARSE_JSON({dollar(payload)}), "
            f"CURRENT_USER()").collect()
    except Exception:
        pass  # auditing must never break the pipeline


def inr(x: float | None) -> str:
    """Format rupees in lakh / crore."""
    if x is None:
        return "-"
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:.2f} Cr"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:.2f} L"
    return f"₹{x:,.0f}"
