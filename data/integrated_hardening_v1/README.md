# Integrated hardening v1

This fixture defines the minimum roadmap 2.9 feature-combination and security
coverage contracts. It contains no prompts from users and no production data.

- `matrix.json` explicitly pins every feature flag and serving/index version.
- `security_matrix.jsonl` covers allow and deny behavior for role, department,
  site, clearance, lifecycle, publication and current version.
- `prerequisites.json` is a dated fail-closed snapshot. It must be refreshed
  from verified milestone decision artifacts before a live matrix run. A stage
  marked complete is ignored unless its decision artifact path, schema and
  SHA-256 all verify.
  Completion additionally uses a stage-specific schema allowlist and semantic
  outcome check; an arbitrary JSON file with a matching self-declared schema
  or decision cannot open a prerequisite.

Offline capability does not authorize provider calls or feature serving. A live
matrix may start only when `integrated-hardening-readiness-v1` reports
`ready_for_live_matrix=true` on a clean commit.

`release_decisions.json` is intentionally incomplete. A final row needs an
accepted or rejected decision plus a path, SHA-256 and schema for the matching
immutable evidence artifact. The metadata composer and final gate fail closed
when a row is missing or self-declares verification without a valid artifact.

Live integrated evidence uses `integrated-matrix-evidence-v1`: exactly seven
rows, each binding baseline/candidate eval, trace, load and aggregated results
by path, SHA-256 and schema. The final gate fails if any one row regresses.
