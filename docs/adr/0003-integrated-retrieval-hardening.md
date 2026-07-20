# ADR 0003: Integrated retrieval hardening is evidence-gated

## Status

Accepted for the offline control plane; amended 2026-07-20 for cumulative
activation profiles and Community Summaries. Live evaluation remains blocked
until its prerequisite decisions and immutable evidence are complete.

## Context

CRAG, claim repair, Grounded Math, Late Interaction, query decomposition,
GraphRAG and Community Summaries each have independent budgets and rollback
controls. Enabling more than one path can still create cross-feature failures
in cache isolation, shared correction budget, governance filtering, streaming,
latency and cost.

## Decision

- Maintain a versioned matrix containing the five cumulative candidate
  profiles: CRAG + Claim Repair, then Grounded Math, Query Decomposition,
  GraphRAG and Community Summaries. The baseline for every row is `all_off`.
  Every row explicitly pins all seven feature flags and all
  planner/index/graph/community versions.
- Late Interaction remains `rejected` and is always effective OFF. The matrix
  verifies the current local-reranker fallback instead of launching the
  rejected encoder as a candidate.
- Treat correction as one request-wide budget. Enforce at most one planner,
  three subqueries, one correction, one repair, one calculation, 50 served
  graph edges, two provider retries and one final generation.
- Re-run allow and deny cases for role, department, site, clearance,
  lifecycle, publication and current version. Admin remains a declared global
  scope exception but never bypasses publication/lifecycle/current checks.
- Keep strict factual streaming buffered. `STRICT_REALTIME_STREAMING=true`
  remains an invalid configuration until a sentence-level verifier exists.
- Use the existing SSE concurrency benchmark for first-token/completion
  latency, then join its metadata-only report with labeled-eval cost, retry and
  fallback metrics.
- A live matrix cannot start from offline tests alone. Every prerequisite
  milestone must have a verified completion/rejection decision, and the final
  integrated gate must bind its artifacts to one clean commit and snapshot.

## Consequences

- Offline readiness may report `capability_passed=true` while
  `ready_for_live_matrix=false`; this is an intentional fail-closed state.
- Rollback is disabling the affected flags and restoring the pinned version or
  serving epoch. No database migration is required for runtime rollback.
- Community Summaries is the final cumulative row, but remains effective OFF
  until GraphRAG, its 10-case evaluation, the 10-point global-answer gain, and
  at least one approved summary all pass their own gates.
