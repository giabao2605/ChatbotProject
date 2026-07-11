# Retrieval architecture decision

## Decision

The pilot uses explicit dense retrieval plus BM25 sparse retrieval as two
separate Qdrant searches, then performs reciprocal-rank fusion (RRF) in
`src/mech_chatbot/rag/pipeline_steps.py`.

The runtime mode for this path is `explicit_dense_bm25_rrf`.  It is the source
of truth for the pilot; Qdrant Query API/prefetch is not currently required.

## Why

- The fusion policy is visible in project code rather than hidden in a client
  library default.
- Each selected chunk retains `retrieval_rrf_score`, dense rank, and BM25 rank
  for diagnostics.
- A provider/library incompatibility has an explicit `hybrid_fallback` mode
  instead of silently changing the ranking policy.

## Operational constraints

- Pilot traces must show the explicit mode for normal questions. Frequent
  `hybrid_fallback` blocks rollout until investigated.
- Trace events include `dense_retrieval`, `bm25_retrieval`, and
  `rrf_grouping`, with numeric latency and the top fused rank metadata.
- The concurrency benchmark compares these stage latencies at 1, 5, and 10
  concurrent requests before any increase to `MAX_CONCURRENT_RAG`.
- A future switch to Qdrant Query API/prefetch is a performance decision, not
  a silent replacement. It requires a new benchmark and architecture review.
