from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts.ops.query_controlled_demo_activation import (
    finalize_activation_contract,
    prepare_activation_draft,
)


NOW = datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc)


def _write_json(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _reference(path: Path, root: Path, schema: str) -> dict:
    return {
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema": schema,
    }


def _source_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / ".gitignore").write_text(".local/\n", encoding="utf-8")
    (root / "source.txt").write_text("locked\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "test source"], cwd=root, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    return root, commit


def _evidence(tmp_path: Path) -> dict:
    root, commit = _source_repo(tmp_path)
    evidence_root = root / ".local" / "decision"
    disposition_path = evidence_root / "window-disposition.json"
    _write_json(disposition_path, {
        "schema": "query-decomposition-formal-window-disposition-v2",
        "source_commit": commit, "run_id": "query-window",
    })
    pack_path = evidence_root / "pack.json"
    _write_json(pack_path, {
        "schema": "query-decomposition-human-review-pack-v2",
        "source_commit": commit, "run_id": "query-window",
    })
    review_path = evidence_root / "review-result.json"
    _write_json(review_path, {
        "schema": "query-decomposition-human-review-result-v1",
        "source_commit": commit, "run_id": "query-window",
        "reviewer": "tran.nghi", "quality_passed": True,
    })
    evidence = {
        "window_disposition": _reference(
            disposition_path, root,
            "query-decomposition-formal-window-disposition-v2",
        ),
        "human_review_pack": _reference(
            pack_path, root, "query-decomposition-human-review-pack-v2",
        ),
        "review_result": _reference(
            review_path, root, "query-decomposition-human-review-result-v1",
        ),
    }
    owner_decision = {
        "schema": "query-controlled-demo-owner-decision-v1",
        "status": "accepted_for_controlled_demo_quality_only",
        "decision": "accepted_for_controlled_demo_quality_only",
        "source_commit": commit, "run_id": "query-window",
        "scope": "controlled_demo", "capability": "query_decomposition",
        "pilot_contract": "query-decomposition-24h-100-v1",
        "evidence": evidence, "technical_eligible": True,
        "human_review_accepted": True, "production_eligible": False,
        "independent_human_review": {
            "reviewer": "tran.nghi", "evaluated_at": "2026-08-25T09:00:00Z",
            "review_result": evidence["review_result"],
        },
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False, "merge_authorized": False,
        "query_decomposition_remains_off": True,
        "owner_approval": {
            "owner": "bao.nguyen", "accepted_at": "2026-08-26T00:48:00Z",
            "scope": "controlled_demo_owner_decision_only",
        },
    }
    decision_path = evidence_root / "query-controlled-demo-owner-decision.json"
    decision_sha = _write_json(decision_path, owner_decision)
    receipt = {
        "schema": "query-controlled-demo-owner-decision-finalization-v1",
        "source_commit": commit,
        "owner_decision": {
            "path": str(decision_path), "sha256": decision_sha,
        },
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False, "merge_authorized": False,
        "next_gate": "separate_activation_contract_and_preflight_authorization",
    }
    receipt_path = evidence_root / "finalization-receipt.json"
    receipt_sha = _write_json(receipt_path, receipt)
    return {
        "root": root, "commit": commit, "run_id": "query-window",
        "decision": decision_path, "decision_sha": decision_sha,
        "receipt": receipt_path, "receipt_sha": receipt_sha,
        "review": review_path,
    }


def _prepare(fixture, output: Path, **overrides):
    arguments = {
        "source_root": fixture["root"], "owner_decision_path": fixture["decision"],
        "finalization_receipt_path": fixture["receipt"], "output": output,
        "owner": "bao.nguyen", "expected_source_commit": fixture["commit"],
        "expected_run_id": fixture["run_id"],
        "expected_owner_decision_sha256": fixture["decision_sha"],
        "expected_finalization_receipt_sha256": fixture["receipt_sha"],
    }
    arguments.update(overrides)
    return prepare_activation_draft(**arguments)


def _approval(
    path: Path, draft_sha: str, authorization: dict, *,
    actor="bao.nguyen", expires_at="2026-08-26T02:00:00Z",
) -> str:
    return _write_json(path, {
        "schema": "query-controlled-demo-activation-owner-approval-v1",
        "draft_sha256": draft_sha, "actor": actor,
        "authorized_at": "2026-08-26T01:00:00Z", "expires_at": expires_at,
        "authorization": authorization,
    })


def test_prepare_creates_exact_live_authorizing_artifact_only_draft(tmp_path):
    fixture = _evidence(tmp_path)
    output = fixture["root"] / ".local" / "activation" / "draft.json"
    draft, digest = _prepare(fixture, output)

    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
    assert draft["status"] == "AWAITING_EXACT_OWNER_APPROVAL"
    assert draft["source_commit"] == fixture["commit"]
    assert draft["source_evidence"]["owner_decision"]["sha256"] == fixture["decision_sha"]
    assert draft["requested_authorization"] == {
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


@pytest.mark.parametrize(
    ("override", "error"),
    (
        ({"expected_source_commit": "0" * 40}, "expected_source_commit"),
        ({"expected_run_id": "other"}, "expected_run_id"),
        ({"expected_owner_decision_sha256": "0" * 64}, "expected_owner_decision"),
        ({"expected_finalization_receipt_sha256": "0" * 64}, "expected_finalization"),
    ),
)
def test_prepare_rejects_exact_identity_drift(tmp_path, override, error):
    fixture = _evidence(tmp_path)
    with pytest.raises(ValueError, match=error):
        _prepare(fixture, fixture["root"] / ".local" / "draft.json", **override)


def test_prepare_rejects_nonaccepted_decision_and_external_output(tmp_path):
    fixture = _evidence(tmp_path)
    decision = json.loads(fixture["decision"].read_text(encoding="utf-8"))
    decision["human_review_accepted"] = False
    fixture["decision_sha"] = _write_json(fixture["decision"], decision)
    receipt = json.loads(fixture["receipt"].read_text(encoding="utf-8"))
    receipt["owner_decision"]["sha256"] = fixture["decision_sha"]
    fixture["receipt_sha"] = _write_json(fixture["receipt"], receipt)
    with pytest.raises(ValueError, match="owner_decision_quality_accepted"):
        _prepare(fixture, fixture["root"] / ".local" / "draft.json")

    fixture = _evidence(tmp_path / "second")
    with pytest.raises(ValueError, match="must stay under .local"):
        _prepare(fixture, fixture["root"] / "draft.json")


def test_finalize_rejects_widened_expired_or_drifted_approval(tmp_path):
    fixture = _evidence(tmp_path)
    root = fixture["root"] / ".local" / "activation"
    draft_path = root / "draft.json"
    draft, draft_sha = _prepare(fixture, draft_path)

    widened = dict(draft["requested_authorization"])
    widened["runtime_start_authorized"] = True
    widened_path = root / "widened.json"
    _approval(widened_path, draft_sha, widened)
    with pytest.raises(ValueError, match="approval_authorization_exact"):
        finalize_activation_contract(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=widened_path, output_dir=root / "widened", now=NOW,
        )

    expired_path = root / "expired.json"
    _approval(
        expired_path, draft_sha, draft["requested_authorization"],
        expires_at="2026-08-26T01:29:00Z",
    )
    with pytest.raises(ValueError, match="approval_time_order"):
        finalize_activation_contract(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=expired_path, output_dir=root / "expired", now=NOW,
        )

    fixture["review"].write_text("drift", encoding="utf-8")
    approval_path = root / "approval.json"
    _approval(approval_path, draft_sha, draft["requested_authorization"])
    with pytest.raises(ValueError, match="owner_decision_evidence_reference"):
        finalize_activation_contract(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=approval_path, output_dir=root / "drift", now=NOW,
        )


def test_finalize_builds_offline_contract_but_does_not_touch_runtime(tmp_path):
    fixture = _evidence(tmp_path)
    root = fixture["root"] / ".local" / "activation"
    draft_path = root / "draft.json"
    draft, draft_sha = _prepare(fixture, draft_path)
    approval_path = root / "approval.json"
    _approval(approval_path, draft_sha, draft["requested_authorization"])

    receipt = finalize_activation_contract(
        source_root=fixture["root"], draft_path=draft_path,
        approval_path=approval_path, output_dir=root / "final", now=NOW,
    )

    bundle = json.loads(Path(receipt["activation_bundle"]["path"]).read_text())
    enabled = [name for name, value in bundle["feature_flags"].items() if value]
    preflight = json.loads(Path(receipt["offline_preflight"]["path"]).read_text())
    rollback = json.loads(Path(receipt["offline_rollback"]["path"]).read_text())
    assert enabled == ["RAG_QUERY_DECOMPOSITION_ENABLED"]
    assert preflight["valid"] is True and preflight["live_authorized"] is True
    assert all(value is False for value in rollback["feature_flags"].values())
    assert receipt["runtime_consumption_authorized"] is False
    assert receipt["source_owner_decision_production_eligible"] is False
    assert receipt["activation_evidence_production_eligible"] is True
    assert receipt["provider_traffic_authorized"] is False
    assert receipt["pilot_dispatch_authorized"] is False
    assert receipt["runtime_start_authorized"] is False
    assert receipt["next_gate"] == "separate_runtime_start_and_query_pilot_authorization"
    assert subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=fixture["root"], check=True,
        capture_output=True, text=True,
    ).stdout.strip() == ""
