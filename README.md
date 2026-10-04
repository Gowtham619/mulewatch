# 🛡️ MuleWatch — Agentic UPI Mule-Ring Investigator on Snowflake

> **Snowflake CoCo CLI Hackathon 2026 · GCC Edition** — Problem statement: *Domain-Specific AI Copilot (BFSI)*
> 100% Snowflake-native. Deployed, operated and extended with **Snowflake Cortex Code (CoCo) CLI** (project skills included).

India's banks lose thousands of crores a year to cyber-fraud. The stolen money is moved through **mule
accounts**: students, homemakers and gig workers whose accounts are rented, bought or hijacked.
Rule-based AML systems produce mostly false positives. Investigators then spend **3–4 hours per case**
stitching together ledgers, device logs, complaints and KYC notes, and writing the Suspicious Transaction
Report (STR) by hand. By the time they finish, the money has already been withdrawn.

**MuleWatch catches whole mule rings, not single accounts, investigates them automatically and drafts
the STR with policy citations, in minutes.** A human approves every decision, and each decision
teaches the system.

---

## What it does

| # | Capability | How (Snowflake-native) |
|---|---|---|
| 1 | **Multi-modal data**: ledger (structured), device telemetry and payment messages (JSON), KYC notes, news and complaints (text), policies (documents) | Tables, `VARIANT`, stages |
| 2 | **Near-real-time features**: pass-through ratio, fan-in, income mismatch, device sharing, structuring, circular flows | **Dynamic Tables** (6-table DAG) |
| 3 | **3-layer detection**: configurable rules → ML classifier → **graph ring detection** | Snowpark Python procs, scikit-learn, NetworkX, **Model Registry** |
| 4 | **AI enrichment** of the alerted population only, to control cost | `AI_COMPLETE` (structured output), `AI_CLASSIFY`, `AI_FILTER`, `JAROWINKLER_SIMILARITY` |
| 5 | **Three agents**: Triage → Investigator (8-source evidence pack) → Compliance (STR with citations plus a self-check) | Python procs + Cortex LLMs, **Cortex Search** (RAG) |
| 6 | **Copilot chat** over numbers, policy, news and complaints | **Cortex Agent** + **Cortex Analyst** on a **Semantic View** with 14 verified queries |
| 7 | **Autonomous loop**: live traffic → detect → investigate → evaluate, every 15 min | **Tasks DAG** |
| 8 | **Human-in-the-loop learning**: analyst decisions re-weight rules and retrain the model | `RECORD_DISPOSITION` → `TUNE_RULES` + `TRAIN_MULE_MODEL` |
| 9 | **Governance**: masked PII, regional row-level security, full AI audit trail, hidden ground truth | Tag-based **masking**, **row access policy**, audit log |
| 10 | **Investigator app**: command center, case queue, ring network graph, STR approval, copilot | **Streamlit in Snowflake** |

## Architecture

```mermaid
flowchart LR
  subgraph RAW["RAW (landing)"]
    T[Transactions<br/>UPI/IMPS/NEFT/ATM/Cash]:::s
    D[Device events<br/>JSON]:::j
    P[Payment msgs<br/>JSON]:::j
    K[KYC notes]:::u
    N[News feed]:::u
    C[Victim complaints<br/>NCRP-style]:::u
    DOC[Policies / SOPs<br/>typologies]:::u
  end
  subgraph CUR["CURATED · Dynamic Tables"]
    TE[TXN_ENRICHED] --> AF[ACCOUNT_FEATURES]
    DF[DEVICE_SHARING] --> AF
    CF[CIRCULAR_FLOWS] --> AF
  end
  T --> TE
  D --> DF
  P --> AF
  C --> AF
  subgraph DET["Detection (Snowpark)"]
    R[Rules engine<br/>RULE_CONFIG] --> B((Blend))
    M[ML classifier<br/>Model Registry] --> B
    G[Graph rings<br/>NetworkX] --> B
  end
  AF --> R & M & G
  B --> CASES[(CASES)]
  subgraph AI["Cortex AI"]
    E[Enrichment<br/>AI_COMPLETE · AI_CLASSIFY · AI_FILTER]
    TA[Triage agent]
    IA[Investigator agent]
    CA[Compliance agent<br/>STR + citations + QA judge]
    S[(Cortex Search<br/>policies · media · complaints)]
    SV[Semantic View<br/>+ verified queries]
    AG[Cortex Agent<br/>MULEWATCH_COPILOT]
  end
  K & N --> E
  CASES --> TA --> IA --> CA
  E --> IA
  DOC --> S --> CA
  S --> AG
  SV --> AG
  CA --> STR[(STR_DRAFTS)]
  STR --> UI[Streamlit in Snowflake<br/>Investigator app]
  AG --> UI
  UI -- approve / reject --> FB[(Feedback labels)]
  FB -- Tasks: TUNE_RULES + retrain --> R & M
  classDef s fill:#dbeafe; classDef j fill:#fef3c7; classDef u fill:#dcfce7;
```

**Tasks DAG** (created suspended, so it costs nothing until you turn it on):
`MW_STREAM_SIM (5 min)` · `MW_ROOT_SCORE (15 min) → MW_RINGS → MW_ENRICH → MW_TRIAGE → MW_INVESTIGATE → MW_EVALUATE` · `MW_LEARN (daily)`

## Results (held-out synthetic rings never used in training)

