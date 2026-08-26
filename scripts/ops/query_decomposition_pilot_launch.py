"""Materialize Query activation and pilot authorization from one approval."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from mech_chatbot.governance.artifact_references import (
    build_json_reference,
    load_json_reference,
)
from mech_chatbot.governance.query_activation_contract import (
    QUERY_ACTIVATION_AUTHORIZATION,
)
from scripts.ops.query_controlled_demo_activation import finalize_activation
from scripts.ops.query_decomposition_pilot import (
    MAXIMUM_AUTHORIZATION_DURATION,
    MINIMUM_AUTHORIZATION_DURATION,
    PILOT_AUTHORIZATION,
    _dot_local,
    _format,
    _manifest,
    _offline_rollback,
    _operator_runbook,
    _read_json,
    _schedule_template,
    _sha256,
    _source_commit,
    _timestamp,
    _write_json,
    finalize_pilot_authorization,
    prepare_pilot_launch_packet,
)


CONSOLIDATED_AUTHORIZATION = {
    "activation": QUERY_ACTIVATION_AUTHORIZATION,
    "pilot": PILOT_AUTHORIZATION,
}


def prepare_consolidated_launch(
    *, source_root: str | Path, source_commit: str,
    activation_draft_path: str | Path, manifest_path: str | Path,
    output_dir: str | Path, owner: str,
) -> dict:
    """Freeze one owner approval boundary for activation and the 24-hour pilot."""
    root = Path(source_root).resolve()
    commit = _source_commit(root)
    if commit != str(source_commit or "").strip():
        raise ValueError("source_commit_mismatch")
    target = _dot_local(output_dir, root)
    if target.exists() and any(target.iterdir()):
        raise ValueError("consolidated_launch_output_must_be_empty")
    activation, _, activation_path = _read_json(activation_draft_path)
    manifest_path = Path(manifest_path).resolve()
    complex_rows, manifest_sha = _manifest(manifest_path)
    normalized_owner = str(owner or "").strip()
    if not all((
        activation_path.is_relative_to(root),
        manifest_path.is_relative_to(root),
        activation.get("schema")
        == "query-controlled-demo-activation-draft-v1",
        activation.get("source_commit") == commit,
        activation.get("scope") == "controlled_demo",
        activation.get("capability") == "query_decomposition",
        activation.get("owner") == normalized_owner,
        activation.get("requested_authorization")
        == QUERY_ACTIVATION_AUTHORIZATION,
    )):
        raise ValueError("activation_draft_invalid")
    target.mkdir(parents=True, exist_ok=True)
    plan = _schedule_template(
        commit=commit, bundle_sha="pending", manifest_path=manifest_path,
        manifest_sha=manifest_sha, complex_rows=complex_rows, root=root,
    )
    plan = {
        **{name: value for name, value in plan.items()
           if name != "activation_bundle_sha256"},
        "schema": "query-decomposition-pilot-schedule-plan-v1",
    }
    plan_path, plan_sha = _write_json(target / "schedule-plan.json", plan)
    runbook_path, runbook_sha = _write_json(
        target / "operator-runbook.json", _operator_runbook(commit),
    )
    rollback_path, rollback_sha = _write_json(
        target / "rollback-plan.json", _offline_rollback(commit),
    )
    draft = {
        "schema": "query-decomposition-consolidated-launch-draft-v1",
        "status": "AWAITING_ONE_CONSOLIDATED_APPROVAL",
        "source_root": str(root),
        "source_commit": commit,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "owner": normalized_owner,
        "activation_draft": build_json_reference(
            activation_path, root=root,
            expected_schema="query-controlled-demo-activation-draft-v1",
        ),
        "schedule_plan": build_json_reference(
            plan_path, root=root,
            expected_schema="query-decomposition-pilot-schedule-plan-v1",
        ),
        "operator_runbook": build_json_reference(
            runbook_path, root=root,
            expected_schema="query-decomposition-pilot-operator-runbook-v1",
        ),
        "rollback_plan": build_json_reference(
            rollback_path, root=root,
            expected_schema="query-decomposition-pilot-offline-rollback-v1",
        ),
        "requested_authorization": CONSOLIDATED_AUTHORIZATION,
    }
    draft_path, draft_sha = _write_json(
        target / "consolidated-launch-draft.json", draft,
    )
    packet = {
        "schema": "query-decomposition-consolidated-launch-packet-v1",
        "status": "AWAITING_ONE_CONSOLIDATED_APPROVAL",
        "source_commit": commit,
        "schedule_plan": {"path": str(plan_path), "sha256": plan_sha},
        "operator_runbook": {
            "path": str(runbook_path), "sha256": runbook_sha,
        },
        "rollback_plan": {
            "path": str(rollback_path), "sha256": rollback_sha,
        },
        "consolidated_launch_draft": {
            "path": str(draft_path), "sha256": draft_sha,
        },
        "runtime_started": False,
        "provider_traffic_generated": False,
        "pilot_dispatched": False,
        "next_gate": "one_consolidated_query_pilot_launch_approval",
    }
    _write_json(target / "consolidated-launch-packet.json", packet)
    return packet


def finalize_consolidated_launch(
    *, draft_path: str | Path, approval_path: str | Path,
    output_dir: str | Path, now: datetime | None = None,
) -> dict:
    """Materialize both contracts without starting runtime or dispatching traffic."""
    draft, draft_raw, draft_path = _read_json(draft_path)
    approval, _, approval_path = _read_json(approval_path)
    root = Path(str(draft.get("source_root") or "")).resolve()
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    authorized_at = _timestamp(approval.get("authorized_at"))
    expires_at = _timestamp(approval.get("expires_at"))
    if not all((
        _source_commit(root) == draft.get("source_commit"),
        draft.get("schema")
        == "query-decomposition-consolidated-launch-draft-v1",
        approval.get("schema")
        == "query-decomposition-consolidated-launch-approval-v1",
        approval.get("draft_sha256") == _sha256(draft_raw),
        approval.get("actor") == draft.get("owner"),
        approval.get("authorization") == draft.get("requested_authorization")
        == CONSOLIDATED_AUTHORIZATION,
        authorized_at <= current <= expires_at,
        MINIMUM_AUTHORIZATION_DURATION
        <= expires_at - authorized_at <= MAXIMUM_AUTHORIZATION_DURATION,
    )):
        raise ValueError("consolidated_launch_approval_invalid")
    schedule_plan = load_json_reference(draft.get("schedule_plan"), root=root)
    operator_runbook = load_json_reference(
        draft.get("operator_runbook"), root=root,
    )
    rollback_plan = load_json_reference(draft.get("rollback_plan"), root=root)
    if not isinstance(schedule_plan, dict):
        raise ValueError("schedule_plan_invalid")
    if not all((
        isinstance(operator_runbook, dict),
        isinstance(rollback_plan, dict),
        operator_runbook == _operator_runbook(draft["source_commit"])
        and rollback_plan == _offline_rollback(draft["source_commit"]),
    )):
        raise ValueError("operator_contract_invalid")
    manifest_path = root / str(schedule_plan.get("manifest", {}).get("path"))
    try:
        complex_rows, manifest_sha = _manifest(manifest_path)
        expected_plan = _schedule_template(
            commit=draft["source_commit"], bundle_sha="pending",
            manifest_path=manifest_path, manifest_sha=manifest_sha,
            complex_rows=complex_rows, root=root,
        )
    except (OSError, ValueError):
        raise ValueError("schedule_plan_invalid") from None
    expected_plan = {
        **{name: value for name, value in expected_plan.items()
           if name != "activation_bundle_sha256"},
        "schema": "query-decomposition-pilot-schedule-plan-v1",
    }
    if schedule_plan != expected_plan:
        raise ValueError("schedule_plan_invalid")
    target = _dot_local(output_dir, root)
    if target.exists() and any(target.iterdir()):
        raise ValueError("consolidated_launch_materialization_must_be_empty")
    target.mkdir(parents=True, exist_ok=True)
    consolidated_ref = build_json_reference(
        approval_path, root=root,
        expected_schema="query-decomposition-consolidated-launch-approval-v1",
    )
    consolidated_draft_ref = build_json_reference(
        draft_path, root=root,
        expected_schema="query-decomposition-consolidated-launch-draft-v1",
    )
    activation_approval_path, _ = _write_json(
        target / "activation-approval.json", {
            "schema": "query-controlled-demo-activation-approval-v1",
            "draft_sha256": draft["activation_draft"]["sha256"],
            "actor": approval["actor"],
            "authorized_at": _format(authorized_at),
            "expires_at": _format(authorized_at + timedelta(minutes=60)),
            "authorization": QUERY_ACTIVATION_AUTHORIZATION,
            "consolidated_launch_approval": consolidated_ref,
            "consolidated_launch_draft": consolidated_draft_ref,
        },
    )
    activation = finalize_activation(
        draft_path=root / draft["activation_draft"]["path"],
        approval_path=activation_approval_path,
        output_dir=target / "activation", now=current,
    )
    pilot_packet = prepare_pilot_launch_packet(
        source_root=root, source_commit=draft["source_commit"],
        manifest_path=root / schedule_plan["manifest"]["path"],
        activation_bundle_path=activation["bundle"]["path"],
        activation_finalization_path=activation["receipt"]["path"],
        output_dir=target / "pilot", owner=approval["actor"],
    )
    pilot_draft_path = Path(
        pilot_packet["pilot_authorization_draft"]["path"]
    )
    pilot_approval_path, _ = _write_json(target / "pilot-approval.json", {
        "schema": "query-decomposition-pilot-approval-v1",
        "draft_sha256": pilot_packet["pilot_authorization_draft"]["sha256"],
        "actor": approval["actor"],
        "authorized_at": _format(authorized_at),
        "expires_at": _format(expires_at),
        "authorization": PILOT_AUTHORIZATION,
        "consolidated_launch_approval": consolidated_ref,
        "consolidated_launch_draft": consolidated_draft_ref,
    })
    _, pilot_sha = finalize_pilot_authorization(
        draft_path=pilot_draft_path, approval_path=pilot_approval_path,
        output_dir=target / "authorized", now=current,
    )
    receipt = {
        "schema": "query-decomposition-consolidated-launch-finalization-v1",
        "source_commit": draft["source_commit"],
        "activation_materialized": True,
        "pilot_authorization_materialized": True,
        "pilot_authorization": {
            "path": str(target / "authorized" / "pilot-authorization.json"),
            "sha256": pilot_sha,
        },
        "runtime_started": False,
        "provider_traffic_generated": False,
        "pilot_dispatched": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "next_gate": "authorized_operator_runtime_launch",
    }
    _write_json(target / "finalization-receipt.json", receipt)
    return receipt


__all__ = [
    "CONSOLIDATED_AUTHORIZATION",
    "finalize_consolidated_launch",
    "prepare_consolidated_launch",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-root", type=Path, required=True)
    prepare.add_argument("--source-commit", required=True)
    prepare.add_argument("--activation-draft", type=Path, required=True)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--owner", required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--draft", type=Path, required=True)
    finalize.add_argument("--approval", type=Path, required=True)
    finalize.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare_consolidated_launch(
            source_root=args.source_root,
            source_commit=args.source_commit,
            activation_draft_path=args.activation_draft,
            manifest_path=args.manifest,
            output_dir=args.output_dir,
            owner=args.owner,
        )
    else:
        result = finalize_consolidated_launch(
            draft_path=args.draft,
            approval_path=args.approval,
            output_dir=args.output_dir,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
