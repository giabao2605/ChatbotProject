"""Prepare and finalize a Query controlled-demo activation contract offline."""

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
    resolve_path,
)
from mech_chatbot.governance.feature_activation import activation_status
from scripts.ops.build_activation_bundle import build_activation_bundle
from scripts.ops.query_controlled_demo_decision import (
    MAX_APPROVAL_MINUTES,
    _inside_dot_local,
    _json,
    _require,
    _source_commit,
    _timestamp,
    _write_json,
)
from scripts.ops.render_activation_profile import build_profile_environment


QUERY_FLAG = "RAG_QUERY_DECOMPOSITION_ENABLED"
REQUESTED_AUTHORIZATION = {
    "materialize_query_controlled_demo_activation_contract": True,
    "promote_activation_evidence_to_production_eligible": True,
    "build_query_only_live_authorizing_bundle": True,
    "run_offline_activation_preflight": True,
    "materialize_offline_rollback_plan": True,
    "feature_activation_authorized": True,
    "runtime_consumption_authorized": False,
    "environment_mutation_authorized": False,
    "scheduled_task_mutation_authorized": False,
    "provider_traffic_authorized": False,
    "pilot_dispatch_authorized": False,
    "runtime_start_authorized": False,
    "runtime_restart_authorized": False,
    "default_rollout_authorized": False,
    "push_authorized": False,
    "merge_authorized": False,
}


def _tool_binding() -> dict:
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


def _all_false(value: dict, names: tuple[str, ...]) -> bool:
    return all(value.get(name) is False for name in names)


def _validate_owner_decision(decision: dict, *, commit: str, run_id: str, owner: str) -> None:
    approval = decision.get("owner_approval") or {}
    review = decision.get("independent_human_review") or {}
    _require({
        "owner_decision_schema": decision.get("schema")
        == "query-controlled-demo-owner-decision-v1",
        "owner_decision_status": decision.get("status")
        == "accepted_for_controlled_demo_quality_only"
        and decision.get("decision") == "accepted_for_controlled_demo_quality_only",
        "owner_decision_identity": decision.get("source_commit") == commit
        and decision.get("run_id") == run_id
        and decision.get("scope") == "controlled_demo"
        and decision.get("capability") == "query_decomposition"
        and decision.get("pilot_contract") == "query-decomposition-24h-100-v1",
        "owner_decision_quality_accepted": decision.get("technical_eligible") is True
        and decision.get("human_review_accepted") is True
        and decision.get("production_eligible") is False,
        "owner_decision_independent_review": bool(str(review.get("reviewer") or ""))
        and str(review.get("reviewer")).casefold() != owner.casefold(),
        "owner_decision_owner_approval": approval.get("owner") == owner
        and approval.get("scope") == "controlled_demo_owner_decision_only",
        "owner_decision_fail_closed": _all_false(
            decision,
            (
                "provider_traffic_authorized", "pilot_dispatch_authorized",
                "feature_activation_authorized", "runtime_start_authorized",
                "default_rollout_authorized", "push_authorized", "merge_authorized",
            ),
        ) and decision.get("query_decomposition_remains_off") is True,
    })


def _validate_receipt(receipt: dict, *, commit: str, decision_sha: str) -> None:
    reference = receipt.get("owner_decision") or {}
    _require({
        "finalization_receipt_schema": receipt.get("schema")
        == "query-controlled-demo-owner-decision-finalization-v1",
        "finalization_receipt_identity": receipt.get("source_commit") == commit
        and reference.get("sha256") == decision_sha,
        "finalization_receipt_fail_closed": _all_false(
            receipt,
            (
                "provider_traffic_authorized", "pilot_dispatch_authorized",
                "feature_activation_authorized", "runtime_start_authorized",
                "default_rollout_authorized", "push_authorized", "merge_authorized",
            ),
        ),
        "finalization_receipt_next_gate": receipt.get("next_gate")
        == "separate_activation_contract_and_preflight_authorization",
    })