From the offline replica (`python tests/offline_pipeline.py 20000 30`), which uses the same data, the same
dynamic-table SQL and the same rules as the deployed system. Ranges cover 5 random worlds (seeds 1–4 and 42),
each with about 23k accounts, 0.9M transactions and 30 planted rings:

| Detector | Precision | Recall | Alerts |
|---|---|---|---|
| Rules only (legacy baseline) | 28–36% | 70–88% | 254–295 |
| ML only | 86–95% | 93–98% | 102–125 |
| **MuleWatch blended (rules + ML + graph)** | **79–100%** | **94–97%** | **93–137** |
| Ring recovery (true rings found as one ring) | 97–100% purity | **100%** | 30–31 rings |

That is **50–68% fewer alerts than rules alone, with higher recall**. The dataset includes hard negatives
on purpose: licensed money-transfer outlets that legitimately receive many payments and withdraw cash the
same day; new accounts receiving property-sale proceeds and moving them the same day; families sharing one
phone; and "low-and-slow" mule rings that use aged accounts and multi-day holds. Most remaining false
positives are the property-sale "windfall" accounts. That is exactly what the human-in-the-loop learning
loop is for: an analyst closes them as false positives, and `MW_LEARN` retrains on that outcome.
Synthetic data is cleaner than real data, so expect lower real-world numbers. The live system re-measures
itself after every run (`ANALYTICS.EVAL_RESULTS`) and shows the numbers in the app.

## Quickstart (about 15 minutes)

**Prerequisites:** the Snowflake trial account from the hackathon link (it includes Cortex and CoCo),
the [Snowflake CLI](https://docs.snowflake.com/en/developer-guide/snowflake-cli/installation/installation)
(`snow`) with a connection set up, and optionally CoCo CLI.

```bash
git clone <this repo> && cd mulewatch
snow connection add            # once; use your trial account, user with ACCOUNTADMIN
./deploy.sh <connection-name>  # setup → data → features → search → pipeline → semantic view → agent → tasks → governance → app
```

**With CoCo CLI** (recommended): open the repo in `cortex` and type
`$mulewatch-deploy deploy MuleWatch to my account`. The project skill in `.cortex/skills/` runs each step
and fixes any syntax differences between Snowflake releases as it goes.

Open **Snowsight → Projects → Streamlit → MULEWATCH_APP**. When you finish: `snow sql -c <conn> -f sql/99_suspend_all.sql`.

### Live demo flow (3 minutes)
1. **Command Center**: KPIs, precision vs the rules baseline, rings table.
2. Sidebar → **⚡ Inject a live mule-ring attack**, then **▶️ Run detection pipeline**. A new P1 case appears.
3. **Investigation**: ring network graph (victims → collectors → layers → ATM/crypto), money timeline,
   AI findings, evidence pack, then **📝 Draft STR**. The draft has citations to policy chunks, a citation
   validity score and an AI judge's faithfulness score.
4. Edit the narrative and **✅ Approve & file**. The decision becomes training labels
   (**Model & Governance → Learning loop**).
5. **Ask MuleWatch**: *"Which rings withdrew the most cash?"*, then *"What does our policy say about STR timelines?"*.
6. Governance proof: run the snippet at the bottom of `sql/10_governance.sql` (masked PII and region filtering for analysts).

## Repository layout

```
sql/        00_setup … 10_governance, 99_suspend_all   (run in order; deploy.sh does it)
python/     datagen · scoring · rings · enrich · agents · evaluate · pipeline · common   (Snowpark procs)
app/        streamlit_app.py · snowflake.yml · environment.yml
docs/       policies/ (synthetic AML policy, SOPs, typology library) · PROTOTYPE_BRIEF · DEMO_SCRIPT · COCO_PROMPTS
.cortex/    skills/ for CoCo CLI (deploy, investigate a case, add a typology)
tests/      offline_pipeline.py · test_procs_duckdb.py · test_app_smoke.py
```

## Testing without a Snowflake account
```bash
pip install pandas numpy scikit-learn networkx duckdb sqlglot streamlit
python tests/offline_pipeline.py 20000 30   # real DT SQL (transpiled to DuckDB) + rules + ML + rings + evaluation
python tests/test_procs_duckdb.py           # runs the actual stored-procedure handlers via a fake Snowpark session
python tests/test_app_smoke.py              # renders every Streamlit page against pipeline output
```

## Cost controls
X-Small warehouse with 60-second auto-suspend, a 40-credit resource monitor, and tasks created suspended.
LLM enrichment runs **only on accounts in open cases**, a cheap model handles bulk extraction, and
investigations are capped per run. The model list in `APP.CONFIG` is tried in order and the first model
available in your region is used.

## Responsible AI
- The AI drafts and a person decides. STRs stay `DRAFT` until a named reviewer approves them.
- Every agent action (model, latency, inputs summary) is written to `GOV.AGENT_AUDIT_LOG`.
- Anti-hallucination checks on each STR: citation validity, numeric grounding, and an LLM judge's faithfulness score.
- The adverse-media screen uses `AI_FILTER` to reject namesakes in other cities (POL-04).
- Ground truth is isolated in `GOV` and never read by detection code.
- All data, people, banks and publications are **synthetic and fictional**. The policy documents paraphrase
  public concepts for demo purposes and are not legal advice.

## Known limitations / next steps
Integration with real core banking and the national complaint feed via Snowpipe Streaming; Jira and
ServiceNow case sync through MCP; a lien and freeze API; graph embeddings; adaptive alert thresholds per segment.
