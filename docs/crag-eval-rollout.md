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
chat_env\Scripts\python.exe -m scripts.eval.provider_smoke `
  --output "reports\crag-rollout\$run\provider-smoke.json"
chat_env\Scripts\python.exe -m scripts.crag_eval.run_rollout `
  --manifest data\crag_eval_v1\eval_manifest.jsonl `
  --output-dir "reports\crag-rollout\$run" `
  --trace logs\rag_trace.jsonl `
  --provider-smoke-artifact "reports\crag-rollout\$run\provider-smoke.json"
```

The runner verifies that the smoke passed 5/5 without retry and has the same
provider-configuration hash. The baseline subprocess forces both feature flags
off. The candidate subprocess forces both flags on. Semantic cache and realtime
strict streaming are forced off for both runs so baseline answers cannot bypass
candidate retrieval, buffered number checks or repair. Both use one frozen
provider settings snapshot and concurrency 1. Each trace snapshot includes only
`execution_context=evaluation` events inside that run's UTC window.

Runner exit status is diagnostic only. If an evaluation wrote its artifacts, the orchestrator continues and `scripts/eval/crag_rollout_gate.py` is the sole rollout decision. The output contains:

- `baseline/eval.json`, `baseline/eval.md`, `baseline/trace.json`, `baseline/trace.md`
- `candidate/eval.json`, `candidate/eval.md`, `candidate/trace.json`, `candidate/trace.md`
- `gate.json` and `run.json`

Do not start a production pilot unless `gate.json` contains `"passed": true`. During a small pilot, set `RAG_CRAG_ENABLED=true` and `RAG_CLAIM_REPAIR_ENABLED=true`, then monitor `evidence_gate`, `corrective_retrieval`, `claim_repair` and `llm_retry`. Roll back by setting both flags to `false`; no data migration is involved.

## Run the supporting case-paired diagnostic V3

Use this diagnostic only to decide whether a new formal window is worth opening.
It never creates formal evidence or authorizes a controlled-demo pilot, default
rollout or feature enablement. Run it from a clean detached checkout at the
exact commit being measured, with provider settings supplied through the
process environment; do not copy a dotenv or credentials into the checkout.

Open a new PowerShell session in the primary checkout and use a new directory
for every attempt:

```powershell
$main = (Get-Location).Path
$python = Join-Path $main 'chat_env\Scripts\python.exe'
$dotenv = Join-Path $main '.env'
$rc = 'C:\path\to\clean-detached-checkout' # Replace with the exact RC path.
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  throw 'chat_env Python was not found in the primary checkout.'
}
if (-not (Test-Path -LiteralPath $dotenv -PathType Leaf)) {
  throw 'The primary checkout dotenv was not found.'
}
if (-not (Test-Path -LiteralPath $rc -PathType Container)) {
  throw 'Replace $rc with an existing clean detached checkout.'
}
Set-Location -LiteralPath $rc

$env:QDRANT_COLLECTION = 'MechChatbot_CRAG_Eval_v1'
$env:RUN_CRAG_EVAL_FIXTURE = '1'
$env:RAG_CRAG_DIAGNOSTIC_OPT_IN = '1'
$env:RAG_EXECUTION_CONTEXT = 'evaluation'
$env:EXTERNAL_PROCESSING_POLICY = 'all_external'

$run = Get-Date -Format 'yyyyMMdd-HHmmss'
$root = "reports\crag-diagnostic\$run"
New-Item -ItemType Directory -Path $root | Out-Null

& $python -m dotenv -f $dotenv run --no-override -- $python `
  -m scripts.crag_eval.preflight `
  --manifest data\crag_eval_v1\eval_manifest.jsonl `
  --output "$root\preflight.json"
if ($LASTEXITCODE -ne 0) { throw 'CRAG fixture preflight failed.' }

& $python -m dotenv -f $dotenv run --no-override -- $python `
  -m scripts.eval.provider_smoke `
  --output "$root\provider-smoke.json"
if ($LASTEXITCODE -ne 0) { throw 'Provider smoke failed.' }

& $python -m dotenv -f $dotenv run --no-override -- $python `
  -m scripts.crag_eval.run_diagnostic `
  --manifest data\crag_eval_v1\eval_manifest.jsonl `
  --preflight "$root\preflight.json" `
  --provider-smoke-artifact "$root\provider-smoke.json" `
  --output-dir "$root\diagnostic" `
  --trace "$root\driver-trace.jsonl" `
  --router-mode offline
```

Start the diagnostic only when preflight passes `9/9` and the fresh smoke
passes `5/5` with zero failure and retry. Every arm must start within 30 minutes
of the same smoke. Do not refresh the smoke during a declared window.

V3 evaluates each canonical case as an adjacent candidate/baseline pair, then
repeats the full case order with the arm order mirrored. Each arm has a private
trace at `diagnostic/<series>/case-<ordinal>/rag-traces/<arm>.jsonl`; evaluator
outputs remain under the sibling `<arm>/` directory. The full nine-case series
is aggregated before the unchanged rollout gate is applied.

Any provider error, fallback, retry, input drift, trace mismatch or smoke expiry
tombstones the whole window. Do not resume it, rerun only the missing cases,
carry completed pairs forward, overwrite the directory or select the better
series. A complete passing diagnostic only permits owner adjudication of a new
formal declaration while both feature flags remain off.

## Cleanup

Inspect the dry-run plan, then execute it only after confirming the staging SQL and Qdrant environment.

```powershell
chat_env\Scripts\python.exe -m scripts.crag_eval.cleanup_fixture
chat_env\Scripts\python.exe -m scripts.crag_eval.cleanup_fixture --execute
```

After cleanup, rerun preflight. The expected result is failure because the tagged SQL documents and staging collection no longer exist. Production collections and SQL rows with any other `SourceSystem` are outside the cleanup query.
