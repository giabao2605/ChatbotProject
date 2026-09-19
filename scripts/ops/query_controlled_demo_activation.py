"""Materialize Query controlled-demo contracts only after exact approvals."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from mech_chatbot.governance.artifact_references import (
    build_json_reference,
    load_json_reference,
    read_json_object,
)
from mech_chatbot.governance.feature_activation import (
    FEATURE_FLAGS,
    VERSION_DEFAULTS,
    activation_status,
)
from mech_chatbot.governance.query_activation_contract import (
    QUERY_ACTIVATION_AUTHORIZATION,
    validate_query_owner_decision,
)
from scripts.ops.build_activation_bundle import build_activation_bundle


MAX_APPROVAL_DURATION = timedelta(minutes=60)
RUNTIME_AUTHORIZATION = {
    "runtime_consumption_authorized": True,
    "runtime_start_authorized": True,
    "provider_traffic_authorized": False,
    "pilot_dispatch_authorized": False,
    "default_rollout_authorized": False,
    "push_authorized": False,
    "merge_authorized": False,
}


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
    temporary = resolved.with_suffix(resolved.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(resolved)
    return resolved, hashlib.sha256(raw).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _dot_local_output(path: str | Path, root: Path) -> Path:
    resolved = Path(path).resolve()
    if not _inside(resolved, root / ".local"):
        raise ValueError("Query activation artifacts must stay under .local")
    return resolved


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("approval_timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("approval_timestamp_invalid")
    return parsed.astimezone(timezone.utc)


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


def _approval(
    *, draft: dict, draft_sha: str, approval: dict, now: datetime,
    expected_schema: str, expected_authorization: dict,
) -> None:
    authorized_at = _timestamp(approval.get("authorized_at"))
    expires_at = _timestamp(approval.get("expires_at"))
    checks = {
        "approval_schema": approval.get("schema") == expected_schema,
        "approval_draft_sha256": approval.get("draft_sha256") == draft_sha,
        "approval_actor": bool(str(approval.get("actor") or "").strip()),
        "approval_authorization": approval.get("authorization")
        == expected_authorization == draft.get("requested_authorization"),
        "approval_window": authorized_at <= now <= expires_at,
        "approval_duration": expires_at - authorized_at <= MAX_APPROVAL_DURATION,
    }
    failed = next((name for name, passed in checks.items() if not passed), None)
    if failed:
        raise ValueError(failed)


def prepare_activation_draft(
    *, source_root: str | Path, source_commit: str,
    evidence_root: str | Path, owner_decision: str | Path,
    owner_decision_finalization: str | Path, output: str | Path,
    owner: str,
) -> tuple[dict, str]:
    root = Path(source_root).resolve()
    verified_source_commit = _source_commit(root)
    if verified_source_commit != str(source_commit or "").strip():
        raise ValueError("source_commit_mismatch")
    evidence_root = Path(evidence_root).resolve()
    owner_value, _, owner_path = _json(owner_decision)
    receipt_value, _, receipt_path = _json(owner_decision_finalization)
    evidence_commit = str(owner_value.get("source_commit") or "")
    owner_reference = build_json_reference(
        owner_path, root=evidence_root,
        expected_schema="query-controlled-demo-owner-decision-v1",
    )
    receipt_reference = build_json_reference(
        receipt_path, root=evidence_root,
        expected_schema="query-controlled-demo-owner-decision-finalization-v1",
    )
    if not validate_query_owner_decision(
        root=root,
        evidence_root=evidence_root,
        evidence_source_commit=evidence_commit,
        owner_decision=owner_reference,
        owner_decision_finalization=receipt_reference,
    ):
        raise ValueError("owner_decision_invalid")
    normalized_owner = str(owner or "").strip()
    if not normalized_owner or normalized_owner != owner_value.get(
        "owner_approval", {}
    ).get("owner"):
        raise ValueError("activation_owner_invalid")
    draft = {
        "schema": "query-controlled-demo-activation-draft-v1",
        "status": "AWAITING_EXACT_OWNER_APPROVAL",
        "source_root": str(root),
        "source_commit": verified_source_commit,
        "evidence_source_commit": evidence_commit,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "owner": normalized_owner,
        "owner_decision_root": str(evidence_root),
        "owner_decision": owner_reference,
        "owner_decision_finalization": receipt_reference,
        "owner_decision_sha256": owner_reference["sha256"],
        "owner_decision_finalization_sha256": receipt_reference["sha256"],
        "requested_authorization": QUERY_ACTIVATION_AUTHORIZATION,
    }
    output_path = _dot_local_output(output, root)
    _, digest = _write_json(output_path, draft)
    return draft, digest


def finalize_activation(
    *, draft_path: str | Path, approval_path: str | Path,
    output_dir: str | Path, now: datetime | None = None,
) -> dict:
    draft, draft_raw, draft_path = _json(draft_path)
    approval, _, approval_path = _json(approval_path)
    root = Path(str(draft.get("source_root") or "")).resolve()
    if _source_commit(root) != draft.get("source_commit"):
        raise ValueError("source_commit_mismatch")
    target = _dot_local_output(output_dir, root)
    if target.exists() and any(target.iterdir()):
        raise ValueError("activation_output_must_be_empty")
    evaluation_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    draft_sha = hashlib.sha256(draft_raw).hexdigest()
    _approval(
        draft=draft,
        draft_sha=draft_sha,
        approval=approval,
        now=evaluation_time,
        expected_schema="query-controlled-demo-activation-approval-v1",
        expected_authorization=QUERY_ACTIVATION_AUTHORIZATION,
    )
    if approval.get("actor") != draft.get("owner"):
        raise ValueError("approval_actor_not_owner")
    target.mkdir(parents=True, exist_ok=True)
    source_commit = str(draft.get("source_commit") or "")
    authorization = {
        "schema": "query-controlled-demo-activation-authorization-v1",
        "source_commit": source_commit,
        "evidence_source_commit": draft.get("evidence_source_commit"),
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "passed": True,
        "decision": "accepted",
        "technical_eligible": True,
        "human_review_accepted": True,
        "production_eligible": False,
        "feature_activation_authorized": True,
        "runtime_consumption_authorized": False,
        "runtime_start_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "owner_decision_root": draft["owner_decision_root"],
        "owner_decision": draft["owner_decision"],
        "owner_decision_finalization": draft["owner_decision_finalization"],
        "activation_owner": approval["actor"],
        "materialized_at": evaluation_time.isoformat().replace("+00:00", "Z"),
        "activation_draft": build_json_reference(
            draft_path, root=root,
            expected_schema="query-controlled-demo-activation-draft-v1",
        ),
        "activation_approval": build_json_reference(
            approval_path, root=root,
            expected_schema="query-controlled-demo-activation-approval-v1",
        ),
    }
    authorization_path, authorization_sha = _write_json(
        target / "query-activation-authorization.json", authorization,
    )
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "query_decomposition",
        "scope": "controlled_demo",
        "decision": "accepted",
        "source_commit": source_commit,
        "evidence": [build_json_reference(
            authorization_path, root=root,
            expected_schema="query-controlled-demo-activation-authorization-v1",
        )],
        "reason": "Exact owner-approved offline Query activation contract.",
        "reviewer_signoff": {
            "reviewer": approval["actor"],
            "signed_at": approval["authorized_at"],
        },
    }
    decision_path, decision_sha = _write_json(
        target / "query-milestone-decision.json", decision,
    )
    ledger_path, ledger_sha = _write_json(
        target / "controlled-demo-decision-ledger.json",
        {
            "schema": "controlled-demo-decision-ledger-v2",
            "status": "complete",
            "decisions": {
                "query_decomposition": {
                    "path": str(decision_path), "sha256": decision_sha,
                },
            },
        },
    )
    bundle_path = target / "query-controlled-demo-bundle.json"
    staged_bundle_path = target / ".query-controlled-demo-bundle.staged.json"
    _, bundle_sha = build_activation_bundle(
        scope="controlled_demo",
        profile="selective",
        enabled_features={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        source_commit=source_commit,
        decision_ledger=ledger_path,
        output=staged_bundle_path,
        root=root,
    )
    environment = {
        **{name: "false" for name in FEATURE_FLAGS},
        **VERSION_DEFAULTS,
        "RAG_QUERY_DECOMPOSITION_ENABLED": "true",
        "RAG_ACTIVATION_PROFILE": "selective",
        "RAG_ACTIVATION_SCOPE": "controlled_demo",
        "RAG_EXECUTION_CONTEXT": "production",
        "RAG_DEPLOYMENT_GIT_SHA": source_commit,
        "RAG_ACTIVATION_BUNDLE_PATH": str(staged_bundle_path),
        "RAG_ACTIVATION_BUNDLE_SHA256": bundle_sha,
    }
    preflight = activation_status(
        environment, root=root, current_commit=source_commit,
    )
    rollback = activation_status(
        {
            **{name: "false" for name in FEATURE_FLAGS},
            "RAG_ACTIVATION_SCOPE": "controlled_demo",
            "RAG_EXECUTION_CONTEXT": "production",
        },
        root=root,
        current_commit=source_commit,
    )
    if not (
        preflight.valid
        and not preflight.live_authorized
        and preflight.reason == "runtime_consumption_not_authorized"
        and rollback.valid
        and rollback.live_authorized
    ):
        staged_bundle_path.unlink(missing_ok=True)
        raise ValueError("offline_activation_preflight_failed")
    preflight_path, preflight_sha = _write_json(
        target / "offline-preflight.json",
        {
            "schema": "query-controlled-demo-offline-preflight-v1",
            "source_commit": source_commit,
            "activation_bundle_sha256": bundle_sha,
            **preflight.to_dict(),
        },
    )
    rollback_path, rollback_sha = _write_json(
        target / "offline-rollback.json",
        {
            "schema": "query-controlled-demo-offline-rollback-v1",
            "source_commit": source_commit,
            "query_decomposition_enabled": False,
            **rollback.to_dict(),
        },
    )
    receipt = {
        "schema": "query-controlled-demo-activation-finalization-v1",
        "source_commit": source_commit,
        "authorization": {
            "path": str(authorization_path), "sha256": authorization_sha,
        },
        "decision": {"path": str(decision_path), "sha256": decision_sha},
        "ledger": {"path": str(ledger_path), "sha256": ledger_sha},
        "bundle": {"path": str(bundle_path), "sha256": bundle_sha},
        "offline_preflight": {
            "path": str(preflight_path), "sha256": preflight_sha,
        },
        "offline_rollback": {
            "path": str(rollback_path), "sha256": rollback_sha,
        },
        "runtime_consumption_authorized": False,
        "runtime_start_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "next_gate": "exact_runtime_consumption_authorization",
    }
    receipt_path, receipt_sha = _write_json(
        target / "finalization-receipt.json", receipt,
    )
    staged_bundle_path.replace(bundle_path)
    return {
        **receipt,
        "receipt": {"path": str(receipt_path), "sha256": receipt_sha},
    }


def prepare_runtime_draft(
    *, source_root: str | Path, bundle_path: str | Path,
    preflight_path: str | Path, rollback_path: str | Path,
    finalization_receipt_path: str | Path,
    output: str | Path,
) -> tuple[dict, str]:
    root = Path(source_root).resolve()
    current_commit = _source_commit(root)
    bundle, bundle_raw, bundle_path = _json(bundle_path)
    preflight, _, _ = _json(preflight_path)
    rollback, _, _ = _json(rollback_path)
    finalization, _, _ = _json(finalization_receipt_path)
    source_commit = str(bundle.get("source_commit") or "")
    if current_commit != source_commit:
        raise ValueError("source_commit_mismatch")
    bundle_sha = hashlib.sha256(bundle_raw).hexdigest()
    ledger = load_json_reference(bundle.get("decision_ledger"), root=root)
    decision = load_json_reference(
        (ledger or {}).get("decisions", {}).get("query_decomposition"),
        root=root,
    )
    evidence = (decision or {}).get("evidence") or []
    authorization = load_json_reference(
        evidence[0] if len(evidence) == 1 else None, root=root,
    )
    owner = str((authorization or {}).get("activation_owner") or "").strip()
    if not all((
        bundle.get("schema") == "rag-activation-bundle-v1",
        bundle.get("scope") == "controlled_demo",
        bundle.get("activation_profile") == "selective",
        bundle.get("feature_flags", {}).get(
            "RAG_QUERY_DECOMPOSITION_ENABLED"
        ) is True,
        preflight.get("schema") == "query-controlled-demo-offline-preflight-v1",
        preflight.get("source_commit") == source_commit,
        preflight.get("activation_bundle_sha256") == bundle_sha,
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
        finalization.get("bundle", {}).get("sha256") == bundle_sha,
        finalization.get("offline_preflight", {}).get("sha256")
        == hashlib.sha256(Path(preflight_path).read_bytes()).hexdigest(),
        finalization.get("offline_rollback", {}).get("sha256")
        == hashlib.sha256(Path(rollback_path).read_bytes()).hexdigest(),
        finalization.get("runtime_consumption_authorized") is False,
        finalization.get("runtime_start_authorized") is False,
        finalization.get("provider_traffic_authorized") is False,
        finalization.get("pilot_dispatch_authorized") is False,
        bool(owner),
    )):
        raise ValueError("offline_activation_evidence_invalid")
    draft = {
        "schema": "controlled-demo-runtime-consumption-draft-v1",
        "source_commit": source_commit,
        "scope": "controlled_demo",
        "activation_bundle_sha256": bundle_sha,
        "enabled_flags": ["RAG_QUERY_DECOMPOSITION_ENABLED"],
        "owner": owner,
        "offline_preflight": build_json_reference(
            preflight_path, root=root,
            expected_schema="query-controlled-demo-offline-preflight-v1",
        ),
        "offline_rollback": build_json_reference(
            rollback_path, root=root,
            expected_schema="query-controlled-demo-offline-rollback-v1",
        ),
        "activation_finalization": build_json_reference(
            finalization_receipt_path, root=root,
            expected_schema="query-controlled-demo-activation-finalization-v1",
        ),
        "requested_authorization": RUNTIME_AUTHORIZATION,
    }
    output_path = _dot_local_output(output, root)
    _, digest = _write_json(output_path, draft)
    return draft, digest


def finalize_runtime_authorization(
    *, draft_path: str | Path, approval_path: str | Path,
    output: str | Path, now: datetime | None = None,
) -> tuple[dict, str]:
    draft, draft_raw, draft_path = _json(draft_path)
    approval, _, approval_path = _json(approval_path)
    evaluation_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    draft_sha = hashlib.sha256(draft_raw).hexdigest()
    _approval(
        draft=draft,
        draft_sha=draft_sha,
        approval=approval,
        now=evaluation_time,
        expected_schema="controlled-demo-runtime-consumption-approval-v1",
        expected_authorization=RUNTIME_AUTHORIZATION,
    )
    if approval.get("actor") != draft.get("owner"):
        raise ValueError("approval_actor_not_owner")
    root = Path(str(draft_path)).resolve()
    while root.name != ".local" and root != root.parent:
        root = root.parent
    if root.name != ".local":
        raise ValueError("runtime_draft_must_be_under_dot_local")
    source_root = root.parent
    current_commit = _source_commit(source_root)
    if current_commit != draft.get("source_commit"):
        raise ValueError("source_commit_mismatch")
    authorization = {
        "schema": "controlled-demo-runtime-consumption-authorization-v1",
        "source_commit": draft["source_commit"],
        "scope": "controlled_demo",
        "activation_bundle_sha256": draft["activation_bundle_sha256"],
        "enabled_flags": draft["enabled_flags"],
        "actor": approval["actor"],
        "authorized_at": approval["authorized_at"],
        "expires_at": approval["expires_at"],
        "materialized_at": evaluation_time.isoformat().replace("+00:00", "Z"),
        "runtime_draft": build_json_reference(
            draft_path, root=source_root,
            expected_schema="controlled-demo-runtime-consumption-draft-v1",
        ),
        "runtime_approval": build_json_reference(
            approval_path, root=source_root,
            expected_schema="controlled-demo-runtime-consumption-approval-v1",
        ),
        **RUNTIME_AUTHORIZATION,
    }
    output_path = _dot_local_output(output, source_root)
    _, digest = _write_json(output_path, authorization)
    return authorization, digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-activation")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--source-commit", required=True)
    prepare.add_argument("--evidence-root", type=Path, required=True)
    prepare.add_argument("--owner-decision", type=Path, required=True)
    prepare.add_argument("--owner-decision-finalization", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--owner", required=True)
    finalize = subparsers.add_parser("finalize-activation")
    finalize.add_argument("--draft", type=Path, required=True)
    finalize.add_argument("--approval", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    runtime_draft = subparsers.add_parser("prepare-runtime")
    runtime_draft.add_argument("--source-root", type=Path, required=True)
    runtime_draft.add_argument("--bundle", type=Path, required=True)
    runtime_draft.add_argument("--preflight", type=Path, required=True)
    runtime_draft.add_argument("--rollback", type=Path, required=True)
    runtime_draft.add_argument("--finalization-receipt", type=Path, required=True)
    runtime_draft.add_argument("--output", type=Path, required=True)
    runtime_finalize = subparsers.add_parser("finalize-runtime")
    runtime_finalize.add_argument("--draft", type=Path, required=True)
    runtime_finalize.add_argument("--approval", type=Path, required=True)
    runtime_finalize.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare-activation":
        _, digest = prepare_activation_draft(
            source_root=args.source_root,
            source_commit=args.source_commit,
            evidence_root=args.evidence_root,
            owner_decision=args.owner_decision,
            owner_decision_finalization=args.owner_decision_finalization,
            output=args.output,
            owner=args.owner,
        )
        print(json.dumps({"path": str(args.output), "sha256": digest}))
    elif args.command == "finalize-activation":
        receipt = finalize_activation(
            draft_path=args.draft,
            approval_path=args.approval,
            output_dir=args.output_dir,
        )
        print(json.dumps(receipt, ensure_ascii=False))
    elif args.command == "prepare-runtime":
        _, digest = prepare_runtime_draft(
            source_root=args.source_root,
            bundle_path=args.bundle,
            preflight_path=args.preflight,
            rollback_path=args.rollback,
            finalization_receipt_path=args.finalization_receipt,
            output=args.output,
        )
        print(json.dumps({"path": str(args.output), "sha256": digest}))
    else:
        _, digest = finalize_runtime_authorization(
            draft_path=args.draft,
            approval_path=args.approval,
            output=args.output,
        )
        print(json.dumps({"path": str(args.output), "sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
