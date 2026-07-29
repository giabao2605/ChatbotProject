# ADR 0002: CRAG pilot isolation and Voyage fallback

## Status

Accepted for a controlled production pilot. The pilot remains disabled until
the deployment preflight passes and the named owners approve the window.

## Decision

- Run two isolated RAG processes from the same commit and pinned snapshot.
  Control keeps `RAG_CRAG_ENABLED=false` and
  `RAG_CLAIM_REPAIR_ENABLED=false`; candidate enables both flags.
- The browser-facing app assigns an eligible authenticated identity with
  HMAC-SHA256 over `experiment_id|authenticated_user_id`. Query text never
  participates in assignment. The cohort is one pinned department.
- Queries selected for adjudication are replayed asynchronously to the other
  process. Replays call the internal RAG service directly, preserve the same
  server-resolved RBAC identity, disable semantic-cache reads and writes, and
  skip app history and user-visible side effects. The target verifies a
  pilot-specific HMAC signature bound to the payload hash, original trace,
  target deployment, expiry and single-use nonce; the app uses a bounded replay queue and drops
  queued work when the pilot is disabled or its pinned contract changes.
- Voyage reranking is not retried inside a user request. Any error, including
  HTTP 429, immediately uses the existing deterministic local-fusion fallback.
  Telemetry records provider status, fallback backend and that no retry was
  attempted.
- Abort when Voyage rerank errors exceed 5 percent in a completed 50-call
  window, or when any other abort rule in roadmap 2.3 fires.
- Derive daily sampling, eligible-traffic balance and matched-pair completeness
  from a hashed assignment-event artifact. Runtime samples all risk cases and
  targets 25 percent of normal answers to keep the observed daily floor at 20
  percent.

## Consequences

- Rollback is routing all traffic to control and setting both candidate flags
  to `false`; no data migration is required.
- The pilot cannot be declared successful from unit tests or staging evidence.
  It requires 7–14 days, at least 100 adjudicated matched pairs and a passing
  `crag-production-pilot-v1` artifact.
- Provider availability may make a pilot inconclusive, but never permits a
  threshold or sample-size reduction after observing results.
