"""Prepare and evaluate versioned fail-closed Query Decomposition pilots."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Iterator

from mech_chatbot.governance.artifact_references import build_json_reference
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.governance.feature_activation import SELECTIVE_PROFILE
from mech_chatbot.governance.feature_activation import profile_environment
from mech_chatbot.governance.query_activation_contract import (
    QUERY_PILOT_AUTHORIZATION, validate_query_activation_authorization,
    QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION, query_pilot_authorization,
    query_pilot_duration_bounds,
)
from scripts.ops.query_pilot_review_capture import REVIEW_CAPTURE_DESIGN_SHA256

PILOT_CONTRACT_VERSION = "query-decomposition-24h-100-v1"
SEQUENTIAL_PILOT_CONTRACT_VERSION = QUERY_SEQUENTIAL_PILOT_CONTRACT_VERSION
PILOT_REQUEST_COUNT = 100
PILOT_REVIEW_CAPTURE_COUNT = 20
PILOT_DURATION = timedelta(hours=24)
PILOT_START_DELAY = timedelta(minutes=5)
MINIMUM_AUTHORIZATION_DURATION = timedelta(hours=24, minutes=10)
MAXIMUM_AUTHORIZATION_DURATION = timedelta(hours=26)
PILOT_AUTHORIZATION = QUERY_PILOT_AUTHORIZATION
_EVIDENCE_FIELDS = frozenset({
    "route", "query_result_status", "completion_outcome",
    "refusal_reason_code", "refusal_template_passed",
    "owner_review_required", "security_passed",
    "citation_structure_passed", "provenance_passed", "leakage_detected",
    "planner_calls", "subquery_count", "correction_count", "intent_count",
    "intent_coverage_complete", "deterministic_split_used",
    "intent_overflow", "final_latency_ms", "request_deadline_ms",
    "estimated_cost", "provider_retries", "final_generations",
})


def pilot_evidence_valid(value: object) -> bool:
    """Return whether one Query pilot row is eligible for the final gate."""
    if not isinstance(value, dict) or set(value) != _EVIDENCE_FIELDS:
        return False
    final_latency_ms = value.get("final_latency_ms")
    request_deadline_ms = value.get("request_deadline_ms")
    if type(final_latency_ms) is not int or type(request_deadline_ms) is not int:
        return False
    estimated_cost = value.get("estimated_cost")
    if (
        isinstance(estimated_cost, bool)
        or not isinstance(estimated_cost, (int, float))
        or not math.isfinite(estimated_cost)
    ):
        return False
    valid_latency = 0 <= final_latency_ms <= request_deadline_ms
    common = all((
        value.get("route") == "query_decomposition",
        value.get("security_passed") is True,
        value.get("leakage_detected") is False,
        type(value.get("citation_structure_passed")) is bool,
        type(value.get("provenance_passed")) is bool,
        type(value.get("subquery_count")) is int,
        value.get("subquery_count") in {2, 3},
        type(value.get("intent_count")) is int,
        value.get("intent_count") == value.get("subquery_count"),
        value.get("intent_coverage_complete") is True,
        type(value.get("deterministic_split_used")) is bool,
        value.get("intent_overflow") is False,
        type(value.get("planner_calls")) is int,
        value.get("planner_calls") in {0, 1},
        type(value.get("correction_count")) is int,
        value.get("correction_count") in {0, 1},
        type(value.get("provider_retries")) is int,
        value.get("provider_retries") == 0,
        estimated_cost >= 0,
        valid_latency,
    ))
    answered = all((
        value.get("query_result_status") == "valid",
        value.get("completion_outcome") == "answered",
        value.get("refusal_reason_code") is None,
        value.get("refusal_template_passed") is False,
        value.get("owner_review_required") is False,
        value.get("citation_structure_passed") is True,
        value.get("provenance_passed") is True,
        type(value.get("final_generations")) is int,
        value.get("final_generations") == 1,
    ))
    safe_refusal = all((
        value.get("query_result_status") == "safe_refusal",
        value.get("completion_outcome") == "refused",
        value.get("refusal_reason_code") == "evidence_gate",
        value.get("refusal_template_passed") is True,
        value.get("owner_review_required") is True,
        type(value.get("final_generations")) is int,
        value.get("final_generations") == 0,
    ))
    return common and (answered or safe_refusal)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_digest(value: object) -> bool:
    normalized = str(value or "").strip().casefold()
    return len(normalized) == 64 and not (
        set(normalized) - set("0123456789abcdef")
    )


def _write_json(path: Path, value: object) -> tuple[Path, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(value)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return path, _sha256(raw)


def _reject_duplicate_fields(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("json_duplicate_field")
        value[name] = item
    return value


def _strict_json_loads(raw: str | bytes) -> object:
    return json.loads(raw, object_pairs_hook=_reject_duplicate_fields)


def _read_json(path: str | Path) -> tuple[dict, bytes, Path]:
    resolved = Path(path).resolve()
    raw = resolved.read_bytes()
    value = _strict_json_loads(raw)
    if not isinstance(value, dict):
        raise ValueError("json_artifact_must_be_object")
    return value, raw, resolved


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _dot_local(path: str | Path, root: Path) -> Path:
    resolved = Path(path).resolve()
    if not _inside(resolved, root / ".local"):
        raise ValueError("query_pilot_artifacts_must_stay_under_dot_local")
    return resolved


def _source_commit(root: Path) -> str:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain=v1"], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("source_root_must_be_clean_git_checkout") from exc
    if dirty:
        raise ValueError("source_worktree_must_be_clean")
    return commit


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def _format(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _manifest(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    rows = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        value = _strict_json_loads(line)
        if not isinstance(value, dict):
            raise ValueError("query_manifest_row_invalid")
        rows.append(value)
    complex_rows = [row for row in rows if row.get("evaluation_group") == "complex"]
    simple_rows = [row for row in rows if row.get("evaluation_group") == "simple"]
    identifiers = [str(row.get("id") or "") for row in rows]
    if not all((
        len(rows) == 13,
        len(complex_rows) == 10,
        len(simple_rows) == 3,
        len(set(identifiers)) == 13,
        all(identifiers),
        all(row.get("manifest_schema") == "rag-eval-manifest-v2" for row in rows),
        all(row.get("evaluation_scope") == "query_only" for row in rows),
        all(bool(str(row.get("question") or "").strip()) for row in rows),
    )):
        raise ValueError("query_manifest_contract_invalid")
    return complex_rows, _sha256(raw)


def _schedule_template(
    *, commit: str, bundle_sha: str, manifest_path: Path,
    manifest_sha: str, complex_rows: list[dict], root: Path,
    pilot_contract_version: str = PILOT_CONTRACT_VERSION,
) -> dict:
    contract = query_pilot_authorization(pilot_contract_version)
    duration = timedelta(hours=contract["minimum_elapsed_hours"])
    cards = []
    for index in range(PILOT_REQUEST_COUNT):
        case = complex_rows[index % len(complex_rows)]
        cards.append({
            "card_id": f"query-pilot-{index + 1:03d}",
            "case_id": case["id"],
            "request_sha256": _sha256(
                str(case["question"]).encode("utf-8")
            ),
            "offset_seconds": round(
                index * duration.total_seconds()
                / (PILOT_REQUEST_COUNT - 1)
            ),
            "review_capture_required": index < PILOT_REVIEW_CAPTURE_COUNT,
        })
    return {
        "schema": "query-decomposition-pilot-schedule-template-v1",
        "pilot_contract_version": pilot_contract_version,
        "source_commit": commit,
        "activation_bundle_sha256": bundle_sha,
        "traffic_class": "owner_authorized_operator_generated_fixture",
        "collection": "MechChatbot_CRAG_Eval_v1",
        "manifest": {
            "path": str(manifest_path.resolve().relative_to(root)),
            "sha256": manifest_sha,
        },
        "card_count": PILOT_REQUEST_COUNT,
        "minimum_elapsed_seconds": int(duration.total_seconds()),
        **({"dispatch_mode": contract["dispatch_mode"]} if "dispatch_mode" in contract else {}),
        "max_concurrency": 1,
        "retry_policy": "none",
        "replacement_policy": "none",
        "catch_up_policy": "none",
        "review_capture_card_ids": [
            card["card_id"] for card in cards
            if card["review_capture_required"]
        ],
        "cards": cards,
    }


def _offline_rollback(commit: str) -> dict:
    return {
        "schema": "query-decomposition-pilot-offline-rollback-v1",
        "source_commit": commit,
        "query_decomposition_enabled": False,
        "feature_flags": {},
        "runtime_stop_required": True,
        "target_profile": "all_off",
        "verify_no_enabled_flags": True,
        "preserve_wal_and_artifacts": True,
        "env_file_mutation_required": False,
        "scheduled_task_mutation_required": False,
        "provider_traffic_authorized": False,
    }


def _operator_runbook(commit: str, pilot_contract_version: str = PILOT_CONTRACT_VERSION) -> dict:
    return {
        "schema": "query-decomposition-pilot-operator-runbook-v1",
        "source_commit": commit,
        "launch": {
            "runtime_start_requires_fresh_approval": True,
            "provider_traffic_requires_fresh_approval": True,
            "required_profile": "selective",
            "required_scope": "controlled_demo",
            "required_enabled_flags": ["RAG_QUERY_DECOMPOSITION_ENABLED"],
            "runtime_topology": "candidate_only",
            "control_runtime_started": False,
            "operator_entrypoint": (
                "scripts/ops/query_decomposition_pilot_operator.py"
            ),
            "supervisor_stops_runtime_in_finally": True,
            "health_and_runtime_identity_preflight_required": True,
            "dispatch_contract": pilot_contract_version,
        },
        "rollback": {
            "stop_candidate_runtime": True,
            "target_profile": "all_off",
            "verify_no_enabled_flags": True,
            "preserve_wal_and_artifacts": True,
        },
        "owner_review": {
            "capture_sample_count": PILOT_REVIEW_CAPTURE_COUNT,
            "capture_storage": "dpapi_current_user_ciphertext_only",
            "review_pack_entrypoint": (
                "scripts/ops/query_pilot_review_pack.py"
            ),
            "local_review_entrypoint": "scripts/ops/query_pilot_review_ui.py",
            "metadata_result_required": True,
            "encrypted_capture_deletion_receipt_required": True,
            "rejected_review_deletes_encrypted_captures": True,
            "pilot_acceptance_requires_all_labels_accepted": True,
            "default_rollout_authorized": False,
        },
        "mutations": {
            "env_file": False,
            "scheduled_task": False,
            "git_remote": False,
        },
    }


def _activation_inputs(
    *, root: Path, commit: str, bundle_path: Path, finalization_path: Path,
) -> tuple[str, dict]:
    bundle, bundle_raw, _ = _read_json(bundle_path)
    finalization, _, _ = _read_json(finalization_path)
    bundle_sha = _sha256(bundle_raw)
    authorization = load_json_reference(
        finalization.get("authorization"), root=root,
    )
    expected_flags = {
        name: value == "true" for name, value in profile_environment(
            SELECTIVE_PROFILE, {"RAG_QUERY_DECOMPOSITION_ENABLED"},
        ).items()
    }
    if not all((
        bundle.get("schema") == "rag-activation-bundle-v1",
        bundle.get("scope") == "controlled_demo",
        bundle.get("activation_profile") == "selective",
        bundle.get("source_commit") == commit,
        bundle.get("feature_flags") == expected_flags,
        finalization.get("schema")
        == "query-controlled-demo-activation-finalization-v1",
        finalization.get("source_commit") == commit,
        finalization.get("bundle", {}).get("sha256") == bundle_sha,
        finalization.get("runtime_consumption_authorized") is False,
        finalization.get("runtime_start_authorized") is False,
        finalization.get("provider_traffic_authorized") is False,
        finalization.get("pilot_dispatch_authorized") is False,
        isinstance(authorization, dict),
        (authorization or {}).get("schema")
        == "query-controlled-demo-activation-authorization-v1",
        (authorization or {}).get("source_commit") == commit,
        (authorization or {}).get("feature_activation_authorized") is True,
        bool(str((authorization or {}).get("activation_owner") or "").strip()),
        validate_query_activation_authorization(
            authorization, root=root, source_commit=commit,
        ),
    )):
        raise ValueError("query_activation_evidence_invalid")
    return bundle_sha, authorization


def prepare_pilot_launch_packet(
    *, source_root: str | Path, source_commit: str,
    manifest_path: str | Path, activation_bundle_path: str | Path,
    activation_finalization_path: str | Path, output_dir: str | Path,
    owner: str, pilot_contract_version: str = PILOT_CONTRACT_VERSION,
) -> dict:
    """Prepare every offline artifact before the single pilot approval gate."""
    root = Path(source_root).resolve()
    commit = _source_commit(root)
    if commit != str(source_commit or "").strip():
        raise ValueError("source_commit_mismatch")
    target = _dot_local(output_dir, root)
    if target.exists() and any(target.iterdir()):
        raise ValueError("pilot_output_must_be_empty")
    manifest_path = Path(manifest_path).resolve()
    bundle_path = Path(activation_bundle_path).resolve()
    finalization_path = Path(activation_finalization_path).resolve()
    if not all(_inside(path, root) for path in (
        manifest_path, bundle_path, finalization_path,
    )):
        raise ValueError("pilot_input_outside_source_root")
    complex_rows, manifest_sha = _manifest(manifest_path)
    bundle_sha, activation = _activation_inputs(
        root=root, commit=commit, bundle_path=bundle_path,
        finalization_path=finalization_path,
    )
    contract = query_pilot_authorization(pilot_contract_version)
    normalized_owner = str(owner or "").strip()
    if normalized_owner != activation.get("activation_owner"):
        raise ValueError("pilot_owner_invalid")
    target.mkdir(parents=True, exist_ok=True)
    template = _schedule_template(
        commit=commit, bundle_sha=bundle_sha, manifest_path=manifest_path,
        manifest_sha=manifest_sha, complex_rows=complex_rows, root=root,
        pilot_contract_version=pilot_contract_version,
    )
    template_path, template_sha = _write_json(
        target / "schedule-template.json", template,
    )
    review_path, review_sha = _write_json(target / "review-contract.json", {
        "schema": "query-decomposition-pilot-review-contract-v2",
        "source_commit": commit,
        "review_mode": "single_owner",
        "reviewer": normalized_owner,
        "minimum_review_count": 20,
        "all_invalid_refusal_or_failure_required": True,
        "review_capture": {
            "design_draft_sha256": REVIEW_CAPTURE_DESIGN_SHA256,
            "selected_card_count": PILOT_REVIEW_CAPTURE_COUNT,
            "selection": "two_per_each_complex_case",
            "encryption": "windows_dpapi_current_user",
            "plaintext_on_disk": False,
            "plaintext_in_logs_or_wal": False,
            "deletion_receipt_required": True,
            "bind_consolidated_launch_draft_sha256": True,
        },
        "labels_per_item": [
            "answer_correct", "citation_correct", "safety_correct",
            "decision", "reason_code",
        ],
    })
    rollback_path, rollback_sha = _write_json(
        target / "rollback.json", _offline_rollback(commit),
    )
    runbook_path, runbook_sha = _write_json(
        target / "operator-runbook.json", _operator_runbook(commit, pilot_contract_version),
    )
    draft = {
        "schema": "query-decomposition-pilot-authorization-draft-v1",
        "status": "AWAITING_CONSOLIDATED_PILOT_APPROVAL",
        "source_root": str(root),
        "source_commit": commit,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "owner": normalized_owner,
        "activation_bundle_sha256": bundle_sha,
        "activation_bundle": build_json_reference(
            bundle_path, root=root, expected_schema="rag-activation-bundle-v1",
        ),
        "activation_finalization": build_json_reference(
            finalization_path, root=root,
            expected_schema="query-controlled-demo-activation-finalization-v1",
        ),
        "schedule_template": build_json_reference(
            template_path, root=root,
            expected_schema="query-decomposition-pilot-schedule-template-v1",
        ),
        "review_contract": build_json_reference(
            review_path, root=root,
            expected_schema="query-decomposition-pilot-review-contract-v2",
        ),
        "offline_rollback": build_json_reference(
            rollback_path, root=root,
            expected_schema="query-decomposition-pilot-offline-rollback-v1",
        ),
        "operator_runbook": build_json_reference(
            runbook_path, root=root,
            expected_schema="query-decomposition-pilot-operator-runbook-v1",
        ),
        "requested_authorization": contract,
    }
    draft_path, draft_sha = _write_json(
        target / "pilot-authorization-draft.json", draft,
    )
    packet = {
        "schema": "query-decomposition-pilot-launch-packet-v1",
        "status": "AWAITING_CONSOLIDATED_PILOT_APPROVAL",
        "source_commit": commit,
        "bundle_sha256": bundle_sha,
        "pilot_contract_version": pilot_contract_version,
        "schedule_template": {"path": str(template_path), "sha256": template_sha},
        "review_contract": {"path": str(review_path), "sha256": review_sha},
        "offline_rollback": {"path": str(rollback_path), "sha256": rollback_sha},
        "operator_runbook": {"path": str(runbook_path), "sha256": runbook_sha},
        "pilot_authorization_draft": {
            "path": str(draft_path), "sha256": draft_sha,
        },
        "runtime_started": False,
        "provider_traffic_generated": False,
        "pilot_dispatched": False,
        "next_gate": "one_consolidated_query_pilot_authorization",
    }
    _write_json(target / "launch-packet.json", packet)
    return packet


def finalize_pilot_authorization(
    *, draft_path: str | Path, approval_path: str | Path,
    output_dir: str | Path, now: datetime | None = None,
) -> tuple[dict, str]:
    draft, draft_raw, draft_path = _read_json(draft_path)
    approval, _, approval_path = _read_json(approval_path)
    root = Path(str(draft.get("source_root") or "")).resolve()
    if _source_commit(root) != draft.get("source_commit"):
        raise ValueError("source_commit_mismatch")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    authorized_at = _timestamp(approval.get("authorized_at"))
    expires_at = _timestamp(approval.get("expires_at"))
    duration = expires_at - authorized_at
    version = draft.get("requested_authorization", {}).get("pilot_contract_version")
    contract = query_pilot_authorization(version)
    minimum_duration, maximum_duration = query_pilot_duration_bounds(version)
    draft_sha = _sha256(draft_raw)
    consolidated_draft = approval.get("consolidated_launch_draft")
    pilot_run_root_value = str(approval.get("pilot_run_root") or "")
    pilot_run_root = (root / pilot_run_root_value).resolve()
    consolidated_draft_valid = (
        isinstance(consolidated_draft, dict)
        and set(consolidated_draft) == {"path", "sha256", "schema"}
        and consolidated_draft.get("schema")
        == "query-decomposition-consolidated-launch-draft-v1"
        and _sha256_digest(consolidated_draft.get("sha256"))
    )
    try:
        consolidated_value = load_json_reference(consolidated_draft, root=root)
    except (OSError, TypeError, ValueError):
        consolidated_value = None
    consolidated_binding_valid = bool(
        consolidated_draft_valid
        and isinstance(consolidated_value, dict)
        and consolidated_value.get("source_commit") == draft.get("source_commit")
        and consolidated_value.get("owner") == approval.get("actor")
        and consolidated_value.get("pilot_run_root") == pilot_run_root_value
    )
    if not all((
        draft.get("schema") == "query-decomposition-pilot-authorization-draft-v1",
        approval.get("schema") == "query-decomposition-pilot-approval-v1",
        approval.get("draft_sha256") == draft_sha,
        approval.get("actor") == draft.get("owner"),
        approval.get("authorization") == draft.get("requested_authorization")
        == contract,
        authorized_at <= current <= expires_at,
        minimum_duration <= duration <= maximum_duration,
        consolidated_binding_valid,
        pilot_run_root_value == Path(pilot_run_root_value).as_posix(),
        _inside(pilot_run_root, root / ".local"),
        not os.path.lexists(pilot_run_root),
    )):
        raise ValueError("pilot_approval_invalid")
    target = _dot_local(output_dir, root)
    if target.exists() and any(target.iterdir()):
        raise ValueError("pilot_authorization_output_must_be_empty")
    template = load_json_reference(draft.get("schedule_template"), root=root)
    if not isinstance(template, dict) or template.get("pilot_contract_version") != version:
        raise ValueError("schedule_template_invalid")
    starts_at = authorized_at + PILOT_START_DELAY
    cards = [
        {
            **card,
            "scheduled_at": _format(
                starts_at + timedelta(seconds=card["offset_seconds"])
            ),
        }
        for card in template.get("cards", ())
    ]
    minimum_until = starts_at + timedelta(hours=contract["minimum_elapsed_hours"])
    if not (
        len(cards) == PILOT_REQUEST_COUNT
        and expires_at >= minimum_until + PILOT_START_DELAY
    ):
        raise ValueError("pilot_approval_window_too_short")
    schedule = {
        **template,
        "schema": "query-decomposition-pilot-schedule-v1",
        "authorized_at": _format(authorized_at),
        "starts_at": _format(starts_at),
        "minimum_runtime_until": _format(minimum_until),
        "expires_at": _format(expires_at),
        "cards": cards,
    }
    schedule_path, schedule_sha = _write_json(target / "schedule.json", schedule)
    authorization = {
        "schema": "query-controlled-demo-pilot-authorization-v1",
        "source_commit": draft["source_commit"],
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "activation_bundle_sha256": draft["activation_bundle_sha256"],
        "enabled_flags": ["RAG_QUERY_DECOMPOSITION_ENABLED"],
        "actor": approval["actor"],
        "authorized_at": _format(authorized_at),
        "starts_at": _format(starts_at),
        "minimum_runtime_until": _format(minimum_until),
        "expires_at": _format(expires_at),
        "materialized_at": _format(current),
        "pilot_draft": build_json_reference(
            draft_path, root=root,
            expected_schema="query-decomposition-pilot-authorization-draft-v1",
        ),
        "pilot_approval": build_json_reference(
            approval_path, root=root,
            expected_schema="query-decomposition-pilot-approval-v1",
        ),
        "consolidated_launch_draft": consolidated_draft,
        "pilot_run_root": pilot_run_root_value,
        "schedule": {
            "path": str(schedule_path.relative_to(root)),
            "sha256": schedule_sha,
            "schema": "query-decomposition-pilot-schedule-v1",
        },
        "activation_finalization": draft["activation_finalization"],
        **contract,
    }
    _, digest = _write_json(target / "pilot-authorization.json", authorization)
    return authorization, digest


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("pilot_wal_locked") from exc
    os.close(descriptor)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _wal_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = _strict_json_loads(line)
        if not isinstance(value, dict):
            raise ValueError("pilot_wal_invalid")
        rows.append(value)
    return rows


def _authorization_and_schedule(
    authorization_path: Path, schedule_path: Path,
) -> tuple[dict, str, dict, str]:
    authorization, authorization_raw, _ = _read_json(authorization_path)
    schedule, schedule_raw, _ = _read_json(schedule_path)
    authorization_sha = _sha256(authorization_raw)
    schedule_sha = _sha256(schedule_raw)
    contract = query_pilot_authorization(authorization.get("pilot_contract_version"))
    if not all((
        schedule.get("pilot_contract_version") == contract["pilot_contract_version"],
        authorization.get("schema")
        == "query-controlled-demo-pilot-authorization-v1",
        all(
            authorization.get(name) == value
            for name, value in contract.items()
        ),
        authorization.get("schedule", {}).get("sha256") == schedule_sha,
        schedule.get("schema") == "query-decomposition-pilot-schedule-v1",
        schedule.get("source_commit") == authorization.get("source_commit"),
        schedule.get("activation_bundle_sha256")
        == authorization.get("activation_bundle_sha256"),
    )):
        raise ValueError("pilot_authorization_or_schedule_invalid")
    return authorization, authorization_sha, schedule, schedule_sha


def record_pilot_completion(
    *, schedule_path: str | Path, authorization_path: str | Path,
    wal_path: str | Path, card_id: str, attempted_at: str,
    completed_at: str, trace_id: str, runtime_identity_sha256: str,
    evidence: dict,
) -> dict:
    """Append one metadata-only, exactly-once completion to the pilot WAL."""
    schedule_path = Path(schedule_path).resolve()
    authorization_path = Path(authorization_path).resolve()
    wal_path = Path(wal_path).resolve()
    authorization, auth_sha, schedule, schedule_sha = (
        _authorization_and_schedule(authorization_path, schedule_path)
    )
    sequential = schedule.get("pilot_contract_version") == SEQUENTIAL_PILOT_CONTRACT_VERSION
    cards = schedule.get("cards")
    cards = cards if isinstance(cards, list) else []
    wal_path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(wal_path):
        rows = _wal_rows(wal_path)
        if any(existing.get("card_id") == card_id for existing in rows):
            raise ValueError("card_already_recorded")
        card = cards[len(rows)] if len(rows) < len(cards) else None
        attempted = _timestamp(attempted_at)
        completed = _timestamp(completed_at)
        next_scheduled = (
            _timestamp(cards[len(rows) + 1].get("scheduled_at"))
            if not sequential and len(rows) + 1 < len(cards) else None
        )
        if not all((
            isinstance(card, dict),
            (card or {}).get("card_id") == card_id,
            attempted >= _timestamp((card or {}).get("scheduled_at")),
            next_scheduled is None or attempted < next_scheduled,
            completed >= attempted,
            not sequential or not rows or (
                attempted >= _timestamp(rows[-1].get("completed_at"))
                and all(pilot_evidence_valid(row.get("evidence")) for row in rows)
            ),
            next_scheduled is None or completed <= next_scheduled,
            completed <= _timestamp(authorization.get("expires_at")),
            _sha256_digest(runtime_identity_sha256),
            set(evidence) == _EVIDENCE_FIELDS,
        )):
            raise ValueError("pilot_completion_invalid")
        row = {
            "schema": "query-decomposition-pilot-wal-v1",
            "authorization_sha256": auth_sha,
            "schedule_sha256": schedule_sha,
            "card_id": card_id,
            "case_id": card["case_id"],
            "attempt_number": 1,
            "attempted_at": _format(attempted),
            "completed_at": _format(completed),
            "trace_id_sha256": _sha256(str(trace_id).encode("utf-8")),
            "runtime_identity_sha256": runtime_identity_sha256,
            "evidence": evidence,
        }
        if any(
            existing.get("trace_id_sha256") == row["trace_id_sha256"]
            for existing in rows
        ):
            raise ValueError("trace_already_recorded")
        with wal_path.open("ab") as stream:
            stream.write((json.dumps(row, ensure_ascii=False) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--source-commit", required=True)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--activation-bundle", type=Path, required=True)
    prepare.add_argument("--activation-finalization", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--owner", required=True)
    prepare.add_argument("--pilot-contract-version", default=PILOT_CONTRACT_VERSION, choices=[PILOT_CONTRACT_VERSION, SEQUENTIAL_PILOT_CONTRACT_VERSION])
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--draft", type=Path, required=True)
    finalize.add_argument("--approval", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    record = commands.add_parser("record")
    record.add_argument("--schedule", type=Path, required=True)
    record.add_argument("--authorization", type=Path, required=True)
    record.add_argument("--wal", type=Path, required=True)
    record.add_argument("--card-id", required=True)
    record.add_argument("--attempted-at", required=True)
    record.add_argument("--completed-at", required=True)
    record.add_argument("--trace-id", required=True)
    record.add_argument("--runtime-identity-sha256", required=True)
    record.add_argument("--evidence", type=Path, required=True)
    gate = commands.add_parser("gate")
    gate.add_argument("--schedule", type=Path, required=True)
    gate.add_argument("--authorization", type=Path, required=True)
    gate.add_argument("--wal", type=Path, required=True)
    gate.add_argument("--runtime-identity-sha256", required=True)
    gate.add_argument("--review-pack", type=Path)
    gate.add_argument("--review-result", type=Path)
    gate.add_argument("--deletion-receipt", type=Path)
    gate.add_argument("--capture-dir", type=Path)
    gate.add_argument("--trace", type=Path)
    gate.add_argument("--deletion-journal", type=Path)
    gate.add_argument("--source-root", type=Path)
    gate.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare_pilot_launch_packet(
            source_root=args.source_root, source_commit=args.source_commit,
            manifest_path=args.manifest,
            activation_bundle_path=args.activation_bundle,
            activation_finalization_path=args.activation_finalization,
            output_dir=args.output_dir, owner=args.owner,
            pilot_contract_version=args.pilot_contract_version,
        )
    elif args.command == "finalize":
        result, digest = finalize_pilot_authorization(
            draft_path=args.draft, approval_path=args.approval,
            output_dir=args.output_dir,
        )
        result = {**result, "sha256": digest}
    elif args.command == "record":
        evidence, _, _ = _read_json(args.evidence)
        result = record_pilot_completion(
            schedule_path=args.schedule, authorization_path=args.authorization,
            wal_path=args.wal, card_id=args.card_id,
            attempted_at=args.attempted_at, completed_at=args.completed_at,
            trace_id=args.trace_id,
            runtime_identity_sha256=args.runtime_identity_sha256,
            evidence=evidence,
        )
    else:
        from scripts.ops.query_decomposition_pilot_gate import build_pilot_gate

        result = build_pilot_gate(
            schedule_path=args.schedule, authorization_path=args.authorization,
            wal_path=args.wal,
            runtime_identity_sha256=args.runtime_identity_sha256,
            review_pack_path=args.review_pack,
            review_result_path=args.review_result,
            deletion_receipt_path=args.deletion_receipt,
            capture_dir=args.capture_dir,
            trace_path=args.trace,
            deletion_journal_path=args.deletion_journal,
            source_root=args.source_root,
        )
        _write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
