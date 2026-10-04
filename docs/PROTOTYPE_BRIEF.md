# Prototype brief - MuleWatch (paste into the hackathon template)

**Team / Track:** <team name> · Problem statement 4 - Domain-Specific AI Copilot (BFSI / Financial crime)

## Challenge
Cyber-fraud proceeds in India are laundered through networks of mule accounts within minutes. Banks'
rule-based monitoring creates large volumes of false positives, investigations take hours per case, and
STRs are written by hand. Money is gone before anyone acts, and rings are reported as unconnected single accounts.

## Solution
MuleWatch is an agentic financial-crime copilot built entirely on Snowflake. It ingests the ledger, device
telemetry, payment messages, KYC notes, news and victim complaints. It detects mule rings with a 3-layer
engine (rules + ML + graph), investigates each case with AI agents, and drafts a policy-cited STR for human
approval. Every analyst decision feeds back into the model.

## Key features
- Dynamic Tables feature store (pass-through, fan-in, income mismatch, device sharing, structuring, circular flows)
- Rules engine + ML classifier (Model Registry) + NetworkX ring detection → blended risk score
- Cortex AI enrichment: AI_COMPLETE structured extraction from KYC notes, AI_CLASSIFY for news and complaints, AI_FILTER to reject namesakes
- Triage, Investigator and Compliance agents; STR with Cortex Search citations and automatic QA (citation validity, numeric grounding, LLM judge)
- Cortex Agent copilot: Cortex Analyst on a Semantic View (14 verified queries) + 3 Cortex Search services + custom tools
- Tasks DAG for autonomous operation; learning loop from analyst outcomes
- Horizon governance: tag-based masking, row access policy, audit trail; Streamlit in Snowflake investigator app

## Impact (synthetic held-out evaluation)
Versus the rules baseline: precision 28–36% → 79–100%, recall 70–88% → 94–97%, 50–68% fewer alerts, and 100% of held-out
rings recovered as complete networks (5 synthetic worlds). Investigation and STR draft go from hours to minutes.

## Snowflake features used
Dynamic Tables · Snowpark Python procedures · Model Registry · Cortex AI functions (AI_COMPLETE, AI_CLASSIFY,
AI_FILTER) · Cortex Search · Semantic Views + Cortex Analyst · Cortex Agents · Tasks · Streamlit in Snowflake ·
Masking & Row Access Policies, Tags · Resource Monitor · CoCo CLI skills

## Links
- GitHub (public): <url>
- Deployed app: <Streamlit URL>
- Demo video: <url>