def _load_inputs(
    *, root: Path, owner_decision_path: str | Path,
    finalization_receipt_path: str | Path, expected_source_commit: str,
    expected_run_id: str, expected_owner_decision_sha256: str,
    expected_finalization_receipt_sha256: str, owner: str,
) -> dict:
    commit = _source_commit(root)
    decision, decision_raw, decision_path = _json(owner_decision_path)
    receipt, receipt_raw, receipt_path = _json(finalization_receipt_path)
    _inside_dot_local(decision_path, root)
    _inside_dot_local(receipt_path, root)
    decision_sha = hashlib.sha256(decision_raw).hexdigest()
    receipt_sha = hashlib.sha256(receipt_raw).hexdigest()
    _require({
        "expected_source_commit": commit == expected_source_commit,
        "expected_run_id": decision.get("run_id") == expected_run_id,
        "expected_owner_decision_sha256": decision_sha
        == expected_owner_decision_sha256,
        "expected_finalization_receipt_sha256": receipt_sha
        == expected_finalization_receipt_sha256,
        "owner_required": bool(str(owner or "").strip()),
    })
    _validate_owner_decision(
        decision, commit=commit, run_id=expected_run_id, owner=owner,
    )
    _validate_receipt(receipt, commit=commit, decision_sha=decision_sha)
    for reference in (decision.get("evidence") or {}).values():
        _inside_dot_local(resolve_path(reference.get("path"), root), root)
        if load_json_reference(reference, root=root) is None:
            raise ValueError("owner_decision_evidence_reference_invalid")
    return {
        "commit": commit, "run_id": expected_run_id, "owner": owner,
        "decision": decision, "decision_path": decision_path,
        "receipt": receipt, "receipt_path": receipt_path,
    }


def _source_evidence(context: dict, root: Path) -> dict:
    return {
        "owner_decision": build_json_reference(
            context["decision_path"], root=root,
            expected_schema="query-controlled-demo-owner-decision-v1",
        ),
        "owner_decision_finalization_receipt": build_json_reference(
            context["receipt_path"], root=root,
            expected_schema="query-controlled-demo-owner-decision-finalization-v1",
        ),
    }


def prepare_activation_draft(
    *, source_root: str | Path, owner_decision_path: str | Path,
    finalization_receipt_path: str | Path, output: str | Path, owner: str,
    expected_source_commit: str, expected_run_id: str,
    expected_owner_decision_sha256: str,
    expected_finalization_receipt_sha256: str,
) -> tuple[dict, str]:
    root = Path(source_root).resolve()
    context = _load_inputs(
        root=root, owner_decision_path=owner_decision_path,
        finalization_receipt_path=finalization_receipt_path,
        expected_source_commit=expected_source_commit,
        expected_run_id=expected_run_id,
        expected_owner_decision_sha256=expected_owner_decision_sha256,
        expected_finalization_receipt_sha256=expected_finalization_receipt_sha256,
        owner=str(owner or "").strip(),
    )
    draft = {
        "schema": "query-controlled-demo-activation-contract-draft-v1",
        "status": "AWAITING_EXACT_OWNER_APPROVAL",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner": context["owner"], "source_commit": context["commit"],
        "run_id": context["run_id"], "scope": "controlled_demo",
        "capability": "query_decomposition",
        "pilot_contract": "query-decomposition-24h-100-v1",
        "preparation_tool": _tool_binding(),
        "source_evidence": _source_evidence(context, root),
        "activation_contract": {
            "profile": "selective", "enabled_features": [QUERY_FLAG],
            "source_owner_decision_production_eligible": False,
            "activation_evidence_production_eligible": True,
            "promotion_scope": "controlled_demo_activation_contract_only",
            "bundle_becomes_live_authorizing_only_if_consumed_by_runtime": True,
            "runtime_consumption_authorized": False,
        },
        "requested_authorization": dict(REQUESTED_AUTHORIZATION),
        "maximum_approval_lifetime_minutes": MAX_APPROVAL_MINUTES,
        "post_approval_stop": "offline_artifacts_ready_runtime_still_unchanged",
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
        == "query-controlled-demo-activation-contract-draft-v1",
        "draft_status": draft.get("status") == "AWAITING_EXACT_OWNER_APPROVAL",
        "approval_schema": approval.get("schema")
        == "query-controlled-demo-activation-owner-approval-v1",
        "approval_draft_sha256": approval.get("draft_sha256") == draft_sha,
        "approval_actor": approval.get("actor") == draft.get("owner"),
        "approval_authorization_exact": approval.get("authorization")
        == REQUESTED_AUTHORIZATION == draft.get("requested_authorization"),
        "approval_time_order": authorized_at <= observed_at <= expires_at,
        "approval_lifetime": (expires_at - authorized_at).total_seconds()
        <= MAX_APPROVAL_MINUTES * 60,
    })


