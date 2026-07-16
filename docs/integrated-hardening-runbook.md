# Integrated retrieval hardening runbook

This runbook operates roadmap milestone 2.9. It does not authorize enabling a
feature whose individual milestone gate or pilot decision is incomplete.

## 1. Offline readiness

Run from the repository root on a clean commit:

```powershell
.\chat_env\Scripts\python.exe -m scripts.integrated_eval.verify_offline `
  --matrix data/integrated_hardening_v1/matrix.json `
  --output reports/integrated-hardening/<run-id>/offline.json

.\chat_env\Scripts\python.exe -m scripts.integrated_eval.preflight `
  --matrix data/integrated_hardening_v1/matrix.json `
  --security-manifest data/integrated_hardening_v1/security_matrix.jsonl `
  --prerequisites data/integrated_hardening_v1/prerequisites.json `
  --offline-evidence reports/integrated-hardening/<run-id>/offline.json `
  --output reports/integrated-hardening/<run-id>/readiness.json
```

Exit code 2 is expected while any prerequisite is incomplete. Do not change a
false prerequisite to true without a verified gate/pilot or rejection artifact.

For the 5–10 user controlled demo, pass the separate demo ledger:

```powershell
.\chat_env\Scripts\python.exe -m scripts.integrated_eval.preflight `
  --matrix data/integrated_hardening_v1/matrix.json `
  --security-manifest data/integrated_hardening_v1/security_matrix.jsonl `
  --prerequisites data/integrated_hardening_v1/prerequisites.json `
  --demo-decisions data/integrated_hardening_v1/demo_decisions.json `
  --offline-evidence reports/integrated-hardening/<run-id>/offline.json `
  --output reports/integrated-hardening/<run-id>/demo-readiness.json
```

This command targets `ready_for_demo_matrix`. It never changes
`ready_for_live_matrix`. Build or verify one scoped decision without editing
the source gate artifact:

```powershell
.\chat_env\Scripts\python.exe -m scripts.eval.milestone_decision build `
  --milestone late_interaction `
  --scope controlled_demo `
  --decision rejected `
  --source-commit <source-commit> `
  --evidence <gate.json> `
  --reason "Gate did not establish the required quality gain." `
  --reviewer <reviewer-id> `
  --output <decision.json>
```

Rejected and inconclusive features are disabled in `demo_matrix.effective_flags`.
Run those rows to verify the fallback path; do not restore the requested flag
just to make the matrix look complete.

## 2. Feature flags and isolation

Each candidate process receives exactly one row from
`data/integrated_hardening_v1/matrix.json`. Do not toggle flags inside a running
process. Start a separate candidate process from the same commit and snapshot.
Set `RAG_EVAL_COMBINATION_ID` to that row's `id` in both arms so every request
budget is attributed to the intended matrix row.

The controlled flags are:

```text
RAG_CRAG_ENABLED
RAG_CLAIM_REPAIR_ENABLED
RAG_GROUNDED_MATH_ENABLED
RAG_LATE_INTERACTION_ENABLED
RAG_QUERY_DECOMPOSITION_ENABLED
RAG_GRAPH_RETRIEVAL_ENABLED
RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED
```

Pin `RAG_PLANNER_VERSION`, `RAG_LATE_INDEX_VERSION`,
`RAG_GRAPH_SERVING_EPOCH` and `RAG_COMMUNITY_SERVING_EPOCH`. Semantic-cache
reads and writes remain disabled during evaluation.

## 3. Baseline, candidate and result aggregation

Use the individual milestone runner for each fixture; do not mix source
collections inside one pair:

```powershell
.\chat_env\Scripts\python.exe -m scripts.crag_eval.run_rollout ...
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.run_rollout ...
.\chat_env\Scripts\python.exe -m scripts.decomposition_eval.run_rollout ...
.\chat_env\Scripts\python.exe -m scripts.graph_eval.run_rollout ...
```

For every combination, preserve the same manifest, SQL/Qdrant snapshot,
provider configuration, commit and concurrency between baseline and candidate.
After the runs, aggregate request budgets and security results:

```powershell
.\chat_env\Scripts\python.exe -m scripts.integrated_eval.results `
  --eval reports/<combination>/candidate/eval.json `
  --security-results reports/<combination>/security-results.jsonl `
  --output reports/<combination>/integrated-results.json
```

## 4. Load benchmark

Run the existing SSE benchmark at the locked concurrency target. It records
question hashes, not raw prompts:

```powershell
.\chat_env\Scripts\python.exe scripts/eval/benchmark_rag_concurrency.py `
  data/<fixture>/eval_manifest.jsonl `
  --base-url http://127.0.0.1:8100 `
  --concurrency 1,5,10 `
  --trace-jsonl logs/rag_trace.jsonl `
  --report reports/<combination>/benchmark.json

