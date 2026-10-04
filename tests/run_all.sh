#!/usr/bin/env bash
# Runs every offline check (no Snowflake account required).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pyflakes python app tests
python3 tests/check_specs.py
python3 tests/offline_pipeline.py 8000 24
python3 tests/test_procs_duckdb.py
python3 tests/test_app_smoke.py
