"""Canonical feature flags and fail-closed live activation validation.

This neutral governance module is shared by RAG delivery and evaluation code.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Mapping

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mech_chatbot.governance.artifact_references import (
    inspect_json_reference,
    load_json_reference,
    read_json_object,
    resolve_path,
)
from mech_chatbot.governance.crag_demo_authorization import (
    validate_crag_demo_authorization,
)
from mech_chatbot.governance.query_activation_contract import (
    runtime_consumption_authorization_status,
    validate_query_activation_authorization,
)
from mech_chatbot.governance.review_governance import review_governance_status
from mech_chatbot.governance.rollout_guardrails import (
    validate_rollout_series_artifact,
)


FEATURE_FLAGS = (
    "RAG_CRAG_ENABLED",
    "RAG_CLAIM_REPAIR_ENABLED",
    "RAG_GROUNDED_MATH_ENABLED",
    "RAG_LATE_INTERACTION_ENABLED",
    "RAG_QUERY_DECOMPOSITION_ENABLED",
    "RAG_GRAPH_RETRIEVAL_ENABLED",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
)
VERSION_FIELDS = (
    "RAG_PLANNER_VERSION",
    "RAG_LATE_INDEX_VERSION",
    "RAG_GRAPH_SERVING_EPOCH",
    "RAG_COMMUNITY_SERVING_EPOCH",
)
VERSION_DEFAULTS = {
    "RAG_PLANNER_VERSION": "planner-v1",
    "RAG_LATE_INDEX_VERSION": "late-v2",
    "RAG_GRAPH_SERVING_EPOCH": "graph-v1",
    "RAG_COMMUNITY_SERVING_EPOCH": "community-v1",
}
MILESTONE_FLAGS = {
    "crag": ("RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"),
    "grounded_math": ("RAG_GROUNDED_MATH_ENABLED",),
    "late_interaction": ("RAG_LATE_INTERACTION_ENABLED",),
    "query_decomposition": ("RAG_QUERY_DECOMPOSITION_ENABLED",),
    "graph_retrieval": ("RAG_GRAPH_RETRIEVAL_ENABLED",),
    "community_summaries": ("RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",),
}
FEATURE_MILESTONES = {
    flag: milestone
    for milestone, flags in MILESTONE_FLAGS.items()
    for flag in flags
}
ACTIVATION_PROFILES = {
    "all_off": frozenset(),
    "crag_claim": frozenset(MILESTONE_FLAGS["crag"]),
    "grounded_math": frozenset(
        (*MILESTONE_FLAGS["crag"], *MILESTONE_FLAGS["grounded_math"])
    ),
    "query_decomposition": frozenset(
        (
            *MILESTONE_FLAGS["crag"],
            *MILESTONE_FLAGS["grounded_math"],
            *MILESTONE_FLAGS["query_decomposition"],
        )
    ),
    "graph_retrieval": frozenset(
        (
            *MILESTONE_FLAGS["crag"],
            *MILESTONE_FLAGS["grounded_math"],
            *MILESTONE_FLAGS["query_decomposition"],
            *MILESTONE_FLAGS["graph_retrieval"],
        )
    ),
    "community_summaries": frozenset(
        flag
        for milestone in (
            "crag", "grounded_math", "query_decomposition",
            "graph_retrieval", "community_summaries",
        )
        for flag in MILESTONE_FLAGS[milestone]
    ),
}
SELECTIVE_PROFILE = "selective"
ACTIVATION_PROFILE_NAMES = (*ACTIVATION_PROFILES, SELECTIVE_PROFILE)
ACTIVATION_SCOPES = {"evaluation", "controlled_demo", "default_rollout"}
_TRUTHY = {"1", "true", "yes", "y", "on"}
_RELEASE_SCHEMAS = {
    "RAG_CRAG_ENABLED": "crag-production-pilot-v1",
    "RAG_CLAIM_REPAIR_ENABLED": "crag-production-pilot-v1",
    "RAG_GROUNDED_MATH_ENABLED": "grounded-math-rollout-run-v1",
    "RAG_LATE_INTERACTION_ENABLED": "retrieval-intelligence-gate-v1",
    "RAG_QUERY_DECOMPOSITION_ENABLED": "decomposition-rollout-run-v1",
    "RAG_GRAPH_RETRIEVAL_ENABLED": "graph-rollout-run-v1",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": "retrieval-intelligence-gate-v1",
}
_CONTROLLED_DEMO_SCHEMAS = {
    **{
        milestone: _RELEASE_SCHEMAS[flags[0]]
        for milestone, flags in MILESTONE_FLAGS.items()
    },
    "crag": "crag-controlled-demo-authorization-v1",
    "query_decomposition": "query-controlled-demo-activation-authorization-v1",
    "graph_retrieval": "rollout-guardrail-series-v1",
}
_RUNTIME_CONSUMPTION_ENV = (
    "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH",
    "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256",
)
_RELEASE_AUTHORITY_PUBLIC_KEY = (
    Path("data") / "integrated_hardening_v1"
    / "release-authority-public-key.pem"
)


@dataclass(frozen=True, slots=True)
class ActivationStatus:
    valid: bool
    live_authorized: bool
    scope: str
    reason: str
    enabled_flags: tuple[str, ...]
    profile: str | None = None
    review_mode: str = "multi_reviewer"
    fallback_features: tuple[str, ...] = ()
    decision_source_commit: str | None = None
    bundle_sha256: str | None = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "live_authorized": self.live_authorized,
            "scope": self.scope,
            "reason": self.reason,
            "enabled_flags": list(self.enabled_flags),
            "profile": self.profile,
            "review_mode": self.review_mode,
            "fallback_features": list(self.fallback_features),
            "decision_source_commit": self.decision_source_commit,
            "bundle_sha256": self.bundle_sha256,
        }


def feature_flags(environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    env = os.environ if environ is None else environ
    return {
        name: str(env.get(name, "false")).strip().casefold() in _TRUTHY
        for name in FEATURE_FLAGS
    }


def feature_versions(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    return {
        name: str(env.get(name, default) or default).strip()
        for name, default in VERSION_DEFAULTS.items()
    }


def profile_environment(
    profile: str,
    enabled_features: Iterable[str] | None = None,
) -> dict[str, str]:
    requested = frozenset(enabled_features or ())
    if profile == SELECTIVE_PROFILE:
        if not requested:
            raise ValueError("selective activation requires at least one feature")
        unknown = requested - set(FEATURE_FLAGS)
        if unknown:
            raise ValueError(f"unknown RAG feature flag: {sorted(unknown)[0]}")
        if ("RAG_CRAG_ENABLED" in requested) != (
            "RAG_CLAIM_REPAIR_ENABLED" in requested
        ):
            raise ValueError("CRAG and Claim Repair must be enabled together")
        if "RAG_LATE_INTERACTION_ENABLED" in requested:
            raise ValueError("Late Interaction is rejected")
        if (
            "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED" in requested
            and "RAG_GRAPH_RETRIEVAL_ENABLED" not in requested
        ):
            raise ValueError("Community Summaries requires Graph Retrieval")
        enabled = requested
    elif profile not in ACTIVATION_PROFILES:
        raise ValueError(f"unknown RAG activation profile: {profile}")
    else:
        if requested:
            raise ValueError("enable-feature is only valid for selective activation")
        enabled = ACTIVATION_PROFILES[profile]
    return {name: str(name in enabled).lower() for name in FEATURE_FLAGS}


def current_git_commit(root: str | Path = ".") -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _artifact_commit(artifact: dict) -> str:
    metadata = artifact.get("run_metadata")
    metadata_commit = metadata.get("commit_sha") if isinstance(metadata, dict) else None
    return str(
        artifact.get("git_sha")
        or artifact.get("source_commit")
        or artifact.get("commit_sha")
        or metadata_commit
        or ""
    )


def _artifact_review_mode(artifact: dict, milestone: str) -> str | None:
    governance = artifact.get("review_governance")
    if isinstance(governance, dict) and governance.get("mode"):
        return str(governance["mode"])
    if artifact.get("review_mode"):
        return str(artifact["review_mode"])
    return "multi_reviewer" if milestone == "crag" else None


def _milestone_artifact_valid(
    artifact: dict,
    *,
    milestone: str,
    decision: str,
    source_commit: str,
    review_mode: str,
    root: Path,
    scope: str = "default_rollout",
) -> bool:
    expected_schema = (
        _CONTROLLED_DEMO_SCHEMAS[milestone]
        if scope == "controlled_demo"
        else _RELEASE_SCHEMAS[MILESTONE_FLAGS[milestone][0]]
    )
    if (
        artifact.get("schema") != expected_schema
        or _artifact_commit(artifact) != source_commit
    ):
        return False
    if expected_schema == "crag-controlled-demo-authorization-v1" and not (
        validate_crag_demo_authorization(artifact, root=root).get("passed") is True
    ):
        return False
    if expected_schema == "query-controlled-demo-activation-authorization-v1":
        return validate_query_activation_authorization(
            artifact, root=root, source_commit=source_commit,
        )
    if (
        expected_schema == "retrieval-intelligence-gate-v1"
        and artifact.get("stage") != milestone
    ):
        return False
    if (
        expected_schema == "rollout-guardrail-series-v1"
        and not validate_rollout_series_artifact(
            artifact, stage=milestone, root=root,
        )
    ):
        return False
    artifact_review_mode = _artifact_review_mode(artifact, milestone)
    if (
        scope == "controlled_demo"
        and milestone == "graph_retrieval"
        and artifact_review_mode != review_mode
    ):
        return False
    if (
        not (scope == "controlled_demo" and milestone == "graph_retrieval")
        and artifact_review_mode is not None
        and artifact_review_mode != review_mode
    ):
        return False
    if decision == "accepted":
        return (
            artifact.get("passed") is True
            and artifact.get("production_eligible", True) is True
            and artifact.get("decision", "accepted") == "accepted"
        )
    return decision == "rejected" and (
        artifact.get("passed") is False
        or artifact.get("production_eligible") is False
        or artifact.get("decision") == "rejected"
    )


def _verified_release_evidence(
    flag: str, row: object, root: Path, source_commit: str, review_mode: str,
) -> bool:
    if not isinstance(row, dict):
        return False
    decision = row.get("decision")
    if decision not in {"accepted", "rejected"}:
        return False
    if decision == "rejected" and not str(row.get("reason") or "").strip():
        return False
    row_commit = str(row.get("source_commit") or "")
    historical_late_rejection = (
        flag == "RAG_LATE_INTERACTION_ENABLED" and decision == "rejected"
    )
    if not row_commit or (
        row_commit != source_commit and not historical_late_rejection
    ):
        return False
    artifact = load_json_reference(row.get("evidence"), root=root)
    if artifact is None or artifact.get("schema") != _RELEASE_SCHEMAS[flag]:
        return False
    if (
        decision == "rejected"
        and row_commit == source_commit
        and _milestone_artifact_valid(
            artifact,
            milestone=FEATURE_MILESTONES[flag],
            decision="accepted",
            source_commit=row_commit,
            review_mode=review_mode,
            root=root,
        )
    ):
        return True
    return _milestone_artifact_valid(
        artifact,
        milestone=FEATURE_MILESTONES[flag],
        decision=decision,
        source_commit=row_commit,
        review_mode=review_mode,
        root=root,
    )


def validate_release_decision_ledger(
    ledger: object,
    *,
    root: str | Path,
    source_commit: str,
    expected_enabled: set[str] | frozenset[str] | None = None,
    review_mode: str = "multi_reviewer",
) -> bool:
    if not isinstance(ledger, dict):
        return False
    rows = ledger.get("decisions")
    if not (
        ledger.get("schema") == "integrated-release-decisions-v1"
        and ledger.get("status") == "complete"
        and isinstance(rows, dict)
        and set(rows) == set(FEATURE_FLAGS)
        and all(
            _verified_release_evidence(
                flag, rows.get(flag), Path(root), source_commit,
                review_mode,
            )
            for flag in FEATURE_FLAGS
        )
    ):
        return False
    if expected_enabled is None:
        return True
    accepted = {
        flag for flag in FEATURE_FLAGS
        if rows[flag].get("decision") == "accepted"
    }
    return accepted == set(expected_enabled)


def _release_authority_public_key(
    root: str | Path, source_commit: str,
) -> bytes | None:
    try:
        return subprocess.check_output(
            [
                "git", "show",
                f"{source_commit}:{_RELEASE_AUTHORITY_PUBLIC_KEY.as_posix()}",
            ],
            cwd=Path(root),
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None


def release_signature_valid(bundle: object, *, root: str | Path) -> bool:
    """Verify the release authority signature over the exact decision ledger."""
    if not isinstance(bundle, dict):
        return False
    signature = bundle.get("release_signature")
    reference = bundle.get("decision_ledger")
    if not (
        isinstance(signature, dict)
        and signature.get("algorithm") == "ed25519"
        and isinstance(reference, dict)
    ):
        return False
    try:
        inspected = inspect_json_reference(reference, root=root)
        if inspected is None:
            return False
        _, ledger_bytes, _ = inspected
        public_key_bytes = _release_authority_public_key(
            root, str(bundle.get("source_commit") or ""),
        )
        if public_key_bytes is None:
            return False
        public_key = serialization.load_pem_public_key(
            public_key_bytes
        )
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        encoded = str(signature.get("value") or "").encode("ascii")
        public_key.verify(
            base64.b64decode(encoded, validate=True),
            ledger_bytes,
        )
    except (
        InvalidSignature,
        UnsupportedAlgorithm,
        OSError,
        TypeError,
        ValueError,
        UnicodeEncodeError,
        binascii.Error,
    ):
        return False
    return True


def _accepted_demo_decision(
    milestone: str, reference: object, root: Path, source_commit: str,
    review_mode: str,
) -> bool:
    decision = load_json_reference(reference, root=root)
    if decision is None or not all((
        decision.get("schema") == "milestone-decision-v2",
        decision.get("milestone") == milestone,
        decision.get("scope") == "controlled_demo",
        decision.get("decision") == "accepted",
        decision.get("source_commit") == source_commit,
        bool(str(decision.get("reason") or "").strip()),
    )):
        return False
    evidence = decision.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    technical_gate_accepted = False
    for item in evidence:
        artifact = load_json_reference(item, root=root)
        if artifact is None or _artifact_commit(artifact) != source_commit:
            return False
        if _milestone_artifact_valid(
            artifact,
            milestone=milestone,
            decision="accepted",
            source_commit=source_commit,
            review_mode=review_mode,
            root=root,
            scope="controlled_demo",
        ):
            technical_gate_accepted = True
    signoff = decision.get("reviewer_signoff")
    return technical_gate_accepted and isinstance(signoff, dict) and bool(
        str(signoff.get("reviewer") or "").strip()
        and str(signoff.get("signed_at") or "").strip()
    )


def validate_controlled_demo_decision_ledger(
    ledger: object,
    *,
    active_milestones: set[str] | frozenset[str],
    root: str | Path,
    source_commit: str,
    review_mode: str = "multi_reviewer",
) -> bool:
    if not isinstance(ledger, dict):
        return False
    rows = ledger.get("decisions")
    return (
        ledger.get("schema") == "controlled-demo-decision-ledger-v2"
        and ledger.get("status") == "complete"
        and isinstance(rows, dict)
        and all(
            _accepted_demo_decision(
                milestone,
                rows.get(milestone),
                Path(root),
                source_commit,
                review_mode,
            )
            for milestone in active_milestones
        )
    )


def _status(
    valid: bool, live_authorized: bool, scope: str, reason: str,
    enabled: tuple[str, ...], **kwargs,
) -> ActivationStatus:
    return ActivationStatus(
        valid=valid,
        live_authorized=live_authorized,
        scope=scope,
        reason=reason,
        enabled_flags=enabled,
        **kwargs,
    )


def _activation_preflight(
    env: Mapping[str, str],
    flags: dict[str, bool],
    enabled: tuple[str, ...],
    scope: str,
) -> tuple[ActivationStatus | None, str | None]:
    execution_context = str(env.get("RAG_EXECUTION_CONTEXT", "production")).strip().casefold()
    declared_profile = str(env.get("RAG_ACTIVATION_PROFILE") or "").strip().casefold()
    runtime_authorization_declared = any(
        str(env.get(name) or "").strip() for name in _RUNTIME_CONSUMPTION_ENV
    )
    def invalid(reason: str) -> tuple[ActivationStatus, None]:
        return _status(False, False, scope, reason, enabled), None

    structural_failure = (
        "activation_scope_invalid" if scope not in ACTIVATION_SCOPES
        else "crag_claim_repair_must_match" if flags["RAG_CRAG_ENABLED"] != flags["RAG_CLAIM_REPAIR_ENABLED"]
        else "late_interaction_rejected" if flags["RAG_LATE_INTERACTION_ENABLED"]
        else "community_requires_graph" if flags["RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED"]
        and not flags["RAG_GRAPH_RETRIEVAL_ENABLED"]
        else None
    )
    if structural_failure:
        return invalid(structural_failure)
    if runtime_authorization_declared and scope != "controlled_demo":
        return invalid("runtime_consumption_authorization_scope_invalid")
    enabled_set = frozenset(enabled)
    if declared_profile and (
        declared_profile not in ACTIVATION_PROFILE_NAMES
        or (declared_profile == SELECTIVE_PROFILE and not enabled)
        or enabled_set != ACTIVATION_PROFILES.get(declared_profile, enabled_set)
    ):
        return invalid("activation_profile_invalid")
    if scope == "evaluation":
        if execution_context not in {"evaluation", "test"}:
            return invalid("evaluation_scope_requires_non_live_context")
        return _status(True, False, scope, "evaluation_override", enabled, profile=declared_profile or None), None
    if not enabled:
        bundle_keys = (
            "RAG_ACTIVATION_BUNDLE_PATH", "RAG_ACTIVATION_BUNDLE_SHA256",
            *_RUNTIME_CONSUMPTION_ENV,
        )
        if any(str(env.get(name) or "").strip() for name in bundle_keys):
            return invalid("all_off_bundle_forbidden")
        return _status(True, True, scope, "all_features_disabled", enabled,
                       profile="all_off"), None
    if declared_profile == SELECTIVE_PROFILE:
        profile = SELECTIVE_PROFILE
    elif enabled_set not in set(ACTIVATION_PROFILES.values()):
        return invalid("activation_profile_invalid")
    else:
        profile = declared_profile or next(name for name, profile_flags in ACTIVATION_PROFILES.items()
                                           if profile_flags == enabled_set)
    if flags["RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED"] and not str(env.get(
        "RAG_GRAPH_FINGERPRINT") or "").strip():
        return invalid("community_graph_fingerprint_missing")
    return None, profile


def _activation_evidence_failure(
    scope: str,
    reason: str,
    enabled: tuple[str, ...],
    *,
    profile: str | None = None,
    source_commit: str | None = None,
    digest: str | None = None,
    review_mode: str = "multi_reviewer",
) -> ActivationStatus:
    return _status(
        False, False, scope, reason, enabled,
        profile=profile,
        review_mode=review_mode,
        decision_source_commit=source_commit,
        bundle_sha256=digest,
    )


def _read_activation_bundle(
    env: Mapping[str, str],
    project_root: Path,
    scope: str,
    enabled: tuple[str, ...],
) -> tuple[ActivationStatus | None, dict | None, str | None]:
    bundle_value = str(env.get("RAG_ACTIVATION_BUNDLE_PATH") or "").strip()
    bundle_digest = str(env.get("RAG_ACTIVATION_BUNDLE_SHA256") or "").strip().lower()
    if not bundle_value or len(bundle_digest) != 64:
        return _activation_evidence_failure(
            scope, "activation_bundle_missing", enabled
        ), None, None
    bundle_path = resolve_path(bundle_value, project_root)
    bundle, raw = read_json_object(bundle_path)
    if bundle is None or raw is None:
        return _activation_evidence_failure(
            scope, "activation_bundle_unreadable", enabled
        ), None, None
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != bundle_digest:
        return _activation_evidence_failure(
            scope, "activation_bundle_hash_mismatch", enabled
        ), None, None
    return None, bundle, actual_digest


def _validate_activation_bundle(
    bundle: dict,
    digest: str,
    env: Mapping[str, str],
    project_root: Path,
    flags: dict[str, bool],
    enabled: tuple[str, ...],
    scope: str,
    profile: str,
    current_commit: str | None,
) -> tuple[ActivationStatus | None, dict | None, str]:
    source_commit = str(bundle.get("source_commit") or "")
    deployment_commit = str(env.get("RAG_DEPLOYMENT_GIT_SHA") or "")
    if not all((
        bundle.get("schema") == "rag-activation-bundle-v1",
        bundle.get("scope") == scope,
        source_commit,
        deployment_commit == source_commit,
        current_commit == source_commit,
    )):
        return _activation_evidence_failure(
            scope, "activation_bundle_contract_mismatch", enabled,
            profile=profile,
            source_commit=source_commit or None,
            digest=digest,
        ), None, source_commit
    if (
        bundle.get("activation_profile") != profile
        or bundle.get("feature_flags") != flags
        or bundle.get("versions") != feature_versions(env)
        or (bundle.get("graph_fingerprint") or None)
        != (str(env.get("RAG_GRAPH_FINGERPRINT") or "").strip() or None)
    ):
        return _activation_evidence_failure(
            scope, "activation_bundle_runtime_mismatch", enabled,
            profile=profile,
            source_commit=source_commit,
            digest=digest,
        ), None, source_commit
    ledger = load_json_reference(bundle.get("decision_ledger"), root=project_root)
    if ledger is None:
        return _activation_evidence_failure(
            scope, "decision_ledger_invalid", enabled,
            profile=profile,
            source_commit=source_commit,
            digest=digest,
        ), None, source_commit
    return None, ledger, source_commit


def _resolve_activation_review_mode(
    bundle: dict,
    project_root: Path,
    source_commit: str,
    scope: str,
    enabled: tuple[str, ...],
    profile: str,
    digest: str,
) -> tuple[ActivationStatus | None, str]:
    review_mode = "multi_reviewer"
    if bundle.get("review_governance") is None:
        return None, review_mode
    governance_artifact = load_json_reference(
        bundle.get("review_governance"), root=project_root,
    )
    governance = review_governance_status(
        governance_artifact,
        source_commit=source_commit,
        scope=scope,
    )
    if governance_artifact is None or not governance.valid:
        return _activation_evidence_failure(
            scope, "review_governance_invalid", enabled,
            profile=profile,
            review_mode=governance.mode,
            source_commit=source_commit,
            digest=digest,
        ), governance.mode
    return None, governance.mode


def _authorize_activation(
    bundle: dict,
    ledger: dict,
    project_root: Path,
    source_commit: str,
    scope: str,
    enabled: tuple[str, ...],
    profile: str,
    review_mode: str,
    digest: str,
) -> ActivationStatus:
    reason = "live_decision_not_accepted"
    if scope == "default_rollout":
        rows = ledger.get("decisions") if ledger.get("schema") == "integrated-release-decisions-v1" else None
        ledger_authorized = validate_release_decision_ledger(
            ledger,
            root=project_root,
            source_commit=source_commit,
            expected_enabled=set(enabled),
            review_mode=review_mode,
        )
        signature_valid = (
            ledger_authorized
            and release_signature_valid(bundle, root=project_root)
        )
        authorized = ledger_authorized and signature_valid
        if ledger_authorized and not signature_valid:
            reason = "release_signature_invalid"
        fallbacks = ()
        if authorized:
            fallbacks = tuple(
                flag for flag in FEATURE_FLAGS
                if rows[flag].get("decision") == "rejected"
            )
    else:
        active_milestones = {
            FEATURE_MILESTONES[flag] for flag in enabled
        }
        authorized = validate_controlled_demo_decision_ledger(
            ledger,
            active_milestones=active_milestones,
            root=project_root,
            source_commit=source_commit,
            review_mode=review_mode,
        )
        fallbacks = ()
    return _status(
        authorized,
        authorized,
        scope,
        "live_decisions_accepted" if authorized else reason,
        enabled,
        profile=profile,
        review_mode=review_mode,
        fallback_features=fallbacks,
        decision_source_commit=source_commit,
        bundle_sha256=digest,
    )


def activation_status(
    environ: Mapping[str, str] | None = None,
    *,
    root: str | Path = ".",
    current_commit: str | None = None,
    now: datetime | None = None,
) -> ActivationStatus:
    """Validate one process activation without trusting mutable env flags alone."""
    env = os.environ if environ is None else environ
    project_root = Path(root)
    flags = feature_flags(env)
    enabled = tuple(name for name in FEATURE_FLAGS if flags[name])
    scope = str(env.get("RAG_ACTIVATION_SCOPE", "default_rollout")).strip().casefold()
    failure, profile = _activation_preflight(env, flags, enabled, scope)
    if failure is not None:
        return failure
    assert profile is not None
    failure, bundle, digest = _read_activation_bundle(
        env, project_root, scope, enabled
    )
    if failure is not None:
        return failure
    assert bundle is not None and digest is not None
    failure, ledger, source_commit = _validate_activation_bundle(
        bundle, digest, env, project_root, flags, enabled, scope, profile,
        current_commit,
    )
    if failure is not None:
        return failure
    assert ledger is not None
    failure, review_mode = _resolve_activation_review_mode(
        bundle, project_root, source_commit, scope, enabled, profile, digest
    )
    if failure is not None:
        return failure
    status = _authorize_activation(
        bundle, ledger, project_root, source_commit, scope, enabled, profile,
        review_mode, digest,
    )
    if scope != "controlled_demo" or not status.valid:
        return status
    evaluation_time = now or datetime.now(timezone.utc)
    if evaluation_time.tzinfo is None:
        return _activation_evidence_failure(
            scope, "runtime_consumption_authorization_invalid", enabled,
            profile=profile, review_mode=review_mode,
            source_commit=source_commit, digest=digest,
        )
    runtime_authorization = runtime_consumption_authorization_status(
        env,
        root=project_root,
        source_commit=source_commit,
        activation_bundle_sha256=digest,
        enabled_flags=enabled,
        now=evaluation_time.astimezone(timezone.utc),
    )
    if runtime_authorization == "authorized":
        return status
    if runtime_authorization == "missing":
        return _status(
            True, False, scope, "runtime_consumption_not_authorized", enabled,
            profile=profile, review_mode=review_mode,
            decision_source_commit=source_commit, bundle_sha256=digest,
        )
    reason = (
        "runtime_consumption_authorization_expired"
        if runtime_authorization == "expired"
        else "runtime_consumption_authorization_invalid"
    )
    return _activation_evidence_failure(
        scope, reason, enabled, profile=profile, review_mode=review_mode,
        source_commit=source_commit, digest=digest,
    )


__all__ = [
    "ACTIVATION_PROFILES",
    "ACTIVATION_PROFILE_NAMES",
    "ActivationStatus",
    "FEATURE_FLAGS",
    "MILESTONE_FLAGS",
    "SELECTIVE_PROFILE",
    "VERSION_DEFAULTS",
    "VERSION_FIELDS",
    "activation_status",
    "current_git_commit",
    "feature_flags",
    "feature_versions",
    "profile_environment",
    "release_signature_valid",
    "validate_controlled_demo_decision_ledger",
    "validate_release_decision_ledger",
]
