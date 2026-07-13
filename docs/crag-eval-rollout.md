# CRAG evaluation staging runbook

This runbook evaluates CRAG and claim repair against a deterministic staging fixture. It never uses `golden_set_datagoc_real.jsonl` or `golden_set_refusal_grounding.jsonl`.

## Safety boundary

- SQL rows are tagged with `SourceSystem=crag-eval-v1`.
- Fixture document numbers start with `CRAG-EVAL-`.
- Qdrant must be `MechChatbot_CRAG_Eval_v1`.
- Asset cleanup accepts only `data/crag_eval_v1` under this repository.
- Live ingest, preflight, evaluation and cleanup require `RUN_CRAG_EVAL_FIXTURE=1`.
- Cleanup is dry-run unless `--execute` is supplied. It refuses to run when the configured collection differs from the staging collection.

## Prepare and ingest

Run these commands in a dedicated PowerShell session. The environment must point at the intended staging SQL instance before the opt-in is set.

```powershell
$env:QDRANT_COLLECTION = 'MechChatbot_CRAG_Eval_v1'
$env:RUN_CRAG_EVAL_FIXTURE = '1'
$env:RAG_EXECUTION_CONTEXT = 'evaluation'

chat_env\Scripts\python.exe -m scripts.crag_eval.generate_fixture
chat_env\Scripts\python.exe -m scripts.crag_eval.ingest_fixture
chat_env\Scripts\python.exe -m scripts.crag_eval.preflight `
  --manifest data\crag_eval_v1\eval_manifest.jsonl `
  --output reports\crag-rollout\preflight.json
```

Preflight verifies the expected filename, page, version and published/current lifecycle in both SQL and the exact staging collection. A failed preflight stops evaluation before the RAG stack is initialized.

Two fixture-only controls make path coverage reproducible: the alias case forces the preliminary evaluator to `AMBIGUOUS`, and the number-repair case substitutes a known violating draft before deterministic post-checks. Retrieval, rewrite, fusion, number checking and repair still run through their production implementations. Both controls are ignored unless `RAG_EXECUTION_CONTEXT=evaluation`, and the runner restores their environment values after each serial case.

## Run baseline, candidate and gate

Choose a new output directory for each attempt. The orchestrator refuses to overwrite the `baseline` or `candidate` directories. It records the commit, manifest hash, provider configuration hash, serial concurrency, UTC windows and fixture fingerprint.

```powershell
$run = Get-Date -Format 'yyyyMMdd-HHmmss'
chat_env\Scripts\python.exe -m scripts.crag_eval.run_rollout `
  --manifest data\crag_eval_v1\eval_manifest.jsonl `
  --output-dir "reports\crag-rollout\$run" `
  --trace logs\rag_trace.jsonl
```

The baseline subprocess forces both feature flags off. The candidate subprocess forces both flags on. Semantic cache and realtime strict streaming are forced off for both runs so baseline answers cannot bypass candidate retrieval, buffered number checks or repair. Both inherit the same provider settings and use concurrency 1. Each trace snapshot includes only `execution_context=evaluation` events inside that run's UTC window.

Runner exit status is diagnostic only. If an evaluation wrote its artifacts, the orchestrator continues and `scripts/eval/crag_rollout_gate.py` is the sole rollout decision. The output contains:

- `baseline/eval.json`, `baseline/eval.md`, `baseline/trace.json`, `baseline/trace.md`
- `candidate/eval.json`, `candidate/eval.md`, `candidate/trace.json`, `candidate/trace.md`
- `gate.json` and `run.json`

Do not start a production pilot unless `gate.json` contains `"passed": true`. During a small pilot, set `RAG_CRAG_ENABLED=true` and `RAG_CLAIM_REPAIR_ENABLED=true`, then monitor `evidence_gate`, `corrective_retrieval`, `claim_repair` and `llm_retry`. Roll back by setting both flags to `false`; no data migration is involved.

## Cleanup

Inspect the dry-run plan, then execute it only after confirming the staging SQL and Qdrant environment.

```powershell
chat_env\Scripts\python.exe -m scripts.crag_eval.cleanup_fixture
chat_env\Scripts\python.exe -m scripts.crag_eval.cleanup_fixture --execute
```

After cleanup, rerun preflight. The expected result is failure because the tagged SQL documents and staging collection no longer exist. Production collections and SQL rows with any other `SourceSystem` are outside the cleanup query.
