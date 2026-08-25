"""Prepare and finalize an exact-evidence Query controlled-demo decision."""

from __future__ import annotations

import argparse
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
from mech_chatbot.rag.feature_activation import (
    validate_controlled_demo_decision_ledger,
)
from scripts.decomposition_eval.human_review_pack import pack_sha256
from scripts.ops.build_activation_bundle import build_activation_bundle


REQUESTED_AUTHORIZATION = {
    "materialize_controlled_demo_decision": True,
    "build_offline_activation_bundle": True,
    "provider_traffic_authorized": False,
    "pilot_dispatch_authorized": False,
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


def _inside_dot_local(path: str | Path, root: Path) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to((root / ".local").resolve())
    except ValueError as exc:
        raise ValueError("query controlled-demo artifacts must stay under .local") from exc
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


def prepare_decision_draft(
    *,
    source_root: str | Path,
    disposition_path: str | Path,
    review_result_path: str | Path,
    output: str | Path,
    owner: str,
) -> tuple[dict, str]:
    root = Path(source_root).resolve()
    commit = _source_commit(root)
    disposition, disposition_raw, disposition_path = _json(disposition_path)
    review, _, review_result_path = _json(review_result_path)
    pack_path = review_result_path.with_name("pack.json")
    pack, _, pack_path = _json(pack_path)
    disposition_sha = hashlib.sha256(disposition_raw).hexdigest()
    pack_sha = pack_sha256(pack)
    execution = disposition.get("execution") or {}
    eligibility = disposition.get("evidence_eligibility") or {}
    governance = disposition.get("governance") or {}
    normalized_owner = str(owner or "").strip()
    reviewer = str(review.get("reviewer") or "").strip()

    _require({
        "owner_required": bool(normalized_owner),
        "owner_matches_source_owner": normalized_owner.casefold()
        == str(pack.get("source_owner") or "").strip().casefold(),
        "disposition_schema": disposition.get("schema")
        == "query-decomposition-formal-window-disposition-v2",
        "review_schema": review.get("schema")
        == "query-decomposition-human-review-result-v1",
        "pack_schema": pack.get("schema")
        == "query-decomposition-human-review-pack-v2",
        "source_commit_matches_checkout": disposition.get("source_commit") == commit,
        "review_source_commit": review.get("source_commit") == commit,
        "pack_source_commit": pack.get("source_commit") == commit,
        "run_id_matches": disposition.get("run_id") == review.get("run_id")
        == pack.get("run_id"),
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
        "window_contract_passed": eligibility.get("full_window_contract_passed") is True,
        "technical_eligible": eligibility.get("technical_eligible") is True,
        "predecision_production_false": eligibility.get("production_eligible") is False,
        "predecision_status": eligibility.get("decision_status")
        == "pending_human_review",
        "review_disposition_sha256": review.get("disposition_sha256")
        == disposition_sha == pack.get("disposition_sha256"),
        "review_validation_passed": review.get("validation_passed") is True,
        "review_complete": review.get("review_complete") is True,
        "review_quality_passed": review.get("quality_passed") is True,
        "review_counts": review.get("case_count") == 13
        and review.get("reviewed_output_count") == 39
        and review.get("accepted_count") == 39
        and review.get("rejected_count") == 0
        and review.get("needs_discussion_count") == 0,
        "reviewer_independent": bool(reviewer)
        and reviewer.casefold() != str(pack.get("source_owner") or "").strip().casefold(),
        "pack_hash_matches": review.get("pack_hash_matches") is True
        and review.get("pack_sha256") == pack_sha
        and review.get("expected_pack_sha256") == pack_sha,
        "review_contract_matches": bool(review.get("review_contract_sha256"))
        and review.get("review_contract_sha256") == pack.get("review_contract_sha256"),
        "query_still_off": governance.get("query_decomposition_remains_off") is True
        and review.get("query_decomposition_remains_off") is True,
        "prior_authorizations_false": all(
            item.get(name) is False
            for item in (governance, review, pack)
            for name in (
                "pilot_authorized", "feature_activation_authorized",
                "default_rollout_authorized",
            )
        ),
        "review_push_merge_false": review.get("push_authorized") is False
        and review.get("merge_authorized") is False,
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
    proposed_technical_evidence = {
        "schema": "decomposition-rollout-run-v1",
        "git_sha": commit,
        "run_id": disposition["run_id"],
        "scope": "controlled_demo",
        "passed": True,
        "technical_eligible": True,
        "production_eligible": True,
        "decision_status": "accepted_for_controlled_demo",
        "formal_pair_count": 3,
        "provider_calls": 111,
        "provider_failures": 0,
        "provider_retries": 0,
        "disallowed_fallback_count": 0,
        "human_review": {
            "mode": "independent_human",
            "reviewer": reviewer,
            "accepted_count": 39,
            "rejected_count": 0,
            "needs_discussion_count": 0,
        },
        "evidence": evidence,
        "pilot_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
    }
    draft = {
        "schema": "query-controlled-demo-decision-draft-v1",
        "status": "AWAITING_EXACT_OWNER_APPROVAL",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner": normalized_owner,
        "source_commit": commit,
        "run_id": disposition["run_id"],
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "pilot_contract": "query-decomposition-24h-100-v1",
        "preparation_tool": _preparation_tool_binding(),
        "evidence": evidence,
        "proposed_technical_evidence": proposed_technical_evidence,
        "requested_authorization": dict(REQUESTED_AUTHORIZATION),
        "maximum_approval_lifetime_minutes": MAX_APPROVAL_MINUTES,
        "post_approval_gate": (
            "materialize_and_validate_query_only_decision_ledger_and_offline_bundle"
        ),
    }
    output = _inside_dot_local(output, root)
    if output.exists():
        raise ValueError("draft_output_must_not_exist")
    _, digest = _write_json(output, draft)
    return draft, digest


def finalize_decision(
    *,
    source_root: str | Path,
    draft_path: str | Path,
    approval_path: str | Path,
    output_dir: str | Path,
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

    _require({
        "draft_schema": draft.get("schema")
        == "query-controlled-demo-decision-draft-v1",
        "draft_status": draft.get("status") == "AWAITING_EXACT_OWNER_APPROVAL",
        "draft_source_commit": draft.get("source_commit") == current_commit,
        "preparation_tool_binding": draft.get("preparation_tool")
        == _preparation_tool_binding(),
        "approval_schema": approval.get("schema")
        == "query-controlled-demo-owner-approval-v1",
        "approval_draft_sha256": approval.get("draft_sha256") == draft_sha,
        "approval_actor": approval.get("actor") == draft.get("owner"),
        "approval_authorization_exact": approval.get("authorization")
        == REQUESTED_AUTHORIZATION == draft.get("requested_authorization"),
        "approval_time_order": authorized_at <= observed_at <= expires_at,
        "approval_lifetime": (
            expires_at - authorized_at
        ).total_seconds() <= MAX_APPROVAL_MINUTES * 60,
    })
    for reference in draft["evidence"].values():
        if load_json_reference(reference, root=root) is None:
            raise ValueError("draft_evidence_reference_invalid")

    technical_path, technical_sha = _write_json(
        output_dir / "query-controlled-demo-technical-evidence.json",
        draft["proposed_technical_evidence"],
    )
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "query_decomposition",
        "scope": "controlled_demo",
        "decision": "accepted",
        "source_commit": current_commit,
        "evidence": [
            _reference(
                technical_path, root=root, schema="decomposition-rollout-run-v1",
            ),
            *draft["evidence"].values(),
        ],
        "reason": (
            "Three exact-commit formal pairs passed with 111/111 provider calls "
            "and independent reviewer tran.nghi accepted all 39 outputs."
        ),
        "approval_binding": {
            "draft_path": str(draft_path),
            "draft_sha256": draft_sha,
            "approval_path": str(approval_path),
            "approval_sha256": approval_sha,
        },
        "reviewer_signoff": {
            "reviewer": approval["actor"],
            "signed_at": approval["authorized_at"],
        },
        "pilot_dispatch_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
    }
    decision_path, decision_sha = _write_json(
        output_dir / "query-controlled-demo-decision.json", decision,
    )
    ledger = {
        "schema": "controlled-demo-decision-ledger-v2",
        "status": "complete",
        "decisions": {
            "query_decomposition": _reference(
                decision_path, root=root, schema="milestone-decision-v2",
            )
        },
    }
    ledger_path, ledger_sha = _write_json(
        output_dir / "query-controlled-demo-decision-ledger.json", ledger,
    )
    if not validate_controlled_demo_decision_ledger(
        ledger,
        active_milestones={"query_decomposition"},
        root=root,
        source_commit=current_commit,
    ):
        raise ValueError("query_controlled_demo_decision_ledger_invalid")
    bundle_path = output_dir / "query-controlled-demo-selective-bundle.json"
    _, bundle_sha = build_activation_bundle(
        scope="controlled_demo",
        profile="selective",
        enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        source_commit=current_commit,
        decision_ledger=ledger_path,
        output=bundle_path,
        root=root,
    )
    receipt = {
        "schema": "query-controlled-demo-decision-finalization-v1",
        "source_commit": current_commit,
        "technical_evidence": {"path": str(technical_path), "sha256": technical_sha},
        "decision": {"path": str(decision_path), "sha256": decision_sha},
        "decision_ledger": {"path": str(ledger_path), "sha256": ledger_sha},
        "activation_bundle": {"path": str(bundle_path), "sha256": bundle_sha},
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "next_gate": "fresh_activation_preflight_and_rollback_before_pilot_authorization",
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