def _reload_draft_sources(draft: dict, *, root: Path) -> dict:
    references = draft.get("source_evidence") or {}
    _require({
        "draft_source_evidence_keys": set(references) == {
            "owner_decision", "owner_decision_finalization_receipt",
        },
    })
    for reference in references.values():
        _inside_dot_local(resolve_path(reference.get("path"), root), root)
    decision = load_json_reference(references["owner_decision"], root=root)
    receipt = load_json_reference(
        references["owner_decision_finalization_receipt"], root=root,
    )
    if decision is None or receipt is None:
        raise ValueError("draft_source_evidence_reference_invalid")
    _validate_owner_decision(
        decision, commit=draft["source_commit"], run_id=draft["run_id"],
        owner=draft["owner"],
    )
    _validate_receipt(
        receipt, commit=draft["source_commit"],
        decision_sha=references["owner_decision"]["sha256"],
    )
    for reference in (decision.get("evidence") or {}).values():
        _inside_dot_local(resolve_path(reference.get("path"), root), root)
        if load_json_reference(reference, root=root) is None:
            raise ValueError("owner_decision_evidence_reference_invalid")
    return {"decision": decision, "receipt": receipt}


def _activation_evidence(
    *, draft: dict, approval: dict,
    draft_path: Path, draft_sha: str, approval_path: Path, approval_sha: str,
) -> dict:
    source_evidence = draft["source_evidence"]
    return {
        "schema": "decomposition-rollout-run-v1",
        "git_sha": draft["source_commit"], "run_id": draft["run_id"],
        "scope": "controlled_demo", "passed": True,
        "technical_eligible": True, "production_eligible": True,
        "decision": "accepted",
        "promotion_scope": "controlled_demo_activation_contract_only",
        "owner_decision": source_evidence["owner_decision"],
        "owner_decision_receipt": source_evidence[
            "owner_decision_finalization_receipt"
        ],
        "activation_approval": {
            "owner": approval["actor"], "approved_at": approval["authorized_at"],
            "draft": {"path": str(draft_path), "sha256": draft_sha},
            "approval": {"path": str(approval_path), "sha256": approval_sha},
        },
        "feature_activation_authorized": True,
        "runtime_consumption_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
    }


def _write_activation_contract(
    *, output_dir: Path, root: Path, draft: dict, approval: dict,
    draft_path: Path, draft_sha: str, approval_path: Path, approval_sha: str,
) -> dict:
    evidence = _activation_evidence(
        draft=draft, approval=approval, draft_path=draft_path,
        draft_sha=draft_sha, approval_path=approval_path,
        approval_sha=approval_sha,
    )
    evidence_path, evidence_sha = _write_json(
        output_dir / "query-controlled-demo-activation-evidence.json", evidence,
    )
    decision = {
        "schema": "milestone-decision-v2", "milestone": "query_decomposition",
        "scope": "controlled_demo", "decision": "accepted",
        "source_commit": draft["source_commit"],
        "evidence": [build_json_reference(
            evidence_path, root=root, expected_schema="decomposition-rollout-run-v1",
        )],
        "reason": "Exact owner-approved Query controlled-demo activation contract.",
        "reviewer_signoff": {
            "reviewer": approval["actor"], "signed_at": approval["authorized_at"],
        },
    }
    decision_path, decision_sha = _write_json(
        output_dir / "query-controlled-demo-activation-decision.json", decision,
    )
    ledger = {
        "schema": "controlled-demo-decision-ledger-v2", "status": "complete",
        "decisions": {
            "query_decomposition": build_json_reference(
                decision_path, root=root, expected_schema="milestone-decision-v2",
            ),
        },
    }
    ledger_path, ledger_sha = _write_json(
        output_dir / "query-controlled-demo-decision-ledger.json", ledger,
    )
    return {
        "activation_evidence": {"path": str(evidence_path), "sha256": evidence_sha},
        "activation_decision": {"path": str(decision_path), "sha256": decision_sha},
        "decision_ledger": {"path": str(ledger_path), "sha256": ledger_sha},
    }


