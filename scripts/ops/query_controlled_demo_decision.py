"""Prepare and finalize an exact-evidence Query controlled-demo owner decision."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from mech_chatbot.governance.artifact_references import (
    build_json_reference,
    load_json_reference,
    read_json_object,
)
from scripts.decomposition_eval.human_review_pack import pack_sha256


REQUESTED_AUTHORIZATION = {
    "materialize_controlled_demo_owner_decision": True,
    "provider_traffic_authorized": False,
    "pilot_dispatch_authorized": False,
    "feature_activation_authorized": False,
    "runtime_start_authorized": False,
    "default_rollout_authorized": False,
    "push_authorized": False,
    "merge_authorized": False,
}
MAX_APPROVAL_MINUTES = 60


def _json(path: str | Path) -> tuple[dict, bytes, Path]:
    resolved = Path(path).resolve()
    value, raw = read_json_object(resolved)
    if value is None or raw is None:
        raise ValueError(f"{resolved} must contain a JSON object")
    return value, raw, resolved


def _write_json(path: str | Path, value: dict) -> tuple[Path, str]:
    resolved = Path(path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    resolved.write_bytes(raw)
    return resolved, hashlib.sha256(raw).hexdigest()


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def _inside_dot_local(path: str | Path, root: Path) -> Path:
    resolved = Path(path).resolve()
    if not _inside(resolved, root / ".local"):
        raise ValueError("query controlled-demo artifacts must stay under .local")
    return resolved


def _source_commit(root: Path) -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain=v1"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("source_root_must_be_a_git_checkout") from exc
    if dirty:
        raise ValueError("source_worktree_must_be_clean")
    return commit


def _preparation_tool_binding() -> dict:
    path = Path(__file__).resolve()
    root = path.parents[2]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "git_commit": commit,
    }


def _require(checks: dict[str, bool]) -> None:
    failed = next((name for name, passed in checks.items() if not passed), None)
    if failed:
        raise ValueError(failed)


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("approval_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("approval_timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def _reference(path: Path, *, root: Path, schema: str) -> dict:
    return build_json_reference(path, root=root, expected_schema=schema)


def _validate_disposition(disposition: dict, disposition_sha: str) -> None:
    execution = disposition.get("execution") or {}
    eligibility = disposition.get("evidence_eligibility") or {}
    governance = disposition.get("governance") or {}
    _require({
        "disposition_schema": disposition.get("schema")
        == "query-decomposition-formal-window-disposition-v2",
        "disposition_status": disposition.get("status")
        == "completed_technical_eligible_pending_human_review",
        "disposition_terminal": disposition.get("terminal") is True,
        "disposition_consumed": disposition.get("consumed") is True,
        "disposition_immutable": disposition.get("immutable") is True,
        "disposition_not_tombstoned": disposition.get("tombstoned") is False,
        "formal_pairs_complete": execution.get("formal_pairs_started") == 3
        and execution.get("formal_pairs_completed") == 3
        and execution.get("formal_pairs_gate_passed") == 3,
        "provider_calls_exact": execution.get("provider_calls") == 111
        and execution.get("provider_successes") == 111,
        "provider_failures_zero": execution.get("provider_failures") == 0,
        "provider_retries_zero": execution.get("provider_retries") == 0,
        "disallowed_fallback_zero": execution.get("disallowed_fallback_count") == 0,
        "formal_evidence": eligibility.get("formal_evidence") is True,
        "rollout_evidence": eligibility.get("rollout_evidence") is True,
        "provider_health_passed": eligibility.get("provider_health_passed") is True,
        "query_quality_evaluated": eligibility.get("query_quality_evaluated") is True,
        "zero_retry_formal_path": eligibility.get(
            "zero_retry_formal_path_exercised"
        ) is True,
        "three_pair_gate_passed": eligibility.get("three_pair_gate_passed") is True,
        "window_contract_passed": eligibility.get(
            "full_window_contract_passed"
        ) is True,
        "technical_eligible": eligibility.get("technical_eligible") is True,
        "predecision_production_false": eligibility.get(
            "production_eligible"
        ) is False,
        "predecision_status": eligibility.get("decision_status")
        == "pending_human_review",
        "reuse_not_authorized": eligibility.get("reuse_authorized") is False,
        "carry_forward_not_authorized": eligibility.get(
            "carry_forward_authorized"
        ) is False,
        "governance_fail_closed": all(
            governance.get(name) is False
            for name in (
                "provider_smoke_rerun_authorized", "retry_or_catch_up_authorized",
                "same_root_reuse_authorized", "additional_formal_pairs_authorized",
                "pilot_authorized", "feature_activation_authorized",
                "default_rollout_authorized", "push_authorized", "merge_authorized",
            )
        ),
        "query_still_off": governance.get("query_decomposition_remains_off") is True,
        "disposition_sha256_format": len(disposition_sha) == 64,
    })


def _validate_review(review: dict, pack: dict, disposition_sha: str) -> None:
    canonical_pack_sha = pack_sha256(pack)
    reviewer = str(review.get("reviewer") or "").strip()
    owner = str(pack.get("source_owner") or "").strip()
    _require({
        "review_schema": review.get("schema")
        == "query-decomposition-human-review-result-v1",
        "pack_schema": pack.get("schema")
        == "query-decomposition-human-review-pack-v2",
        "review_scope": review.get("scope") == "controlled_demo_quality_review",
        "review_capability": review.get("capability") == "query_decomposition",
        "review_mode": review.get("review_mode") == "independent_human",
        "reviewer_independent": bool(reviewer)
        and reviewer.casefold() not in {owner.casefold(), "codex"},
        "review_disposition_sha256": review.get("disposition_sha256")
        == pack.get("disposition_sha256") == disposition_sha,
        "pack_hash_matches": review.get("pack_hash_matches") is True
        and review.get("pack_sha256") == canonical_pack_sha
        and review.get("expected_pack_sha256") == canonical_pack_sha,
        "review_contract_matches": bool(review.get("review_contract_sha256"))
        and review.get("review_contract_sha256")
        == pack.get("review_contract_sha256"),
        "review_validation_passed": review.get("validation_passed") is True,
        "review_complete": review.get("review_complete") is True,
        "review_quality_passed": review.get("quality_passed") is True,
        "review_counts": review.get("case_count") == 13
        and review.get("reviewed_output_count") == 39
        and review.get("accepted_count") == 39
        and review.get("rejected_count") == 0
        and review.get("needs_discussion_count") == 0,
        "review_production_false": review.get("production_eligible") is False
        and pack.get("production_eligible") is False,
        "review_authorizations_false": all(
            item.get(name) is False
            for item in (review, pack)
            for name in (
                "pilot_authorized", "feature_activation_authorized",
                "default_rollout_authorized", "push_authorized", "merge_authorized",
            )
        ),
        "review_query_still_off": review.get(
            "query_decomposition_remains_off"
        ) is True and pack.get("query_decomposition_remains_off") is True,
    })


def _artifact_bindings_valid(pack: dict, *, run_root: Path, source_root: Path) -> bool:
    bindings = pack.get("artifact_bindings")
    if not isinstance(bindings, dict) or not bindings:
        return False
    bases = {"run_root": run_root.resolve(), "source_root": source_root.resolve()}
    for binding in bindings.values():
        if not isinstance(binding, dict) or binding.get("base") not in bases:
            return False
        base = bases[binding["base"]]
        path = (base / str(binding.get("path") or "")).resolve()
        expected = str(binding.get("sha256") or "")
        if not _inside(path, base) or not path.is_file() or len(expected) != 64:
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return True


def _validate_exact_identity(
    *, commit: str, disposition: dict, disposition_sha: str, review: dict,
    review_sha: str, pack: dict, expected_source_commit: str,
    expected_run_id: str, expected_disposition_sha256: str,
    expected_review_result_sha256: str, expected_reviewer: str,
) -> None:
    _require({
        "expected_source_commit": commit == expected_source_commit
        == disposition.get("source_commit") == review.get("source_commit")
        == pack.get("source_commit"),
        "expected_run_id": expected_run_id == disposition.get("run_id")
        == review.get("run_id") == pack.get("run_id"),
        "expected_disposition_sha256": disposition_sha
        == expected_disposition_sha256,
        "expected_review_result_sha256": review_sha
        == expected_review_result_sha256,
        "expected_reviewer": str(review.get("reviewer") or "").strip()
        == str(expected_reviewer or "").strip(),
    })


def prepare_decision_draft(
    *, source_root: str | Path, disposition_path: str | Path,
    review_result_path: str | Path, output: str | Path, owner: str,
    expected_source_commit: str, expected_run_id: str,
    expected_disposition_sha256: str, expected_review_result_sha256: str,
    expected_reviewer: str,
) -> tuple[dict, str]:
    root = Path(source_root).resolve()
    commit = _source_commit(root)
    disposition, disposition_raw, disposition_path = _json(disposition_path)
    review, review_raw, review_result_path = _json(review_result_path)
    pack, _, pack_path = _json(review_result_path.with_name("pack.json"))
    disposition_sha = hashlib.sha256(disposition_raw).hexdigest()
    review_sha = hashlib.sha256(review_raw).hexdigest()
    normalized_owner = str(owner or "").strip()
    _validate_exact_identity(
        commit=commit, disposition=disposition, disposition_sha=disposition_sha,
        review=review, review_sha=review_sha, pack=pack,
        expected_source_commit=expected_source_commit,
        expected_run_id=expected_run_id,
        expected_disposition_sha256=expected_disposition_sha256,
        expected_review_result_sha256=expected_review_result_sha256,
        expected_reviewer=expected_reviewer,
    )
    _validate_disposition(disposition, disposition_sha)
    _validate_review(review, pack, disposition_sha)
    _require({
        "owner_required": bool(normalized_owner),
        "owner_matches_source_owner": normalized_owner.casefold()
        == str(pack.get("source_owner") or "").strip().casefold(),
        "pack_artifact_bindings": _artifact_bindings_valid(
            pack, run_root=disposition_path.parent, source_root=root,
        ),
    })
    evidence = {
        "window_disposition": _reference(
            disposition_path, root=root,
            schema="query-decomposition-formal-window-disposition-v2",
        ),
        "human_review_pack": _reference(
            pack_path, root=root,
            schema="query-decomposition-human-review-pack-v2",
        ),
        "review_result": _reference(
            review_result_path, root=root,
            schema="query-decomposition-human-review-result-v1",
        ),
    }
    proposed_decision = {
        "schema": "query-controlled-demo-owner-decision-v1",
        "status": "accepted_for_controlled_demo_quality_only",
        "decision": "accepted_for_controlled_demo_quality_only",
        "source_commit": commit,
        "run_id": disposition["run_id"],
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "pilot_contract": "query-decomposition-24h-100-v1",
        "evidence": evidence,
        "technical_eligible": True,
        "human_review_accepted": True,
        "production_eligible": False,
        "independent_human_review": {
            "reviewer": review["reviewer"],
            "evaluated_at": review["evaluated_at"],
            "review_result": evidence["review_result"],
        },
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "query_decomposition_remains_off": True,
    }
    draft = {
        "schema": "query-controlled-demo-owner-decision-draft-v1",
        "status": "AWAITING_EXACT_OWNER_APPROVAL",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner": normalized_owner,
        "source_commit": commit,
        "run_id": disposition["run_id"],
        "scope": "controlled_demo_owner_decision_only",
        "capability": "query_decomposition",
        "preparation_tool": _preparation_tool_binding(),
        "evidence": evidence,
        "proposed_owner_decision": proposed_decision,
        "requested_authorization": dict(REQUESTED_AUTHORIZATION),
        "maximum_approval_lifetime_minutes": MAX_APPROVAL_MINUTES,
        "post_approval_gate": "materialize_owner_decision_only",
    }
    output = _inside_dot_local(output, root)
    if output.exists():
        raise ValueError("draft_output_must_not_exist")
    _, digest = _write_json(output, draft)
    return draft, digest


def _validate_approval(
    *, draft: dict, draft_sha: str, approval: dict,
    authorized_at: datetime, expires_at: datetime, observed_at: datetime,
) -> None:
    _require({
        "draft_schema": draft.get("schema")
        == "query-controlled-demo-owner-decision-draft-v1",
        "draft_status": draft.get("status") == "AWAITING_EXACT_OWNER_APPROVAL",
        "approval_schema": approval.get("schema")
        == "query-controlled-demo-owner-approval-v1",
        "approval_draft_sha256": approval.get("draft_sha256") == draft_sha,
        "approval_actor": approval.get("actor") == draft.get("owner"),
        "approval_authorization_exact": approval.get("authorization")
        == REQUESTED_AUTHORIZATION == draft.get("requested_authorization"),
        "approval_time_order": authorized_at <= observed_at <= expires_at,
        "approval_lifetime": (expires_at - authorized_at).total_seconds()
        <= MAX_APPROVAL_MINUTES * 60,
    })


def finalize_decision(
    *, source_root: str | Path, draft_path: str | Path,
    approval_path: str | Path, output_dir: str | Path,
    now: datetime | None = None,
) -> dict:
    root = Path(source_root).resolve()
    current_commit = _source_commit(root)
    draft, draft_raw, draft_path = _json(draft_path)
    approval, approval_raw, approval_path = _json(approval_path)
    draft_sha = hashlib.sha256(draft_raw).hexdigest()
    approval_sha = hashlib.sha256(approval_raw).hexdigest()
    authorized_at = _timestamp(approval.get("authorized_at"))
    expires_at = _timestamp(approval.get("expires_at"))
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    output_dir = _inside_dot_local(output_dir, root)
    if output_dir.exists():
        raise ValueError("final_output_dir_must_not_exist")
    _validate_approval(
        draft=draft, draft_sha=draft_sha, approval=approval,
        authorized_at=authorized_at, expires_at=expires_at,
        observed_at=observed_at,
    )
    _require({
        "draft_source_commit": draft.get("source_commit") == current_commit,
        "preparation_tool_binding": draft.get("preparation_tool")
        == _preparation_tool_binding(),
    })
    for reference in draft.get("evidence", {}).values():
        if load_json_reference(reference, root=root) is None:
            raise ValueError("draft_evidence_reference_invalid")
    decision = deepcopy(draft["proposed_owner_decision"])
    decision["owner_approval"] = {
        "owner": approval["actor"],
        "accepted_at": approval["authorized_at"],
        "scope": "controlled_demo_owner_decision_only",
        "draft": {"path": str(draft_path), "sha256": draft_sha},
        "approval": {"path": str(approval_path), "sha256": approval_sha},
    }
    decision_path, decision_sha = _write_json(
        output_dir / "query-controlled-demo-owner-decision.json", decision,
    )
    receipt = {
        "schema": "query-controlled-demo-owner-decision-finalization-v1",
        "source_commit": current_commit,
        "owner_decision": {"path": str(decision_path), "sha256": decision_sha},
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "next_gate": "separate_activation_contract_and_preflight_authorization",
    }
    _write_json(output_dir / "finalization-receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--disposition", type=Path, required=True)
    prepare.add_argument("--review-result", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--owner", required=True)
    prepare.add_argument("--expected-source-commit", required=True)
    prepare.add_argument("--expected-run-id", required=True)
    prepare.add_argument("--expected-disposition-sha256", required=True)
    prepare.add_argument("--expected-review-result-sha256", required=True)
    prepare.add_argument("--expected-reviewer", required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--source-root", type=Path, required=True)
    finalize.add_argument("--draft", type=Path, required=True)
    finalize.add_argument("--approval", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        draft, digest = prepare_decision_draft(
            source_root=args.source_root,
            disposition_path=args.disposition,
            review_result_path=args.review_result,
            output=args.output,
            owner=args.owner,
            expected_source_commit=args.expected_source_commit,
            expected_run_id=args.expected_run_id,
            expected_disposition_sha256=args.expected_disposition_sha256,
            expected_review_result_sha256=args.expected_review_result_sha256,
            expected_reviewer=args.expected_reviewer,
        )
        print(json.dumps({
            "path": str(args.output), "sha256": digest, "status": draft["status"],
        }))
    else:
        print(json.dumps(finalize_decision(
            source_root=args.source_root,
            draft_path=args.draft,
            approval_path=args.approval,
            output_dir=args.output_dir,
        )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
