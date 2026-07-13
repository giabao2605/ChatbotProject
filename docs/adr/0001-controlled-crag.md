# ADR 0001: Controlled corrective retrieval and claim repair

## Status

Accepted for evaluation; disabled by default in production.

## Decision

- `RAG_CRAG_ENABLED=false` permits at most one query-rewrite retrieval pass
  when an evidence evaluator returns `AMBIGUOUS`.
- Corrected retrieval reuses the existing strict, broad, RBAC, site,
  lifecycle, publication, and current-version filters without rebuilding or
  relaxing them.
- `RAG_CLAIM_REPAIR_ENABLED=false` permits at most one repair generation after
  a number-grounding failure. The complete answer is held back until material,
  code, unit, number, and citation checks pass again.
- `RAG_EXECUTION_CONTEXT` is `production`, `evaluation`, or `test` and is
  attached to every trace event. Evaluation and tests must identify themselves.
- Rollout compares labeled baseline and candidate reports plus trace snapshots.
  Wrong answers must not increase, leakage must remain zero, and latency/cost
  must remain inside the configured gate.

## Operations

Create trace snapshots with `scripts/eval/rag_trace_snapshot.py`. Compare a
baseline and candidate with `scripts/eval/crag_rollout_gate.py`. Roll back by
setting both CRAG feature flags to `false`; no data migration is required.
