# CRAG production pilot runbook

Milestone 2.3 is a live experiment, not a deployment toggle. Code readiness is
necessary but completion requires real production evidence for 7–14 days and
at least 100 adjudicated matched pairs.

## 1. Pin the pilot before traffic

Copy [`examples/crag-pilot-config.example.json`](examples/crag-pilot-config.example.json)
to a run-specific directory. Fill the commit, snapshot, one eligible department,
deployment IDs, UTC window and three named owners before starting either process.
Do not change the cohort, salt, snapshot or assignment version after traffic starts.

The assignment salt belongs in the secret store and is supplied only through
`CRAG_PILOT_ASSIGNMENT_SALT`; it must not be written to the config or artifacts.

## 2. Start isolated control and candidate processes

Both processes use the same code, SQL data and pinned Qdrant snapshot. They use
different ports and deployment IDs.

Control environment:

```powershell
$env:RAG_SERVER_PORT = '8101'
$env:RAG_DEPLOYMENT_ID = 'crag-control-v1'
$env:RAG_DEPLOYMENT_GIT_SHA = '<commit>'
$env:RAG_SNAPSHOT_FINGERPRINT = '<snapshot>'
$env:CRAG_PILOT_ASSIGNMENT_SALT = '<same-secret-store-value>'
$env:RAG_CRAG_ENABLED = 'false'
$env:RAG_CLAIM_REPAIR_ENABLED = 'false'
```

Candidate environment:

```powershell
$env:RAG_SERVER_PORT = '8102'
$env:RAG_DEPLOYMENT_ID = 'crag-candidate-v1'
$env:RAG_DEPLOYMENT_GIT_SHA = '<same-commit>'
$env:RAG_SNAPSHOT_FINGERPRINT = '<same-snapshot>'
$env:CRAG_PILOT_ASSIGNMENT_SALT = '<same-secret-store-value>'
$env:RAG_CRAG_ENABLED = 'true'
$env:RAG_CLAIM_REPAIR_ENABLED = 'true'
```

Run the read-only preflight before enabling gateway routing:

```powershell
chat_env\Scripts\python.exe -m scripts.eval.crag_pilot_preflight `
  --config reports\crag-pilot\config.json `
  --output reports\crag-pilot\deployment-preflight.json
```

The preflight must report `passed=true`. It checks health, deployment isolation,
commit, snapshot and opposite feature-flag states.

## 3. Enable stable gateway assignment

Set these only on the browser-facing app process after preflight and owner approval:

```powershell
$env:CRAG_PILOT_ENABLED = 'true'
$env:CRAG_PILOT_EXPERIMENT_ID = 'crag-pilot-v1'
$env:CRAG_PILOT_ASSIGNMENT_SALT = '<secret-store-value>'
$env:CRAG_PILOT_DEPARTMENT = 'Technical'
$env:CRAG_PILOT_COHORT_SHA256 = '<pinned-cohort-sha256>'
$env:CRAG_PILOT_CONTROL_URL = 'http://127.0.0.1:8101'
$env:CRAG_PILOT_CANDIDATE_URL = 'http://127.0.0.1:8102'
$env:CRAG_PILOT_CONTROL_DEPLOYMENT_ID = 'crag-control-v1'
$env:CRAG_PILOT_CANDIDATE_DEPLOYMENT_ID = 'crag-candidate-v1'
$env:CRAG_PILOT_SNAPSHOT_FINGERPRINT = '<same-snapshot>'
```

The flag defaults to false. An enabled but incomplete config fails closed.
Assignment depends only on the authenticated user identity. Replays go directly
to the opposite RAG process with semantic cache disabled and never create app
history. Each replay carries an HMAC signature bound to its payload hash,
original trace, target deployment, five-minute expiry and single-use nonce.
Trace events contain hashed actor and pair IDs, not raw prompts or credentials.
The replay executor has a bounded queue and cancels queued work on app shutdown;
set `CRAG_PILOT_REPLAY_WORKERS` and `CRAG_PILOT_REPLAY_QUEUE_SIZE` before startup.

## 4. Sampling, adjudication and monitoring

Sample 100 percent of refusals, corrections, repairs, access denials and provider
errors. Runtime targets 25 percent of normal answers so the observed daily rate
can remain at least 20 percent. Every sampled query
must have both control and candidate output, two independent reviewers and a
third adjudicator when they disagree.

Export every metadata-only `pilot_assignment` event, including unsampled normal
answers, to `assignments.jsonl`. Build `matched-pairs.jsonl` only from sampled
IDs and attach a resolved `evaluation-adjudication-v1` record to every pair.
Monitoring windows must be trace-backed: performance windows contain exactly 50
eligible queries and last at least 30 minutes; Voyage windows independently
contain exactly 50 completed rerank calls. Each row carries its source trace
SHA-256. Create `trace-snapshot.json` from the raw trace with the exact pilot UTC
start/end and `execution_context=production`. The gate re-hashes that raw source,
requires contiguous performance windows from pilot start to end, and reconciles
their query/call totals with the snapshot and all assignment events.

Monitor `evidence_gate`, `corrective_retrieval`, `claim_repair`,
`external_ai_call`, `llm_retry`, `rag_end`, `pilot_assignment` and
`pilot_replay_result`. Voyage uses immediate local fallback without an in-request
retry. Abort when its error rate exceeds 5 percent in a completed 50-call window.

Also abort immediately for leakage outside an admin exception, a confirmed severe
wrong answer, correction/repair budget violation, or two consecutive non-overlapping
50-query windows of at least 30 minutes where P95 exceeds 1.25 control or cost
exceeds 1.5 control.

## 5. Build the final artifact

The labeled pair JSONL contains no prompt, username, user ID, credential or secret.
Each row carries a unique `matched_pair_id`, timestamp, assigned arm, cohort hash,
role/site/query-type buckets, two arm metrics and adjudication metadata.

```powershell
chat_env\Scripts\python.exe -m scripts.eval.crag_pilot_gate `
  --config reports\crag-pilot\config.json `
  --preflight reports\crag-pilot\deployment-preflight.json `
  --trace-snapshot reports\crag-pilot\trace-snapshot.json `
  --assignments reports\crag-pilot\assignments.jsonl `
  --pairs reports\crag-pilot\matched-pairs.jsonl `
  --windows reports\crag-pilot\monitoring-windows.jsonl `
  --output-dir reports\crag-pilot\decision
```

The gate hashes assignments, pairs, monitoring windows and preflight into the
decision artifact. It derives daily sampling and arm balance from all assignment
events rather than trusting a manually entered aggregate.

Only `decision=accepted` and `passed=true` closes milestone 2.3. At day 14,
fewer than 100 adjudicated pairs yields `inconclusive`; do not lower the sample.

## 6. Abort, stop and rerun

To abort or end the pilot, first set `CRAG_PILOT_ENABLED=false` on the app so all
traffic returns to the normal control URL. Then set both candidate feature flags
to false and stop the candidate process. No data migration is required.

For a new attempt, use a new experiment ID, salt, run directory and UTC window;
rerun deployment preflight. Never append a restarted experiment to an earlier
pilot artifact.
