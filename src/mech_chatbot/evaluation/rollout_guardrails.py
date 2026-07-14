"""Executable rollout contracts shared by evaluation milestones.

The module validates artifact facts only. It never infers rollout readiness
from unit-test counts or from raw telemetry volume.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path


PAIR_SCHEMA = "rollout-evidence-pair-v1"
LIVE_EVIDENCE_TYPES = {"staging_evaluation", "production_pilot"}
BENCHMARK_CONDITION_FIELDS = (
    "git_sha",
    "manifest_sha256",
    "snapshot_fingerprint",
    "provider_configuration_sha256",
    "concurrency",
    "governance_scope_sha256",
    "collection",
)
EVIDENCE_IDENTITY_FIELDS = (
    "artifact_path",
    "artifact_schema",
    "artifact_sha256",
    "trace_path",
    "trace_schema",
    "trace_sha256",
    "started_at",
    "completed_at",
)
STAGE_DEPENDENCIES = {
    "crag": ("evaluation_foundation",),
    "grounded_math": ("crag",),
    "late_interaction": ("grounded_math",),
    "query_decomposition": ("late_interaction",),
    "graph_retrieval": ("query_decomposition",),
    "community_summaries": ("graph_retrieval",),
    "integrated_hardening": (
        "crag",
        "grounded_math",
        "late_interaction",
        "query_decomposition",
        "graph_retrieval",
    ),
}
COMPLETED_DECISIONS = {"accepted", "rejected", "completed"}
GATE_SCHEMAS = {
    "crag": "crag-rollout-gate-v1",
    "grounded_math": "retrieval-intelligence-gate-v1",
    "late_interaction": "retrieval-intelligence-gate-v1",
    "query_decomposition": "retrieval-intelligence-gate-v1",
    "graph_retrieval": "retrieval-intelligence-gate-v1",
    "community_summaries": "retrieval-intelligence-gate-v1",
    "integrated_hardening": "retrieval-intelligence-gate-v1",
}


def _load_verified_artifact(reference: dict, *, prefix: str = "artifact") -> dict | None:
    path_value = reference.get(f"{prefix}_path")
    expected_sha = str(reference.get(f"{prefix}_sha256") or "").lower()
    expected_schema = reference.get(f"{prefix}_schema")
    if not path_value or not expected_sha or not expected_schema:
        return None
    path = Path(path_value)
    if not path.is_file():
        return None
    try:
        payload = path.read_bytes()
        artifact = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        return None
    if not isinstance(artifact, dict) or artifact.get("schema") != expected_schema:
        return None
    return artifact


def evaluate_rollout_pair(pair: dict) -> dict:
    """Validate one baseline/candidate pair against roadmap section 2.1."""
    baseline = pair.get("baseline") or {}
    candidate = pair.get("candidate") or {}
    mismatches = [
        field
        for field in BENCHMARK_CONDITION_FIELDS
        if baseline.get(field) != candidate.get(field)
    ]
    benchmark_conditions_complete = all(
        baseline.get(field) not in (None, "")
        and candidate.get(field) not in (None, "")
        for field in BENCHMARK_CONDITION_FIELDS
    )
    evidence_identity_complete = all(
        baseline.get(field) not in (None, "")
        and candidate.get(field) not in (None, "")
        for field in EVIDENCE_IDENTITY_FIELDS
    )
    verified_evidence_artifacts = [
        _load_verified_artifact(context, prefix=prefix)
        for context in (baseline, candidate)
        for prefix in ("artifact", "trace")
    ]
    evidence_artifacts_valid = all(verified_evidence_artifacts)
    evidence_artifact_schemas_valid = (
        evidence_artifacts_valid
        and [artifact["schema"] for artifact in verified_evidence_artifacts]
        == [
            "rag-labeled-eval-v4", "rag-refusal-snapshot-v1",
            "rag-labeled-eval-v4", "rag-refusal-snapshot-v1",
        ]
    )
    artifacts_distinct = (
        baseline.get("artifact_sha256") != candidate.get("artifact_sha256")
        and baseline.get("trace_sha256") != candidate.get("trace_sha256")
    )
    artifact_context_valid = (
        evidence_artifact_schemas_valid
        and verified_evidence_artifacts[0].get("run_label") == "baseline"
        and verified_evidence_artifacts[2].get("run_label") == "candidate"
        and all(
            artifact.get("git_sha") == context.get("git_sha")
            and context.get("manifest_sha256") in artifact.get("manifest_sha256s", [])
            and artifact.get("snapshot_fingerprint") == context.get("snapshot_fingerprint")
            and artifact.get("provider_configuration_sha256")
            == context.get("provider_configuration_sha256")
            and artifact.get("governance_scope_sha256")
            == context.get("governance_scope_sha256")
            and artifact.get("benchmark_concurrency") == context.get("concurrency")
            and artifact.get("collection") == context.get("collection")
            for artifact, context in (
                (verified_evidence_artifacts[0], baseline),
                (verified_evidence_artifacts[2], candidate),
            )
        )
        and all(
            trace.get("source", {}).get("git_sha") == context.get("git_sha")
            and trace.get("filters", {}).get("start") == context.get("started_at")
            and trace.get("filters", {}).get("end") == context.get("completed_at")
            and trace.get("filters", {}).get("execution_contexts") == ["evaluation"]
            for trace, context in (
                (verified_evidence_artifacts[1], baseline),
                (verified_evidence_artifacts[3], candidate),
            )
        )
    )
    try:
        baseline_start = datetime.fromisoformat(
            str(baseline.get("started_at")).replace("Z", "+00:00")
        )
        baseline_end = datetime.fromisoformat(
            str(baseline.get("completed_at")).replace("Z", "+00:00")
        )
        candidate_start = datetime.fromisoformat(
            str(candidate.get("started_at")).replace("Z", "+00:00")
        )
        candidate_end = datetime.fromisoformat(
            str(candidate.get("completed_at")).replace("Z", "+00:00")
        )
        evidence_windows_valid = (
            baseline_start < baseline_end <= candidate_start < candidate_end
        )
    except (TypeError, ValueError):
        evidence_windows_valid = False
    evidence_type = pair.get("evidence_type")
    data_plane = pair.get("data_plane") or {}
    production_collection = data_plane.get("production_collection")
    mutation_mode = data_plane.get("mutation_mode")
    touches_production = production_collection in {
        baseline.get("collection"), candidate.get("collection")
    }
    production_collection_not_mutated = (
        bool(production_collection)
        and mutation_mode in {"staging", "shadow", "read_only"}
        and (not touches_production or mutation_mode == "read_only")
    )
    gate = pair.get("gate") or {}
    gate_artifact = _load_verified_artifact(gate)
    gate_schema_valid = (
        gate_artifact is not None
        and gate_artifact.get("schema") == GATE_SCHEMAS.get(pair.get("stage"))
    )
    gate_stage_valid = (
        gate_artifact is not None
        and (
            pair.get("stage") == "crag"
            or gate_artifact.get("stage") == pair.get("stage")
        )
    )
    gate_checks = (gate_artifact or {}).get("checks") or {}
    expected_gate_inputs = {
        "baseline_eval_sha256": baseline.get("artifact_sha256"),
        "candidate_eval_sha256": candidate.get("artifact_sha256"),
        "baseline_trace_sha256": baseline.get("trace_sha256"),
        "candidate_trace_sha256": candidate.get("trace_sha256"),
    }
    gate_inputs_bound = (
        gate_artifact is not None
        and gate_artifact.get("inputs") == expected_gate_inputs
    )
    gate_result_consistent = (
        isinstance((gate_artifact or {}).get("passed"), bool)
        and bool(gate_checks)
        and all(isinstance(value, bool) for value in gate_checks.values())
        and gate_artifact.get("passed") == all(gate_checks.values())
    )
    safety_gate_passed = (
        gate_checks.get("wrong_answer_not_increased") is True
        and gate_checks.get("leakage_zero") is True
    )
    rollback = pair.get("rollback") or {}
    rollback_artifact = _load_verified_artifact(rollback)
    rollback_contract_valid = (
        bool(rollback.get("flags"))
        and rollback.get("defaults_disabled") is True
        and rollback_artifact is not None
        and rollback_artifact.get("schema") == "rollback-test-evidence-v1"
        and rollback_artifact.get("passed") is True
        and rollback_artifact.get("git_sha") == baseline.get("git_sha")
        and set(rollback_artifact.get("flags") or []) == set(rollback.get("flags") or [])
    )
    checks = {
        "schema_valid": pair.get("schema") == PAIR_SCHEMA,
        "run_id_present": bool(pair.get("run_id")),
        "stage_known": pair.get("stage") in STAGE_DEPENDENCIES,
        "live_artifact": evidence_type in LIVE_EVIDENCE_TYPES,
        "benchmark_conditions_match": not mismatches,
        "benchmark_conditions_complete": benchmark_conditions_complete,
        "evidence_identity_complete": evidence_identity_complete,
        "evidence_artifacts_verified": evidence_artifacts_valid,
        "evidence_artifact_schemas_valid": evidence_artifact_schemas_valid,
        "artifact_context_valid": artifact_context_valid,
        "baseline_candidate_artifacts_distinct": artifacts_distinct,
        "evidence_windows_valid": evidence_windows_valid,
        "production_collection_not_mutated": production_collection_not_mutated,
        "gate_artifact_present": gate_artifact is not None,
        "gate_schema_valid": gate_schema_valid,
        "gate_stage_valid": gate_stage_valid,
        "gate_inputs_bound": gate_inputs_bound,
        "gate_result_consistent": gate_result_consistent,
        "safety_gate_passed": safety_gate_passed,
        "rollback_contract_valid": rollback_contract_valid,
    }
    passed = all(checks.values())
    return {
        "schema": "rollout-guardrail-report-v1",
        "stage": pair.get("stage"),
        "passed": passed,
        "production_eligible": passed and (gate_artifact or {}).get("passed") is True,
        "checks": checks,
        "mismatches": mismatches,
    }


def _decision_complete(value: dict | None) -> bool:
    value = value or {}
    if value.get("decision") not in COMPLETED_DECISIONS:
        return False
    path_value = value.get("artifact")
    expected_sha = str(value.get("artifact_sha256") or "").lower()
    expected_schema = value.get("artifact_schema")
    if not path_value or not expected_sha or not expected_schema:
        return False
    path = Path(path_value)
    if not path.is_file():
        return False
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
            return False
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(artifact, dict) and artifact.get("schema") == expected_schema


def evaluate_rollout_series(
    stage: str,
    pairs: list[dict],
    *,
    prior_decisions: dict[str, dict] | None = None,
    minimum_pairs: int = 3,
) -> dict:
    """Validate comparable multi-run evidence before a production transition."""
    prior_decisions = prior_decisions or {}
    pair_reports = [evaluate_rollout_pair(pair) for pair in pairs]
    run_ids = [pair.get("run_id") for pair in pairs]
    evidence_signatures = [
        tuple(
            (pair.get(arm) or {}).get(field)
            for arm in ("baseline", "candidate")
            for field in ("artifact_sha256", "trace_sha256")
        )
        for pair in pairs
    ]
    reference = pairs[0].get("baseline", {}) if pairs else {}
    series_fields = BENCHMARK_CONDITION_FIELDS
    series_conditions_match = all(
        all((pair.get("baseline") or {}).get(field) == reference.get(field) for field in series_fields)
        for pair in pairs
    )
    dependencies = STAGE_DEPENDENCIES.get(stage, ())
    checks = {
        "stage_known": stage in STAGE_DEPENDENCIES,
        "all_pairs_match_stage": bool(pairs) and all(pair.get("stage") == stage for pair in pairs),
        "pair_contracts_valid": bool(pair_reports) and all(report["passed"] for report in pair_reports),
        "minimum_independent_pairs": (
            len(pairs) >= max(1, int(minimum_pairs))
            and len(set(run_ids)) == len(run_ids)
            and all(run_ids)
        ),
        "independent_pair_evidence": (
            bool(pairs)
            and len(set(evidence_signatures)) == len(evidence_signatures)
            and all(all(value not in (None, "") for value in signature) for signature in evidence_signatures)
        ),
        "series_conditions_match": bool(pairs) and series_conditions_match,
        "prior_milestones_completed": all(
            _decision_complete(prior_decisions.get(dependency))
            for dependency in dependencies
        ),
        "all_pair_gates_passed": bool(pair_reports) and all(
            report["production_eligible"] for report in pair_reports
        ),
    }
    return {
        "schema": "rollout-guardrail-series-v1",
        "stage": stage,
        "passed": all(checks.values()),
        "production_eligible": all(checks.values()),
        "checks": checks,
        "minimum_pairs": minimum_pairs,
        "dependencies": list(dependencies),
        "pair_reports": pair_reports,
    }
