# Using CoCo CLI with MuleWatch

Judges give extra credit for heavy use of CoCo CLI and Snowflake-native features. Use the prompts below
in order. **Screen-record or save the CoCo session**: it is good evidence for your submission and makes a
strong 30 seconds of the demo video.

## 0. Start
```bash
cd mulewatch
cortex                      # CoCo CLI, connected to your hackathon trial account
/skill list                 # should show mulewatch-deploy, mulewatch-investigate, mulewatch-add-typology
```

## 1. Deploy (CoCo runs each script and fixes syntax drift)
```
$mulewatch-deploy deploy MuleWatch to my account. Go file by file and show me the result of each step.
```

## 2. Understand what got built
```
Show me the dynamic table graph in MULEWATCH.CURATED and explain what ACCOUNT_FEATURES computes for each mule red flag.
```
```
Query MULEWATCH.ANALYTICS.EVAL_RESULTS for the latest run and explain why the blended detector beats RULES_ONLY.
```

## 3. Investigate a case like an analyst
```
$mulewatch-investigate take the highest priority case and walk me through the evidence and STR quality.
```

## 4. Extend the product live (strong demo moment)
```
$mulewatch-add-typology add "dormant account takeover": accounts inactive for 180+ days that suddenly receive
many UPI credits and withdraw cash. Plant 4 such rings plus a decoy (pensioners reactivating accounts), add the
feature and a rule, validate offline with tests/offline_pipeline.py, then redeploy the code.
```

## 5. Operate
```
Turn on autonomous mode for 30 minutes: enable the MW_ROOT_SCORE task DAG and MW_STREAM_SIM, then show task history.
```
```
Suspend everything that consumes credits (tasks, dynamic tables, search indexing, warehouse).
```

## 6. Governance check
```
Prove the masking and row access policies work: switch to FINCRIME_ANALYST with secondary roles off, query
customers and cases, then do the same as FINCRIME_MANAGER.
```

## 7. Ask the Cortex Agent from the terminal
```
Call SNOWFLAKE.CORTEX.DATA_AGENT_RUN on MULEWATCH.AI.MULEWATCH_COPILOT with "Which mule rings withdrew the most
cash and what does our policy say about lien marking?" and show me the tools it used.
```
