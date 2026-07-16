"""Scoped, immutable decision contracts for retrieval roadmap milestones."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DECISION_SCOPES = {"controlled_demo", "default_rollout"}
DECISIONS = {"accepted", "rejected", "inconclusive"}
MILESTONE_FLAGS = {
    "crag": ("RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"),
    "grounded_math": ("RAG_GROUNDED_MATH_ENABLED",),
    "late_interaction": ("RAG_LATE_INTERACTION_ENABLED",),
    "query_decomposition": ("RAG_QUERY_DECOMPOSITION_ENABLED",),
    "graph_retrieval": ("RAG_GRAPH_RETRIEVAL_ENABLED",),
    "community_summaries": ("RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",),
}
DEMO_MILESTONES = frozenset(MILESTONE_FLAGS)


def _resolve_path(value: object, root: str | Path) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else Path(root) / path


def _load_json_reference(reference: dict, *, root: str | Path) -> tuple[dict, dict]:
    path = _resolve_path(reference.get("path"), root)
    try:
        raw = path.read_bytes()
        artifact = json.loads(raw.decode("utf-8"))
        report = {
            "path": str(path),
            "exists": True,
            "sha256_matches": hashlib.sha256(raw).hexdigest() == reference.get("sha256"),
            "schema_matches": (
                not reference.get("schema")
                or artifact.get("schema") == reference.get("schema")
            ),
        }
        return artifact, report
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}, {
            "path": str(path), "exists": False, "sha256_matches": False,
            "schema_matches": False,
        }


def _verify_artifact_reference(
    reference: dict, *, root: str | Path, source_commit: str,
    require_commit_anchor: bool,
) -> dict:
    artifact, report = _load_json_reference(reference, root=root)
    run_metadata = artifact.get("run_metadata")
    metadata_commit = (
        run_metadata.get("commit_sha")
        if isinstance(run_metadata, dict) else None
    )
    artifact_commit = str(
        artifact.get("git_sha") or artifact.get("commit_sha")
        or artifact.get("source_commit") or metadata_commit or ""
    )
    commit_anchor_present = bool(artifact_commit)
    report["commit_anchor_present"] = commit_anchor_present
    report["source_commit_matches"] = (
        artifact_commit == source_commit if commit_anchor_present
        else not require_commit_anchor
    )

    nested_references = artifact.get("source_artifacts")
    nested_reports: list[dict[str, Any]] = []
    nested_shape_valid = nested_references is None or (
        isinstance(nested_references, list)
        and bool(nested_references)
        and all(
            isinstance(item, dict)
            and bool(str(item.get("path") or "").strip())
            and len(str(item.get("sha256") or "")) == 64
            and bool(str(item.get("schema") or "").strip())
            for item in nested_references
        )
    )
    if nested_shape_valid and isinstance(nested_references, list):
        nested_reports = [
            _verify_artifact_reference(
                item, root=root, source_commit=source_commit,
                require_commit_anchor=False,
            )
            for item in nested_references
        ]
    report["nested_artifacts_valid"] = nested_shape_valid
    report["nested_artifacts_passed"] = (
        nested_shape_valid and all(item["passed"] for item in nested_reports)
    )
    nested_commit_anchor_present = any(
        item["provenance_commit_anchor"] for item in nested_reports
    )
    report["nested_commit_anchor_present"] = nested_commit_anchor_present
    report["provenance_commit_anchor"] = (
        nested_commit_anchor_present
        if isinstance(nested_references, list)
        else commit_anchor_present and report["source_commit_matches"]
    )
    report["commit_requirement_satisfied"] = (
        report["provenance_commit_anchor"]
        or (not require_commit_anchor and nested_references is None)
    )
    report["source_artifacts"] = nested_reports
    report["passed"] = all(report[field] for field in (
        "exists", "sha256_matches", "schema_matches", "source_commit_matches",
        "nested_artifacts_valid", "nested_artifacts_passed",
        "commit_requirement_satisfied",
    ))
    return report


def validate_milestone_decision(payload: dict) -> dict:
    evidence = payload.get("evidence")
    signoff = payload.get("reviewer_signoff")
    checks = {
        "schema_valid": payload.get("schema") == "milestone-decision-v2",
        "milestone_present": str(payload.get("milestone") or "").strip() in MILESTONE_FLAGS,
        "scope_valid": payload.get("scope") in DECISION_SCOPES,
        "decision_valid": payload.get("decision") in DECISIONS,
        "source_commit_present": bool(str(payload.get("source_commit") or "").strip()),
        "evidence_present": (
            isinstance(evidence, list)
            and bool(evidence)
            and all(
                isinstance(item, dict)
                and bool(str(item.get("path") or "").strip())
                and len(str(item.get("sha256") or "")) == 64
                and bool(str(item.get("schema") or "").strip())
                for item in evidence
            )
        ),
        "reason_present": bool(str(payload.get("reason") or "").strip()),
        "reviewer_signoff_present": (
            isinstance(signoff, dict)
            and bool(str(signoff.get("reviewer") or "").strip())
            and bool(str(signoff.get("signed_at") or "").strip())
        ),
    }
    passed = all(checks.values())
    return {
        "schema": "milestone-decision-validation-v2",
        "passed": passed,
        "checks": checks,
        "completes_controlled_demo": (
            passed and payload.get("scope") == "controlled_demo"
            and payload.get("decision") in DECISIONS
        ),
        "completes_default_rollout": (
            passed and payload.get("scope") == "default_rollout"
            and payload.get("decision") in {"accepted", "rejected"}
        ),
    }


def verify_milestone_decision(
    payload: dict, *, root: str | Path, current_commit: str | None = None,
) -> dict:
    validation = validate_milestone_decision(payload)
    source_commit = str(payload.get("source_commit") or "")
    evidence_reports = [
        _verify_artifact_reference(
            item, root=root, source_commit=source_commit,
            require_commit_anchor=True,
        )
        for item in payload.get("evidence") or []
    ]
    source_matches = bool(evidence_reports) and all(
        report["source_commit_matches"] for report in evidence_reports
    )
    return {
        "schema": "milestone-decision-verification-v2",
        "passed": validation["passed"] and bool(evidence_reports)
        and all(report["passed"] for report in evidence_reports),
        "validation": validation,
        "source_commit_matches": source_matches,
        "current_commit_matches": current_commit == source_commit,
        "evidence": evidence_reports,
    }


def verify_demo_decision_ledger(
    payload: dict, *, root: str | Path, current_commit: str | None = None,
) -> dict:
    rows = payload.get("decisions") or {}
    schema_valid = (
        payload.get("schema") == "controlled-demo-decision-ledger-v2"
        and set(rows) == DEMO_MILESTONES
    )
    reports = {}
    for milestone in DEMO_MILESTONES:
        reference = rows.get(milestone) or {}
        try:
            decision, reference_report = _load_json_reference(reference, root=root)
            reference_valid = (
                reference_report["exists"]
                and reference_report["sha256_matches"]
            )
            verification = verify_milestone_decision(
                decision, root=root, current_commit=current_commit,
            )
            passed = (
                reference_valid
                and decision.get("milestone") == milestone
                and decision.get("scope") == "controlled_demo"
                and verification["passed"]
            )
            reports[milestone] = {
                "passed": passed,
                "scope": decision.get("scope"),
                "decision": decision.get("decision"),
                "reference_sha256_matches": reference_valid,
                "verification": verification,
            }
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            reports[milestone] = {
                "passed": False, "scope": None, "decision": None,
                "reference_sha256_matches": False,
            }
    readiness = evaluate_demo_readiness(
        capability_passed=schema_valid,
        decision_reports=reports,
    )
    return {
        "schema": "controlled-demo-decision-ledger-verification-v2",
        "passed": readiness["ready_for_demo_matrix"],
        "ready_for_demo_matrix": readiness["ready_for_demo_matrix"],
        "ready_for_live_matrix": False,
        "schema_valid": schema_valid,
        "decisions": reports,
        "fallback_milestones": readiness["fallback_milestones"],
    }


def resolve_demo_flags(flags: dict, decisions: dict) -> dict:
    resolved = dict(flags)
    fallbacks = []
    for milestone, decision in decisions.items():
        if (
            milestone in MILESTONE_FLAGS
            and decision.get("scope") == "controlled_demo"
            and decision.get("decision") in {"rejected", "inconclusive"}
        ):
            fallbacks.append(milestone)
            for flag in MILESTONE_FLAGS[milestone]:
                if flag in resolved:
                    resolved[flag] = False
    return {
        "flags": resolved,
        "fallback_milestones": sorted(fallbacks),
        "blocked": False,
    }


def build_demo_matrix(feature_matrix: dict, decisions: dict) -> dict:
    combinations = []
    for row in feature_matrix.get("combinations") or []:
        requested = {
            name: (
                value if isinstance(value, bool)
                else str(value).strip().casefold() in {"1", "true", "yes", "on"}
            )
            for name, value in (row.get("flags") or {}).items()
        }
        applicable = {
            milestone: decision
            for milestone, decision in (decisions or {}).items()
            if any(requested.get(flag) is True for flag in MILESTONE_FLAGS.get(milestone, ()))
        }
        resolved = resolve_demo_flags(requested, applicable)
        combinations.append({
            "id": row.get("id"),
            "prerequisites": list(row.get("prerequisites") or []),
            "requested_flags": requested,
            "effective_flags": resolved["flags"],
            "fallback_milestones": resolved["fallback_milestones"],
            "versions": dict(row.get("versions") or {}),
        })
    return {
        "schema": "integrated-demo-feature-matrix-v1",
        "source_schema": feature_matrix.get("schema"),
        "source_version": feature_matrix.get("version"),
        "scope": "controlled_demo",
        "combinations": combinations,
    }


def evaluate_demo_readiness(*, capability_passed: bool, decision_reports: dict) -> dict:
    complete = (
        set(decision_reports or {}) == DEMO_MILESTONES
        and all(
            report.get("passed") is True
            and report.get("scope") == "controlled_demo"
            and report.get("decision") in DECISIONS
            for report in decision_reports.values()
        )
    )
    fallbacks = sorted(
        milestone for milestone, report in (decision_reports or {}).items()
        if report.get("decision") in {"rejected", "inconclusive"}
    )
    blockers = []
    if not capability_passed:
        blockers.append("offline_capability_incomplete")
    if not complete:
        blockers.append("demo_decisions_incomplete")
    return {
        "schema": "integrated-demo-readiness-v1",
        "capability_passed": capability_passed is True,
        "ready_for_demo_matrix": capability_passed is True and complete,
        "ready_for_live_matrix": False,
        "decision_reports": dict(decision_reports or {}),
        "fallback_milestones": fallbacks,
        "blockers": blockers,
    }


def classify_provider_outcome(errors) -> dict:
    normalized = [str(error).casefold() for error in errors or () if str(error).strip()]
    capacity_failure = bool(normalized) and all(
        "no_capacity" in error
        or ("503" in error and "service_unavailable" in error)
        for error in normalized
    )
    if capacity_failure:
        return {
            "decision": "inconclusive",
            "provider_blocked": True,
            "quality_evaluated": False,
            "reason": "provider_capacity_unavailable",
        }
    return {
        "decision": None,
        "provider_blocked": False,
        "quality_evaluated": not normalized,
        "reason": "provider_available" if not normalized else "non_capacity_failure",
    }


__all__ = [
    "build_demo_matrix", "classify_provider_outcome", "evaluate_demo_readiness",
    "resolve_demo_flags",
    "validate_milestone_decision", "verify_demo_decision_ledger",
    "verify_milestone_decision",
]