def _write_offline_preflight(
    *, output_dir: Path, root: Path, draft: dict,
    bundle_path: Path, bundle_sha: str,
) -> dict:
    environment = build_profile_environment(
        profile="selective", scope="controlled_demo",
        activation_bundle=bundle_path, activation_bundle_sha256=bundle_sha,
    )
    status = activation_status(
        environment, root=root, current_commit=draft["source_commit"],
    )
    _require({
        "offline_activation_preflight": status.valid and status.live_authorized
        and set(status.enabled_flags) == {QUERY_FLAG},
    })
    preflight = {
        "schema": "query-controlled-demo-offline-activation-preflight-v1",
        "source_commit": draft["source_commit"],
        "activation_bundle": {"path": str(bundle_path), "sha256": bundle_sha},
        "valid": status.valid, "live_authorized": status.live_authorized,
        "scope": status.scope, "profile": status.profile,
        "enabled_flags": list(status.enabled_flags), "reason": status.reason,
        "runtime_consumed": False, "runtime_started": False,
    }
    path, digest = _write_json(
        output_dir / "offline-activation-preflight.json", preflight,
    )
    return {"path": str(path), "sha256": digest}


def _write_offline_rollback(*, output_dir: Path, root: Path, draft: dict) -> dict:
    rollback_environment = build_profile_environment(
        profile="all_off", scope="controlled_demo",
    )
    rollback_status = activation_status(
        rollback_environment, root=root, current_commit=draft["source_commit"],
    )
    rollback = {
        "schema": "query-controlled-demo-offline-rollback-plan-v1",
        "source_commit": draft["source_commit"], "profile": "all_off",
        "valid": rollback_status.valid,
        "feature_flags": {
            name: value == "true"
            for name, value in rollback_environment.items()
            if name.startswith("RAG_") and name.endswith("_ENABLED")
        },
        "runtime_applied": False, "runtime_restart_authorized": False,
    }
    _require({
        "offline_rollback_all_off": rollback_status.valid
        and not any(rollback["feature_flags"].values()),
    })
    path, digest = _write_json(
        output_dir / "offline-rollback-plan.json", rollback,
    )
    return {"path": str(path), "sha256": digest}


def _write_bundle_and_checks(
    *, output_dir: Path, root: Path, draft: dict, artifacts: dict,
) -> dict:
    bundle_path = output_dir / "query-only-controlled-demo-bundle.json"
    _, bundle_sha = build_activation_bundle(
        scope="controlled_demo", profile="selective",
        enabled_features={QUERY_FLAG}, source_commit=draft["source_commit"],
        decision_ledger=artifacts["decision_ledger"]["path"],
        output=bundle_path, root=root,
    )
    return {
        "activation_bundle": {"path": str(bundle_path), "sha256": bundle_sha},
        "offline_preflight": _write_offline_preflight(
            output_dir=output_dir, root=root, draft=draft,
            bundle_path=bundle_path, bundle_sha=bundle_sha,
        ),
        "offline_rollback": _write_offline_rollback(
            output_dir=output_dir, root=root, draft=draft,
        ),
    }


