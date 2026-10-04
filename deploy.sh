#!/usr/bin/env bash
# =============================================================================
# MuleWatch one-shot deployment using the Snowflake CLI (`snow`).
#   ./deploy.sh <connection-name>            # full deploy (about 10-15 minutes)
#   ./deploy.sh <connection-name> app        # redeploy only the Streamlit app
#   ./deploy.sh <connection-name> code       # re-upload python + recreate procedures
# The connection must log in as a user with ACCOUNTADMIN (trial default user).
# Tip: inside CoCo CLI just say:  "$mulewatch-deploy deploy MuleWatch to my account"
# =============================================================================
set -euo pipefail
CONN="${1:-default}"
MODE="${2:-all}"
cd "$(dirname "$0")"

ADMIN=(--connection "$CONN" --role MULEWATCH_ADMIN --warehouse MULEWATCH_WH --database MULEWATCH)

step() { printf "\n\033[1;36m▶ %s\033[0m\n" "$1"; }
sqlf() { step "$1"; snow sql "${ADMIN[@]}" -f "$1"; }

upload_code() {
  step "Uploading Python modules to @MULEWATCH.APP.CODE_STAGE"
  for f in python/*.py; do
    snow stage copy "$f" @MULEWATCH.APP.CODE_STAGE --overwrite "${ADMIN[@]}" >/dev/null
  done
  sqlf sql/02_procedures.sql
}

deploy_app() {
  step "Deploying Streamlit app"
  (cd app && snow streamlit deploy --replace "${ADMIN[@]}")
  snow streamlit get-url MULEWATCH_APP --schema APP "${ADMIN[@]}" || true
}

case "$MODE" in
  app)  deploy_app; exit 0 ;;
  code) upload_code; exit 0 ;;
esac

step "00 setup (ACCOUNTADMIN)"
snow sql --connection "$CONN" --role ACCOUNTADMIN -f sql/00_setup.sql
sqlf sql/01_tables.sql
upload_code
step "Uploading policy documents to @MULEWATCH.RAW.DOCS_STAGE"
for f in docs/policies/*.md; do
  snow stage copy "$f" @MULEWATCH.RAW.DOCS_STAGE --overwrite "${ADMIN[@]}" >/dev/null
done
sqlf sql/03_dynamic_tables.sql
sqlf sql/04_seed.sql
sqlf sql/05_search_services.sql
sqlf sql/06_first_run.sql
sqlf sql/07_semantic_view.sql
sqlf sql/08_cortex_agent.sql
sqlf sql/09_tasks.sql
sqlf sql/10_governance.sql
deploy_app

printf "\n\033[1;32m✔ MuleWatch deployed.\033[0m  Open Snowsight → Projects → Streamlit → MULEWATCH_APP\n"
printf "  When done demoing:  snow sql -c %s -f sql/99_suspend_all.sql\n" "$CONN"