.\chat_env\Scripts\python.exe -m scripts.integrated_eval.load_report `
  --benchmark reports/<combination>/benchmark.json `
  --eval reports/<combination>/candidate/eval.json `
  --concurrency 10 `
  --output reports/<combination>/load.json
```

The comparison must contain P50/P95 first-token and completion latency,
cost/query, provider retry rate and fallback rate.

Create baseline and candidate load reports at the same selected concurrency.
Create `matrix-evidence.json` with schema `integrated-matrix-evidence-v1`. It
must contain exactly the seven IDs from the versioned matrix. Every row has
hashed/schema-pinned references named `baseline_eval`, `candidate_eval`,
`baseline_trace`, `candidate_trace`, `baseline_load`, `candidate_load` and
`results`, plus `baseline_benchmark`/`candidate_benchmark` and a hashed
`security_results` JSONL reference. Set `primary_combination_id` to the row whose eval/trace paths will
be passed as the positional arms of the final gate.

Both eval arms must persist `pipeline_configuration` with the canonical seven
flag names and four version fields. Baseline has all seven flags off; candidate
must exactly equal its row in `matrix.json`. A matching `combination_id` string
without the matching configuration is rejected.

Before snapshotting each arm, copy the arm's raw JSONL trace to its immutable
report directory and run `rag_trace_snapshot.py` against that copy. The final
gate re-hashes the raw file and rebuilds the snapshot; an appended shared log,
synthetic snapshot, parse error, legacy reason event, missing query observation
or mismatch between trace maxima and eval case budgets is rejected. Final
generation count is enforced from labeled-eval request telemetry because repair
can emit an additional `llm_generation` event and the raw event name alone does
not distinguish the request's single final-generation budget.

Then compose gate metadata from all seven evidence pairs. The composer rejects
a different commit, manifest, snapshot, provider configuration, governance
scope, collection or concurrency inside any pair. It also rejects an unrelated
trace, load report or results artifact:

```powershell
.\chat_env\Scripts\python.exe -m scripts.integrated_eval.compose_gate_metadata `
  --readiness reports/<run-id>/readiness.json `
  --offline-evidence reports/<run-id>/offline.json `
  --release-decisions reports/<run-id>/release-decisions.json `
  --matrix-evidence reports/<run-id>/matrix-evidence.json `
  --feature-matrix data/integrated_hardening_v1/matrix.json `
  --output reports/<run-id>/gate-metadata.json
```

Invoke the final gate with the baseline, candidate and trace files referenced
by `primary_combination_id`. The gate recomputes their hashes and requires them
to equal the primary row, then requires all seven per-combination quality,
security, budget and load results to pass.

Every release-decision row must contain an evidence reference with path,
SHA-256 and schema. The referenced artifact must carry the same accepted or
rejected decision; a self-declared `evidence_verified` boolean is not accepted.

## 5. Abort and rollback

Abort the active combination immediately when any of these occurs:

- leakage outside the declared admin exception;
- wrong-answer regression or a wrong number/unit/citation;
- more than one correction, repair, calculation or planner call;
- more than three subqueries, 50 graph edges, two provider retries or one final
  generation;
- deadline exceeded;
- first-token/completion P95 or cost exceeds 1.5 baseline;
- fallback rate exceeds 10 percent;
- cache namespace collision or any unverified/stale graph/community evidence;
- a violating draft is observed before post-check/repair completes.

Rollback by stopping the candidate process and setting all flags in its matrix
row to `false`. Restore the previous pinned planner/index/serving epoch before
restarting. Do not delete SQL/Qdrant data as part of runtime rollback.

## 6. Scoped cleanup

Preview cleanup before `--execute`, and set the fixture opt-in and staging
collection required by that script:

```powershell
.\chat_env\Scripts\python.exe -m scripts.crag_eval.cleanup_fixture
.\chat_env\Scripts\python.exe -m scripts.grounded_math_eval.cleanup_fixture
.\chat_env\Scripts\python.exe -m scripts.graph_eval.cleanup_fixture
```

Graph cleanup removes only rows whose documents have
`SourceSystem=graph-eval-v1`, including their proposals, and only the graph
fixture collection/assets. CRAG and Grounded Math cleanup have equivalent
source-system/collection guards.

For Late Interaction, the source collection is always read-only. Use
`scripts.late_interaction.backfill_shadow --prune-orphans` only after a full
successful source scan and only against the explicitly named shadow
collection. Never point cleanup at `TaiLieuKyThuat_v2`.

## 7. Release decision

The integrated gate must record one decision for every feature: accepted with
gate and controlled-pilot evidence, or rejected with the immutable failure
artifact and flags kept off. Update
`docs/doichieukientruc-progress-roadmap.md` with measured values; do not replace
failed evidence with assumptions or unit-test counts.
