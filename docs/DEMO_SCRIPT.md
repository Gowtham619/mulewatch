# MuleWatch - 3-minute demo video script

| Time | Screen | Say |
|---|---|---|
| 0:00-0:20 | Title / Command Center | "Indian banks lose crores to cyber-fraud, and the money moves through mule accounts in minutes. Rule-based alerts are mostly noise. MuleWatch catches whole mule rings, investigates them and drafts the STR, all inside Snowflake." |
| 0:20-0:40 | Command Center KPIs + precision vs rules | "On held-out rings, rules alone are about 30% precise. Our blend of rules, an ML model and graph ring detection is 80-100% precise with about 95% recall, and it raises half as many alerts. Here is today's live number." |
| 0:40-1:00 | Sidebar: Inject attack → Run pipeline | "Let's simulate a live scam wave. Dynamic Tables refresh the features, Snowpark scores every account, NetworkX finds the ring, and Cortex AI enriches only the alerted accounts." |
| 1:00-1:30 | Investigation → Network tab | "Here's the new P1 ring: victims pay collectors, money is layered, then withdrawn at ATMs or sent to crypto. The median dwell time is minutes. Shared phones link the accounts." |
| 1:30-1:55 | AI findings + Evidence | "The investigator agent built an evidence pack from 8 sources (ledger, devices, complaints, KYC notes read by AI_COMPLETE, adverse media screened with AI_FILTER) and wrote a hypothesis." |
| 1:55-2:20 | STR draft tab | "The compliance agent retrieves our policies with Cortex Search and drafts the STR with cited clauses. It checks itself: citation validity, numeric grounding and an AI judge. Nothing is filed until I approve." Click Approve. |
| 2:20-2:40 | Ask MuleWatch | Ask "Which rings withdrew the most cash?" then "What's our STR timeline?" "A Cortex Agent routes between Cortex Analyst on our semantic view and Cortex Search." |
| 2:40-2:55 | Model & Governance | "My approval became training labels, and the learning loop re-weights the rules and retrains the model. PII is masked for analysts, rows are filtered by region, and every AI action is audited." |
| 2:55-3:00 | CoCo terminal | "All of it deployed and extended with CoCo CLI skills. Thank you." |
