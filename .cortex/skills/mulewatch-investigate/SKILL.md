---
name: mulewatch-investigate
description: Investigate a MuleWatch case end-to-end from the terminal - evidence, hypothesis, STR draft quality review - and recommend a disposition.
---
# Investigate a MuleWatch case

Input: a case id like `MW-20261004-00012` (if missing, take the highest-priority case with STATUS = 'TRIAGED').

## Steps (procedural memory)
1. Brief: `CALL MULEWATCH.APP.GET_CASE_BRIEF('<id>')`.
2. If INVESTIGATION_SUMMARY is null: `CALL MULEWATCH.APP.INVESTIGATOR_AGENT('<id>')`.
3. Review the evidence: `SELECT EVIDENCE_TYPE, PAYLOAD FROM MULEWATCH.ANALYTICS.CASE_EVIDENCE WHERE CASE_ID = '<id>'`.
   Summarise money trail (external credits, median dwell minutes, cash/crypto out), devices, complaints, KYC mismatches.
4. Draft or refresh the STR: `CALL MULEWATCH.APP.COMPLIANCE_AGENT('<id>')`, then read the latest row in
   `ANALYTICS.STR_DRAFTS`. Flag if CITATION_VALIDITY < 1, FAITHFULNESS_SCORE < 4 or numeric_grounding < 0.8.
5. Check for exculpatory signals: business-correspondent outlets, one-off windfalls, family device sharing.
6. Recommend a disposition (STR_FILED / ESCALATED_LEA / FALSE_POSITIVE / NEEDS_MORE_INFO) with reasons.
   DO NOT call RECORD_DISPOSITION yourself - a human must approve. Print the exact CALL for them to run.
