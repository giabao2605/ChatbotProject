"""Fail-closed Query controlled-demo authorization validators."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from mech_chatbot.governance.artifact_references import (
    load_json_reference,
    read_json_object,
    resolve_path,
)


MAX_APPROVAL_DURATION = timedelta(minutes=60)
QUERY_PILOT_CONTRACT_VERSION = "query-decomposition-24h-100-v1"
QUERY_PILOT_MINIMUM_DURATION = timedelta(hours=24, minutes=10)
QUERY_PILOT_MAXIMUM_DURATION = timedelta(hours=26)
QUERY_OWNER_DECISION_AUTHORIZATION = {
    "materialize_controlled_demo_owner_decision": True,
    "provider_traffic_authorized": False,
    "pilot_dispatch_authorized": False,
    "feature_activation_authorized": False,
    "runtime_start_authorized": False,
    "default_rollout_authorized": False,
    "push_authorized": False,
    "merge_authorized": False,
}
QUERY_ACTIVATION_AUTHORIZATION = {
    "materialize_query_controlled_demo_activation_contract": True,
    "feature_activation_authorized": True,
    "runtime_consumption_authorized": False,
    "runtime_start_authorized": False,
    "provider_traffic_authorized": False,
    "pilot_dispatch_authorized": False,
    "default_rollout_authorized": False,
    "push_authorized": False,
    "merge_authorized": False,
}
QUERY_PILOT_AUTHORIZATION = {
    "runtime_consumption_authorized": True,
    "runtime_start_authorized": True,
    "provider_traffic_authorized": True,
    "pilot_dispatch_authorized": True,
    "default_rollout_authorized": False,
    "push_authorized": False,
    "merge_authorized": False,
    "pilot_contract_version": QUERY_PILOT_CONTRACT_VERSION,
    "eligible_request_count": 100,
    "minimum_elapsed_hours": 24,
    "max_concurrency": 1,
    "retry_policy": "none",
    "replacement_policy": "none",
    "catch_up_policy": "none",
}
QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION = "query-decomposition-sequential-100-v1"


def query_pilot_authorization(version: str) -> dict:
    """Select an explicit contract; never reinterpret historical approval."""
    if version == QUERY_PILOT_CONTRACT_VERSION:
        return dict(QUERY_PILOT_AUTHORIZATION)
    if version == QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION:
        return {
            **QUERY_PILOT_AUTHORIZATION,
            "pilot_contract_version": version,
            "minimum_elapsed_hours": 0,
            "dispatch_mode": "sequential_after_completion",
        }
    raise ValueError("pilot_contract_version_invalid")


def query_pilot_duration_bounds(version: str) -> tuple[timedelta, timedelta]:
    query_pilot_authorization(version)
    if version == QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION:
        return timedelta(minutes=10), timedelta(hours=6)
    return QUERY_PILOT_MINIMUM_DURATION, QUERY_PILOT_MAXIMUM_DURATION


_FALSE_RUNTIME_BOUNDARIES = (
    "provider_traffic_authorized",
    "pilot_dispatch_authorized",
    "default_rollout_authorized",
    "push_authorized",
    "merge_authorized",
)
_ELIGIBILITY_TRUE_FIELDS = (
    "formal_evidence",
    "rollout_evidence",
    "provider_health_passed",
    "query_quality_evaluated",
    "zero_retry_formal_path_exercised",
    "three_pair_gate_passed",
    "full_window_contract_passed",
    "technical_eligible",
)
_GOVERNANCE_FALSE_FIELDS = (
    "provider_smoke_rerun_authorized",
    "retry_or_catch_up_authorized",
    "same_root_reuse_authorized",
    "additional_formal_pairs_authorized",
    "pilot_authorized",
    "feature_activation_authorized",
    "default_rollout_authorized",
    "push_authorized",
    "merge_authorized",
)
_PAIR_IDS = tuple(f"formal-pair-{index:02d}" for index in range(1, 4))
_REQUIRED_ARTIFACT_BINDINGS = {
    "window_disposition",
    "owner_declaration",
    "manifest",
    "offline_preflight",
    "offline_rollback",
    "provider_boundary_preflight",
    "provider_boundary_rollback",
    "provider_smoke",
    *(
        f"{pair_id}_{suffix}"
        for pair_id in _PAIR_IDS
        for suffix in (
            "root_trace",
            "run",
            "rollout_pair",
            "gate",
            "baseline_eval",
            "baseline_trace",
            "baseline_review_capture",
            "candidate_eval",
            "candidate_trace",
            "candidate_review_capture",
        )
    ),
}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _load_contained_reference(reference: object, root: Path) -> dict | None:
    if not isinstance(reference, dict):
        return None
    path = resolve_path(reference.get("path"), root)
    if not _inside(path, root):
        return None
    return load_json_reference(reference, root=root)


def _git_value(root: Path, *arguments: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *arguments], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _trusted_evidence_root(
    project_root: Path, declared_root: object, evidence_source_commit: str,
) -> Path | None:
    evidence_root = resolve_path(declared_root, project_root).resolve()
    if evidence_root == project_root:
        project_commit = _git_value(project_root, "rev-parse", "HEAD")
        project_dirty = _git_value(project_root, "status", "--porcelain=v1")
        if project_commit is None:
            return evidence_root
        return evidence_root if (
            project_commit == evidence_source_commit and project_dirty == ""
        ) else None
    project_common = _git_value(project_root, "rev-parse", "--git-common-dir")
    evidence_common = _git_value(evidence_root, "rev-parse", "--git-common-dir")
    evidence_commit = _git_value(evidence_root, "rev-parse", "HEAD")
    evidence_dirty = _git_value(evidence_root, "status", "--porcelain=v1")
    if not all((project_common, evidence_common, evidence_commit)):
        return None
    project_common_path = resolve_path(project_common, project_root).resolve()
    evidence_common_path = resolve_path(evidence_common, evidence_root).resolve()
    worktrees_root = project_common_path.parent / ".local" / "worktrees"
    if not all((
        project_common_path == evidence_common_path,
        _inside(evidence_root, worktrees_root),
        evidence_commit == evidence_source_commit,
        evidence_dirty == "",
    )):
        return None
    return evidence_root


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _approval_window(
    approval: dict, *, materialized_at: object,
) -> tuple[datetime, datetime] | None:
    authorized_at = _timestamp(approval.get("authorized_at"))
    expires_at = _timestamp(approval.get("expires_at"))
    materialized = _timestamp(materialized_at)
    if not all((authorized_at, expires_at, materialized)):
        return None
    assert authorized_at is not None and expires_at is not None
    assert materialized is not None
    if not (
        authorized_at <= materialized <= expires_at
        and expires_at - authorized_at <= MAX_APPROVAL_DURATION
    ):
        return None
    return authorized_at, expires_at


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _artifact_bindings_valid(
    pack: dict, *, source_root: Path, run_root: Path, disposition_sha: str,
) -> bool:
    bindings = pack.get("artifact_bindings")
    if not isinstance(bindings, dict) or set(bindings) != _REQUIRED_ARTIFACT_BINDINGS:
        return False
    bases = {"source_root": source_root, "run_root": run_root}
    for name, binding in bindings.items():
        expected_base = "source_root" if name == "manifest" else "run_root"
        if not isinstance(binding, dict) or not all((
            set(binding) == {"base", "path", "sha256"},
            binding.get("base") == expected_base,
        )):
            return False
        base = bases[expected_base]
        path = resolve_path(binding.get("path"), base)
        expected_sha = str(binding.get("sha256") or "")
        if not all((
            _inside(path, base),
            path.is_file(),
            len(expected_sha) == 64,
            hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha,
        )):
            return False
    return bindings["window_disposition"]["sha256"] == disposition_sha


def _formal_evidence_valid(
    *, owner: dict, owner_root: Path, source_commit: str,
) -> bool:
    evidence = owner.get("evidence")
    if not isinstance(evidence, dict):
        return False
    disposition = _load_contained_reference(
        evidence.get("window_disposition"), owner_root,
    )
    pack = _load_contained_reference(evidence.get("human_review_pack"), owner_root)
    review = _load_contained_reference(evidence.get("review_result"), owner_root)
    if not all(isinstance(value, dict) for value in (disposition, pack, review)):
        return False
    assert disposition is not None and pack is not None and review is not None
    run_id = str(owner.get("run_id") or "")
    disposition_sha = str(
        evidence["window_disposition"].get("sha256") or ""
    )
    execution = disposition.get("execution") or {}
    eligibility = disposition.get("evidence_eligibility") or {}
    governance = disposition.get("governance") or {}
    reviewer = str(review.get("reviewer") or "").strip()
    source_owner = str(pack.get("source_owner") or "").strip()
    pack_path = resolve_path(evidence["human_review_pack"].get("path"), owner_root)
    run_root = pack_path.parent.parent
    return all((
        disposition.get("schema")
        == "query-decomposition-formal-window-disposition-v2",
        pack.get("schema") == "query-decomposition-human-review-pack-v2",
        review.get("schema") == "query-decomposition-human-review-result-v1",
        all(
            value.get("source_commit") == source_commit
            and value.get("run_id") == run_id
            for value in (disposition, pack, review)
        ),
        disposition.get("status")
        == "completed_technical_eligible_pending_human_review",
        disposition.get("terminal") is True,
        disposition.get("consumed") is True,
        disposition.get("immutable") is True,
        disposition.get("tombstoned") is False,
        execution.get("formal_pairs_started") == 3,
        execution.get("formal_pairs_completed") == 3,
        execution.get("formal_pairs_gate_passed") == 3,
        execution.get("provider_calls") == 111,
        execution.get("provider_successes") == 111,
        execution.get("provider_failures") == 0,
        execution.get("provider_retries") == 0,
        execution.get("disallowed_fallback_count") == 0,
        all(eligibility.get(name) is True for name in _ELIGIBILITY_TRUE_FIELDS),
        eligibility.get("production_eligible") is False,
        eligibility.get("decision_status") == "pending_human_review",
        eligibility.get("reuse_authorized") is False,
        eligibility.get("carry_forward_authorized") is False,
        all(governance.get(name) is False for name in _GOVERNANCE_FALSE_FIELDS),
        governance.get("query_decomposition_remains_off") is True,
        pack.get("scope") == "controlled_demo_quality_review",
        pack.get("capability") == "query_decomposition",
        pack.get("production_eligible") is False,
        review.get("scope") == "controlled_demo_quality_review",
        review.get("capability") == "query_decomposition",
        review.get("review_mode") == "independent_human",
        bool(reviewer) and reviewer.casefold() not in {
            source_owner.casefold(), "codex",
        },
        review.get("disposition_sha256")
        == pack.get("disposition_sha256") == disposition_sha,
        review.get("pack_hash_matches") is True,
        review.get("pack_sha256")
        == review.get("expected_pack_sha256") == _canonical_sha256(pack),
        bool(review.get("review_contract_sha256")),
        review.get("review_contract_sha256") == pack.get("review_contract_sha256"),
        review.get("validation_passed") is True,
        review.get("review_complete") is True,
        review.get("quality_passed") is True,
        review.get("case_count") == 13,
        review.get("reviewed_output_count") == 39,
        review.get("accepted_count") == 39,
        review.get("rejected_count") == 0,
        review.get("needs_discussion_count") == 0,
        review.get("production_eligible") is False,
        owner.get("independent_human_review") == {
            "reviewer": reviewer,
            "evaluated_at": review.get("evaluated_at"),
            "review_result": evidence.get("review_result"),
        },
        _artifact_bindings_valid(
            pack,
            source_root=owner_root,
            run_root=run_root,
            disposition_sha=disposition_sha,
        ),
    ))


def _owner_approval_valid(
    *, owner: dict, owner_reference: dict, owner_root: Path,
) -> bool:
    binding = owner.get("owner_approval")
    if not isinstance(binding, dict):
        return False
    draft = _load_contained_reference(binding.get("draft"), owner_root)
    approval = _load_contained_reference(binding.get("approval"), owner_root)
    if draft is None or approval is None:
        return False
    proposed = {key: value for key, value in owner.items() if key != "owner_approval"}
    window = _approval_window(
        approval, materialized_at=binding.get("accepted_at"),
    )
    return all((
        draft.get("schema") == "query-controlled-demo-owner-decision-draft-v1",
        approval.get("schema") == "query-controlled-demo-owner-approval-v1",
        draft.get("proposed_owner_decision") == proposed,
        draft.get("requested_authorization")
        == QUERY_OWNER_DECISION_AUTHORIZATION,
        approval.get("authorization") == QUERY_OWNER_DECISION_AUTHORIZATION,
        approval.get("draft_sha256") == binding["draft"].get("sha256"),
        approval.get("actor") == binding.get("owner"),
        approval.get("authorized_at") == binding.get("accepted_at"),
        binding.get("scope") == "controlled_demo_owner_decision_only",
        window is not None,
        owner_reference.get("sha256"),
    ))


def _owner_decision_valid(
    artifact: dict, *, project_root: Path, evidence_source_commit: str,
) -> bool:
    owner_root = _trusted_evidence_root(
        project_root,
        artifact.get("owner_decision_root"),
        evidence_source_commit,
    )
    if owner_root is None:
        return False
    owner_reference = artifact.get("owner_decision")
    owner = _load_contained_reference(owner_reference, owner_root)
    receipt = _load_contained_reference(
        artifact.get("owner_decision_finalization"), owner_root,
    )
    if owner is None or receipt is None or not isinstance(owner_reference, dict):
        return False
    false_boundaries = (
        "provider_traffic_authorized",
        "pilot_dispatch_authorized",
        "feature_activation_authorized",
        "runtime_start_authorized",
        "default_rollout_authorized",
        "push_authorized",
        "merge_authorized",
    )
    return all((
        owner.get("schema") == "query-controlled-demo-owner-decision-v1",
        owner.get("source_commit") == evidence_source_commit,
        owner.get("scope") == "controlled_demo",
        owner.get("capability") == "query_decomposition",
        owner.get("status") == "accepted_for_controlled_demo_quality_only",
        owner.get("decision") == "accepted_for_controlled_demo_quality_only",
        owner.get("technical_eligible") is True,
        owner.get("human_review_accepted") is True,
        owner.get("production_eligible") is False,
        all(owner.get(name) is False for name in false_boundaries),
        owner.get("query_decomposition_remains_off") is True,
        receipt.get("schema")
        == "query-controlled-demo-owner-decision-finalization-v1",
        receipt.get("source_commit") == evidence_source_commit,
        receipt.get("owner_decision", {}).get("sha256")
        == owner_reference.get("sha256"),
        all(receipt.get(name) is False for name in false_boundaries),
        _owner_approval_valid(
            owner=owner,
            owner_reference=owner_reference,
            owner_root=owner_root,
        ),
        _formal_evidence_valid(
            owner=owner,
            owner_root=owner_root,
            source_commit=evidence_source_commit,
        ),
    ))


def _query_activation_approval_valid(
    artifact: dict, *, project_root: Path, source_commit: str,
) -> bool:
    draft_reference = artifact.get("activation_draft")
    approval_reference = artifact.get("activation_approval")
    draft = _load_contained_reference(draft_reference, project_root)
    approval = _load_contained_reference(approval_reference, project_root)
    if not all(isinstance(value, dict) for value in (
        draft_reference, approval_reference, draft, approval,
    )):
        return False
    assert draft is not None and approval is not None
    owner_reference = artifact.get("owner_decision")
    receipt_reference = artifact.get("owner_decision_finalization")
    if not isinstance(owner_reference, dict) or not isinstance(
        receipt_reference, dict,
    ):
        return False
    evidence_root = _trusted_evidence_root(
        project_root,
        artifact.get("owner_decision_root"),
        str(artifact.get("evidence_source_commit") or ""),
    )
    owner_decision = (
        _load_contained_reference(owner_reference, evidence_root)
        if evidence_root is not None
        else None
    )
    expected_owner = str(
        (owner_decision or {}).get("owner_approval", {}).get("owner") or ""
    ).strip()
    owner_sha = str(owner_reference.get("sha256") or "")
    receipt_sha = str(
        receipt_reference.get("sha256") or ""
    )
    return all((
        draft.get("schema") == "query-controlled-demo-activation-draft-v1",
        draft.get("source_commit") == source_commit,
        draft.get("evidence_source_commit")
        == artifact.get("evidence_source_commit"),
        draft.get("scope") == "controlled_demo",
        draft.get("capability") == "query_decomposition",
        draft.get("owner_decision_sha256") == owner_sha,
        draft.get("owner_decision_finalization_sha256") == receipt_sha,
        draft.get("requested_authorization") == QUERY_ACTIVATION_AUTHORIZATION,
        approval.get("schema") == "query-controlled-demo-activation-approval-v1",
        approval.get("draft_sha256") == draft_reference.get("sha256"),
        approval.get("authorization") == QUERY_ACTIVATION_AUTHORIZATION,
        bool(str(approval.get("actor") or "").strip()),
        bool(expected_owner),
        approval.get("actor") == draft.get("owner") == expected_owner,
        artifact.get("activation_owner") == approval.get("actor"),
        _approval_window(
            approval, materialized_at=artifact.get("materialized_at"),
        ) is not None,
    ))


def validate_query_activation_authorization(
    artifact: object, *, root: str | Path, source_commit: str,
) -> bool:
    """Validate exact Query evidence and its separate activation approval."""
    if not isinstance(artifact, dict):
        return False
    false_boundaries = (
        "runtime_consumption_authorized",
        "runtime_start_authorized",
        *_FALSE_RUNTIME_BOUNDARIES,
    )
    project_root = Path(root).resolve()
    evidence_source_commit = str(artifact.get("evidence_source_commit") or "")
    return all((
        artifact.get("schema")
        == "query-controlled-demo-activation-authorization-v1",
        artifact.get("source_commit") == source_commit,
        bool(evidence_source_commit),
        artifact.get("scope") == "controlled_demo",
        artifact.get("capability") == "query_decomposition",
        artifact.get("passed") is True,
        artifact.get("decision") == "accepted",
        artifact.get("technical_eligible") is True,
        artifact.get("human_review_accepted") is True,
        artifact.get("production_eligible") is False,
        artifact.get("feature_activation_authorized") is True,
        all(artifact.get(name) is False for name in false_boundaries),
        _owner_decision_valid(
            artifact,
            project_root=project_root,
            evidence_source_commit=evidence_source_commit,
        ),
        _query_activation_approval_valid(
            artifact, project_root=project_root, source_commit=source_commit,
        ),
    ))


def validate_query_owner_decision(
    *, root: str | Path, evidence_root: str | Path,
    evidence_source_commit: str, owner_decision: object,
    owner_decision_finalization: object,
) -> bool:
    """Validate the exact quality decision before drafting activation."""
    return _owner_decision_valid(
        {
            "owner_decision_root": str(evidence_root),
            "owner_decision": owner_decision,
            "owner_decision_finalization": owner_decision_finalization,
        },
        project_root=Path(root).resolve(),
        evidence_source_commit=evidence_source_commit,
    )


def _pilot_schedule_valid(
    schedule: dict, template: dict, *, source_commit: str,
    activation_bundle_sha256: str, authorized_at: datetime,
    expires_at: datetime,
) -> bool:
    version = schedule.get("pilot_contract_version")
    try:
        expected = query_pilot_authorization(version)
    except ValueError:
        return False
    sequential = version == QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION
    cards = schedule.get("cards")
    template_cards = template.get("cards")
    if not isinstance(cards, list) or not isinstance(template_cards, list):
        return False
    if len(cards) != 100 or len(template_cards) != 100:
        return False
    starts_at = _timestamp(schedule.get("starts_at"))
    minimum_until = _timestamp(schedule.get("minimum_runtime_until"))
    if starts_at is None or minimum_until is None:
        return False
    if not all((
        schedule.get("schema") == "query-decomposition-pilot-schedule-v1",
        template.get("schema")
        == "query-decomposition-pilot-schedule-template-v1",
        schedule.get("pilot_contract_version")
        == template.get("pilot_contract_version")
        == version,
        schedule.get("source_commit") == template.get("source_commit")
        == source_commit,
        schedule.get("activation_bundle_sha256")
        == template.get("activation_bundle_sha256")
        == activation_bundle_sha256,
        schedule.get("authorized_at")
        == authorized_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        schedule.get("expires_at")
        == expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        starts_at - authorized_at == timedelta(minutes=5),
        minimum_until - starts_at == timedelta(hours=expected["minimum_elapsed_hours"]),
        schedule.get("card_count") == 100,
        schedule.get("minimum_elapsed_seconds") == expected["minimum_elapsed_hours"] * 3600,
        not sequential or schedule.get("dispatch_mode") == expected["dispatch_mode"],
        schedule.get("max_concurrency") == 1,
        all(
            schedule.get(name) == "none"
            for name in (
                "retry_policy", "replacement_policy", "catch_up_policy",
            )
        ),
    )):
        return False
    for index, (card, template_card) in enumerate(zip(cards, template_cards, strict=True)):
        if not isinstance(card, dict) or not isinstance(template_card, dict):
            return False
        scheduled = _timestamp(card.get("scheduled_at"))
        offset = template_card.get("offset_seconds")
        if not all((
            set(card) == {*template_card, "scheduled_at"},
            all(card.get(name) == value for name, value in template_card.items()),
            type(offset) is int,
            not sequential or offset == 0,
            card.get("card_id") == f"query-pilot-{index + 1:03d}",
            card.get("review_capture_required") is (index < 20),
            scheduled is not None,
            scheduled is not None
            and scheduled == starts_at + timedelta(seconds=offset),
        )):
            return False
    return True


def _pilot_authorization_window(
    authorization: dict, *, project_root: Path, source_commit: str,
    activation_bundle_sha256: str, enabled_flags: tuple[str, ...],
) -> tuple[datetime, datetime] | None:
    draft_reference = authorization.get("pilot_draft")
    approval_reference = authorization.get("pilot_approval")
    schedule_reference = authorization.get("schedule")
    draft = _load_contained_reference(draft_reference, project_root)
    approval = _load_contained_reference(approval_reference, project_root)
    schedule = _load_contained_reference(schedule_reference, project_root)
    if not all(isinstance(value, dict) for value in (
        draft_reference, approval_reference, draft, approval, schedule,
    )):
        return None
    assert draft is not None and approval is not None and schedule is not None
    template = _load_contained_reference(
        draft.get("schedule_template"), project_root,
    )
    finalization = _load_contained_reference(
        draft.get("activation_finalization"), project_root,
    )
    if template is None or finalization is None:
        return None
    activation = _load_contained_reference(
        finalization.get("authorization"), project_root,
    )
    if activation is None:
        return None
    authorized_at = _timestamp(approval.get("authorized_at"))
    expires_at = _timestamp(approval.get("expires_at"))
    materialized_at = _timestamp(authorization.get("materialized_at"))
    if authorized_at is None or expires_at is None or materialized_at is None:
        return None
    duration = expires_at - authorized_at
    try:
        expected_authorization = query_pilot_authorization(authorization.get("pilot_contract_version"))
        minimum_duration, maximum_duration = query_pilot_duration_bounds(authorization.get("pilot_contract_version"))
    except ValueError:
        return None
    expected_flags = sorted(enabled_flags)
    activation_owner = str(activation.get("activation_owner") or "").strip()
    consolidated_approval_reference = approval.get(
        "consolidated_launch_approval"
    )
    consolidated_draft_reference = approval.get("consolidated_launch_draft")
    consolidated_valid = (
        consolidated_approval_reference is None
        and consolidated_draft_reference is None
    )
    if isinstance(consolidated_approval_reference, dict) and isinstance(
        consolidated_draft_reference, dict,
    ):
        consolidated_approval = _load_contained_reference(
            consolidated_approval_reference, project_root,
        )
        consolidated_draft = _load_contained_reference(
            consolidated_draft_reference, project_root,
        )
        consolidated_authorized_at = _timestamp(
            (consolidated_approval or {}).get("authorized_at")
        )
        consolidated_expires_at = _timestamp(
            (consolidated_approval or {}).get("expires_at")
        )
        consolidated_valid = all((
            isinstance(consolidated_approval, dict),
            isinstance(consolidated_draft, dict),
            (consolidated_approval or {}).get("schema")
            == "query-decomposition-consolidated-launch-approval-v1",
            (consolidated_draft or {}).get("schema")
            == "query-decomposition-consolidated-launch-draft-v1",
            (consolidated_draft or {}).get("source_commit") == source_commit,
            (consolidated_draft or {}).get("owner") == approval.get("actor"),
            (consolidated_approval or {}).get("draft_sha256")
            == consolidated_draft_reference.get("sha256"),
            (consolidated_approval or {}).get("actor")
            == approval.get("actor"),
            consolidated_authorized_at is not None
            and consolidated_authorized_at.replace(microsecond=0)
            == authorized_at,
            consolidated_expires_at is not None
            and consolidated_expires_at.replace(microsecond=0)
            == expires_at,
            (consolidated_approval or {}).get("authorization")
            == (consolidated_draft or {}).get("requested_authorization")
            == {
                "activation": QUERY_ACTIVATION_AUTHORIZATION,
                "pilot": expected_authorization,
            },
        ))
    if not all((
        authorization.get("schema")
        == "query-controlled-demo-pilot-authorization-v1",
        authorization.get("source_commit") == source_commit,
        authorization.get("scope") == "controlled_demo",
        authorization.get("capability") == "query_decomposition",
        authorization.get("activation_bundle_sha256")
        == activation_bundle_sha256,
        authorization.get("enabled_flags") == expected_flags
        == ["RAG_QUERY_DECOMPOSITION_ENABLED"],
        all(
            authorization.get(name) == value
            for name, value in expected_authorization.items()
        ),
        authorization.get("actor") == approval.get("actor"),
        authorization.get("authorized_at")
        == authorized_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        authorization.get("expires_at")
        == expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        draft.get("schema")
        == "query-decomposition-pilot-authorization-draft-v1",
        draft.get("source_commit") == source_commit,
        draft.get("scope") == "controlled_demo",
        draft.get("capability") == "query_decomposition",
        draft.get("activation_bundle_sha256") == activation_bundle_sha256,
        draft.get("requested_authorization") == expected_authorization,
        approval.get("schema") == "query-decomposition-pilot-approval-v1",
        approval.get("draft_sha256") == draft_reference.get("sha256"),
        approval.get("authorization") == expected_authorization,
        consolidated_valid,
        bool(activation_owner),
        validate_query_activation_authorization(
            activation, root=project_root, source_commit=source_commit,
        ),
        approval.get("actor") == draft.get("owner") == activation_owner,
        finalization.get("schema")
        == "query-controlled-demo-activation-finalization-v1",
        finalization.get("source_commit") == source_commit,
        finalization.get("bundle", {}).get("sha256")
        == activation_bundle_sha256,
        finalization.get("runtime_consumption_authorized") is False,
        finalization.get("runtime_start_authorized") is False,
        finalization.get("provider_traffic_authorized") is False,
        finalization.get("pilot_dispatch_authorized") is False,
        minimum_duration <= duration <= maximum_duration,
        authorized_at <= materialized_at <= expires_at,
        _pilot_schedule_valid(
            schedule, template, source_commit=source_commit,
            activation_bundle_sha256=activation_bundle_sha256,
            authorized_at=authorized_at, expires_at=expires_at,
        ),
    )):
        return None
    return authorized_at, expires_at


def runtime_consumption_authorization_status(
    environ: Mapping[str, str],
    *,
    root: str | Path,
    source_commit: str,
    activation_bundle_sha256: str,
    enabled_flags: tuple[str, ...],
    now: datetime,
) -> str:
    """Return missing, invalid, expired, or authorized for one exact bundle."""
    path_value = str(
        environ.get("RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH") or ""
    ).strip()
    expected_sha = str(
        environ.get("RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256") or ""
    ).strip().casefold()
    if not path_value and not expected_sha:
        return "missing"
    project_root = Path(root).resolve()
    path = resolve_path(path_value, project_root)
    if not _inside(path, project_root):
        return "invalid"
    authorization, raw = read_json_object(path)
    if not all((
        path_value,
        len(expected_sha) == 64,
        authorization is not None,
        raw is not None,
        raw is not None and hashlib.sha256(raw).hexdigest() == expected_sha,
    )):
        return "invalid"
    assert authorization is not None
    if authorization.get("schema") == "query-controlled-demo-pilot-authorization-v1":
        window = _pilot_authorization_window(
            authorization,
            project_root=project_root,
            source_commit=source_commit,
            activation_bundle_sha256=activation_bundle_sha256,
            enabled_flags=enabled_flags,
        )
        if window is None:
            return "invalid"
        authorized_at, expires_at = window
        evaluation_time = now.astimezone(timezone.utc)
        if evaluation_time < authorized_at:
            return "invalid"
        return "expired" if evaluation_time > expires_at else "authorized"
    draft_reference = authorization.get("runtime_draft")
    approval_reference = authorization.get("runtime_approval")
    draft = _load_contained_reference(draft_reference, project_root)
    approval = _load_contained_reference(approval_reference, project_root)
    if draft is None or approval is None or not isinstance(draft_reference, dict):
        return "invalid"
    preflight = _load_contained_reference(
        draft.get("offline_preflight"), project_root,
    )
    rollback = _load_contained_reference(
        draft.get("offline_rollback"), project_root,
    )
    finalization = _load_contained_reference(
        draft.get("activation_finalization"), project_root,
    )
    if preflight is None or rollback is None or finalization is None:
        return "invalid"
    activation_authorization = _load_contained_reference(
        finalization.get("authorization"), project_root,
    )
    if activation_authorization is None:
        return "invalid"
    activation_owner = str(
        activation_authorization.get("activation_owner") or ""
    ).strip()
    requested = {
        "runtime_consumption_authorized": True,
        "runtime_start_authorized": True,
        **{name: False for name in _FALSE_RUNTIME_BOUNDARIES},
    }
    expected_flags = sorted(enabled_flags)
    window = _approval_window(
        approval, materialized_at=authorization.get("materialized_at"),
    )
    if not all((
        authorization.get("schema")
        == "controlled-demo-runtime-consumption-authorization-v1",
        authorization.get("source_commit") == source_commit,
        authorization.get("scope") == "controlled_demo",
        authorization.get("activation_bundle_sha256")
        == activation_bundle_sha256,
        authorization.get("enabled_flags") == expected_flags,
        authorization.get("authorized_at") == approval.get("authorized_at"),
        authorization.get("expires_at") == approval.get("expires_at"),
        authorization.get("runtime_consumption_authorized") is True,
        authorization.get("runtime_start_authorized") is True,
        all(authorization.get(name) is False for name in _FALSE_RUNTIME_BOUNDARIES),
        draft.get("schema") == "controlled-demo-runtime-consumption-draft-v1",
        draft.get("source_commit") == source_commit,
        draft.get("scope") == "controlled_demo",
        draft.get("activation_bundle_sha256") == activation_bundle_sha256,
        draft.get("enabled_flags") == expected_flags,
        draft.get("requested_authorization") == requested,
        preflight.get("schema") == "query-controlled-demo-offline-preflight-v1",
        preflight.get("source_commit") == source_commit,
        preflight.get("activation_bundle_sha256") == activation_bundle_sha256,
        preflight.get("valid") is True,
        preflight.get("live_authorized") is False,
        preflight.get("reason") == "runtime_consumption_not_authorized",
        rollback.get("schema") == "query-controlled-demo-offline-rollback-v1",
        rollback.get("source_commit") == source_commit,
        rollback.get("query_decomposition_enabled") is False,
        rollback.get("valid") is True,
        rollback.get("live_authorized") is True,
        finalization.get("schema")
        == "query-controlled-demo-activation-finalization-v1",
        finalization.get("source_commit") == source_commit,
        finalization.get("bundle", {}).get("sha256")
        == activation_bundle_sha256,
        finalization.get("offline_preflight", {}).get("sha256")
        == draft.get("offline_preflight", {}).get("sha256"),
        finalization.get("offline_rollback", {}).get("sha256")
        == draft.get("offline_rollback", {}).get("sha256"),
        finalization.get("runtime_consumption_authorized") is False,
        finalization.get("runtime_start_authorized") is False,
        finalization.get("provider_traffic_authorized") is False,
        finalization.get("pilot_dispatch_authorized") is False,
        bool(activation_owner),
        approval.get("schema")
        == "controlled-demo-runtime-consumption-approval-v1",
        approval.get("draft_sha256") == draft_reference.get("sha256"),
        approval.get("authorization") == requested,
        bool(str(approval.get("actor") or "").strip()),
        approval.get("actor") == draft.get("owner") == activation_owner,
        authorization.get("actor") == approval.get("actor"),
        window is not None,
    )):
        return "invalid"
    assert window is not None
    authorized_at, expires_at = window
    evaluation_time = now.astimezone(timezone.utc)
    if evaluation_time < authorized_at:
        return "invalid"
    return "expired" if evaluation_time > expires_at else "authorized"


__all__ = [
    "QUERY_ACTIVATION_AUTHORIZATION",
    "QUERY_OWNER_DECISION_AUTHORIZATION",
    "QUERY_PILOT_AUTHORIZATION",
    "QUERY_PILOT_CONTRACT_VERSION",
    "runtime_consumption_authorization_status",
    "validate_query_activation_authorization",
    "validate_query_owner_decision",
]
