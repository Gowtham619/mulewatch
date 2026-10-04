---
name: mulewatch-add-typology
description: Add a new financial-crime typology to MuleWatch across data generator, features, rules, policy library and evaluation.
---
# Add a typology to MuleWatch

Example request: "add dormant-account takeover" or "add QR-code merchant collusion".

## Checklist
1. `python/datagen.py`: add a `ring_<name>()` generator that plants the pattern AND a hard-negative decoy;
   register it in `generate_frames()` with a share of rings; write ground truth with TYPOLOGY and ROLE.
2. `sql/03_dynamic_tables.sql`: add the feature(s) to `CURATED.ACCOUNT_FEATURES` (keep windows anchored to MAX(TXN_TS)).
3. `python/scoring.py`: add the feature to `ML_FEATURES`.
4. `sql/01_tables.sql`: add a rule row in `ANALYTICS.RULE_CONFIG` (feature, operator, threshold, weight, typology).
   For a live account also `INSERT` it directly.
5. `python/rings.py`: map the new role / typology in `build_rings` if it forms networks.
6. `docs/policies/typ_01_typology_library.md`: add a section describing the typology and indicators; re-upload and `CALL APP.LOAD_DOCS()`.
7. Validate offline first: `python tests/offline_pipeline.py 8000 24` - recall must not drop for existing typologies.
8. Redeploy: `./deploy.sh <conn> code`, then `CALL APP.GENERATE_SYNTHETIC_DATA(...)`, `CALL APP.TRAIN_MULE_MODEL()`, `CALL APP.RUN_FULL_PIPELINE(2)`.