def _validated_finalize_context(
    *, source_root: str | Path, draft_path: str | Path,
    approval_path: str | Path, output_dir: str | Path,
    now: datetime | None = None,
) -> dict:
    root = Path(source_root).resolve()
    commit = _source_commit(root)
    draft, draft_raw, draft_path = _json(draft_path)
    approval, approval_raw, approval_path = _json(approval_path)
    _inside_dot_local(draft_path, root)
    _inside_dot_local(approval_path, root)
    draft_sha = hashlib.sha256(draft_raw).hexdigest()
    approval_sha = hashlib.sha256(approval_raw).hexdigest()
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _validate_approval(
        draft=draft, draft_sha=draft_sha, approval=approval,
        authorized_at=_timestamp(approval.get("authorized_at")),
        expires_at=_timestamp(approval.get("expires_at")), observed_at=observed_at,
    )
    _require({
        "draft_source_commit": draft.get("source_commit") == commit,
        "preparation_tool_binding": draft.get("preparation_tool") == _tool_binding(),
        "activation_contract_exact": draft.get("activation_contract") == {
            "profile": "selective", "enabled_features": [QUERY_FLAG],
            "source_owner_decision_production_eligible": False,
            "activation_evidence_production_eligible": True,
            "promotion_scope": "controlled_demo_activation_contract_only",
            "bundle_becomes_live_authorizing_only_if_consumed_by_runtime": True,
            "runtime_consumption_authorized": False,
        },
    })
    _reload_draft_sources(draft, root=root)
    output_dir = _inside_dot_local(output_dir, root)
    if output_dir.exists():
        raise ValueError("final_output_dir_must_not_exist")
    return {
        "root": root, "commit": commit, "draft": draft,
        "draft_path": draft_path, "draft_sha": draft_sha,
        "approval": approval, "approval_path": approval_path,
        "approval_sha": approval_sha, "output_dir": output_dir,
    }


def finalize_activation_contract(
    *, source_root: str | Path, draft_path: str | Path,
    approval_path: str | Path, output_dir: str | Path,
    now: datetime | None = None,
) -> dict:
    context = _validated_finalize_context(
        source_root=source_root, draft_path=draft_path,
        approval_path=approval_path, output_dir=output_dir, now=now,
    )
    root = context["root"]
    draft = context["draft"]
    approval = context["approval"]
    output_dir = context["output_dir"]
    artifacts = _write_activation_contract(
        output_dir=output_dir, root=root, draft=draft, approval=approval,
        draft_path=context["draft_path"], draft_sha=context["draft_sha"],
        approval_path=context["approval_path"],
        approval_sha=context["approval_sha"],
    )
    artifacts.update(_write_bundle_and_checks(
        output_dir=output_dir, root=root, draft=draft, artifacts=artifacts,
    ))
    receipt = {
        "schema": "query-controlled-demo-activation-finalization-v1",
        "source_commit": context["commit"], **artifacts,
        "source_owner_decision_production_eligible": False,
        "activation_evidence_production_eligible": True,
        "feature_activation_authorized": True,
        "runtime_consumption_authorized": False,
        "environment_mutation_authorized": False,
        "scheduled_task_mutation_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "runtime_start_authorized": False,
        "runtime_restart_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False, "merge_authorized": False,
        "next_gate": "separate_runtime_start_and_query_pilot_authorization",
    }
    _write_json(output_dir / "finalization-receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--owner-decision", type=Path, required=True)
    prepare.add_argument("--finalization-receipt", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--owner", required=True)
    prepare.add_argument("--expected-source-commit", required=True)
    prepare.add_argument("--expected-run-id", required=True)
    prepare.add_argument("--expected-owner-decision-sha256", required=True)
    prepare.add_argument("--expected-finalization-receipt-sha256", required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--source-root", type=Path, required=True)
    finalize.add_argument("--draft", type=Path, required=True)
    finalize.add_argument("--approval", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        draft, digest = prepare_activation_draft(
            source_root=args.source_root, owner_decision_path=args.owner_decision,
            finalization_receipt_path=args.finalization_receipt,
            output=args.output, owner=args.owner,
            expected_source_commit=args.expected_source_commit,
            expected_run_id=args.expected_run_id,
            expected_owner_decision_sha256=args.expected_owner_decision_sha256,
            expected_finalization_receipt_sha256=(
                args.expected_finalization_receipt_sha256
            ),
        )
        print(json.dumps({
            "path": str(args.output), "sha256": digest, "status": draft["status"],
        }))
    else:
        print(json.dumps(finalize_activation_contract(
            source_root=args.source_root, draft_path=args.draft,
            approval_path=args.approval, output_dir=args.output_dir,
        )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
