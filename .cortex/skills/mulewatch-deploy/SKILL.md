---
name: mulewatch-deploy
description: Deploy (or repair) the MuleWatch project into the connected Snowflake account, step by step, fixing SQL incompatibilities as they appear.
---
# Deploy MuleWatch

You are deploying the MuleWatch financial-crime project in this repository to the active Snowflake connection.

## Procedure
1. Confirm the connection works and the user has ACCOUNTADMIN (`SELECT CURRENT_ROLE(), CURRENT_ACCOUNT()`).
2. Run `sql/00_setup.sql` as ACCOUNTADMIN, then `sql/01_tables.sql` as MULEWATCH_ADMIN.
3. Upload every file in `python/` to `@MULEWATCH.APP.CODE_STAGE` (overwrite, no compression), then run `sql/02_procedures.sql`.
4. Upload `docs/policies/*.md` to `@MULEWATCH.RAW.DOCS_STAGE`.
5. Run, in order, `sql/03_dynamic_tables.sql` … `sql/10_governance.sql`. Script 04 takes 3-6 minutes and 06 takes 3-8 minutes.
6. Deploy the Streamlit app from `app/` (`snow streamlit deploy --replace`) and print its URL.

## Rules
- Execute ONE file at a time and read the output. If a statement fails because of syntax that changed
  between Snowflake releases (Cortex Agent spec, semantic view YAML, AI function signatures), look up the
  current documentation, fix the file in the repo, and re-run only that file. Keep a list of the fixes.
- Never drop the database or disable masking / row access policies to "make it work".
- If a model in `APP.CONFIG.LLM_MODELS` is unavailable, do not edit code: `APP.CONFIG` is tried in order.
- Leave tasks SUSPENDED unless the user asks for autonomous mode.
- At the end, show the results of `SELECT * FROM MULEWATCH.ANALYTICS.EVAL_RESULTS QUALIFY RUN_TS = MAX(RUN_TS) OVER ()`
  and the top 5 cases by priority, then remind the user about `sql/99_suspend_all.sql`.
