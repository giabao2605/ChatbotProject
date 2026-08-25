from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts.decomposition_eval.human_review_pack import pack_sha256
from scripts.ops.query_controlled_demo_decision import (
    finalize_decision,
    prepare_decision_draft,
)


NOW = datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def _write_json(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _source_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir()
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


def _evidence(tmp_path: Path) -> dict[str, Path | str]:
    root, commit = _source_repo(tmp_path)
    run_root = root / ".local" / "query-window"
    pack_dir = run_root / "human-review-pack"
    disposition = {
        "schema": "query-decomposition-formal-window-disposition-v2",
        "run_id": "query-window",
        "source_commit": commit,
        "status": "completed_technical_eligible_pending_human_review",
        "terminal": True,
        "consumed": True,
        "immutable": True,
        "tombstoned": False,
        "execution": {
            "formal_pairs_started": 3,
            "formal_pairs_completed": 3,
            "formal_pairs_gate_passed": 3,
            "provider_calls": 111,
            "provider_successes": 111,
            "provider_failures": 0,
            "provider_retries": 0,
            "disallowed_fallback_count": 0,
        },
        "evidence_eligibility": {
            "full_window_contract_passed": True,
            "technical_eligible": True,
            "production_eligible": False,
            "decision_status": "pending_human_review",
        },
        "governance": {
            "pilot_authorized": False,
            "feature_activation_authorized": False,
            "default_rollout_authorized": False,
            "push_authorized": False,
            "merge_authorized": False,
            "query_decomposition_remains_off": True,
        },
    }
    disposition_path = run_root / "window-disposition.json"
    disposition_sha = _write_json(disposition_path, disposition)
    pack = {
        "schema": "query-decomposition-human-review-pack-v2",
        "status": "locked_unreviewed",
        "scope": "controlled_demo_quality_review",
        "capability": "query_decomposition",
        "source_commit": commit,
        "source_owner": "bao.nguyen",
        "run_id": "query-window",
        "disposition_sha256": disposition_sha,
        "case_count": 13,
        "output_instance_count": 39,
        "review_mode": "independent_human",
        "review_contract_sha256": "a" * 64,
        "pilot_authorized": False,
        "feature_activation_authorized": False,
        "default_rollout_authorized": False,
        "query_decomposition_remains_off": True,
        "production_eligible": False,
    }
    pack_path = pack_dir / "pack.json"
    _write_json(pack_path, pack)
    pack_sha = pack_sha256(pack)
    review = {
        "schema": "query-decomposition-human-review-result-v1",
        "scope": "controlled_demo_quality_review",
        "capability": "query_decomposition",
        "source_commit": commit,
        "run_id": "query-window",
        "disposition_sha256": disposition_sha,
        "review_mode": "independent_human",
        "reviewer": "tran.nghi",
        "evaluated_at": "2026-08-25T09:00:00Z",
        "pack_sha256": pack_sha,
        "expected_pack_sha256": pack_sha,
        "pack_hash_matches": True,
        "review_contract_sha256": "a" * 64,
        "validation_passed": True,
        "review_complete": True,
        "quality_passed": True,
        "case_count": 13,
        "reviewed_output_count": 39,
        "accepted_count": 39,
        "rejected_count": 0,
        "needs_discussion_count": 0,
        "production_eligible": False,
        "pilot_authorized": False,
        "feature_activation_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "query_decomposition_remains_off": True,
    }
    review_path = pack_dir / "review-result.json"
    _write_json(review_path, review)
    return {
        "root": root,
        "commit": commit,
        "disposition": disposition_path,
        "review": review_path,
    }


def test_prepare_creates_non_authorizing_exact_evidence_draft(tmp_path):
    fixture = _evidence(tmp_path)
    output = fixture["root"] / ".local" / "decision" / "draft.json"

    draft, digest = prepare_decision_draft(
        source_root=fixture["root"],
        disposition_path=fixture["disposition"],
        review_result_path=fixture["review"],
        output=output,
        owner="bao.nguyen",
    )

    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
    assert draft["status"] == "AWAITING_EXACT_OWNER_APPROVAL"
    assert draft["source_commit"] == fixture["commit"]
    assert draft["preparation_tool"]["path"] == (
        "scripts/ops/query_controlled_demo_decision.py"
    )
    assert len(draft["preparation_tool"]["sha256"]) == 64
    assert len(draft["preparation_tool"]["git_commit"]) == 40
    assert draft["evidence"]["review_result"]["sha256"]
    assert draft["requested_authorization"] == {
        "materialize_controlled_demo_decision": True,
        "build_offline_activation_bundle": True,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "runtime_start_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
    }


def test_prepare_rejects_incomplete_human_review(tmp_path):
    fixture = _evidence(tmp_path)
    review = json.loads(fixture["review"].read_text(encoding="utf-8"))
    review["quality_passed"] = False
    _write_json(fixture["review"], review)

    with pytest.raises(ValueError, match="review_quality_passed"):
        prepare_decision_draft(
            source_root=fixture["root"],
            disposition_path=fixture["disposition"],
            review_result_path=fixture["review"],
            output=fixture["root"] / ".local" / "draft.json",
            owner="bao.nguyen",
        )


def test_prepare_rejects_owner_who_does_not_own_source_pack(tmp_path):
    fixture = _evidence(tmp_path)

    with pytest.raises(ValueError, match="owner_matches_source_owner"):
        prepare_decision_draft(
            source_root=fixture["root"],
            disposition_path=fixture["disposition"],
            review_result_path=fixture["review"],
            output=fixture["root"] / ".local" / "draft.json",
            owner="tran.nghi",
        )


def test_prepare_rejects_output_outside_dot_local(tmp_path):
    fixture = _evidence(tmp_path)

    with pytest.raises(ValueError, match="must stay under .local"):
        prepare_decision_draft(
            source_root=fixture["root"],
            disposition_path=fixture["disposition"],
            review_result_path=fixture["review"],
            output=fixture["root"] / "draft.json",
            owner="bao.nguyen",
        )


def test_finalize_rejects_approval_with_draft_hash_drift(tmp_path):
    fixture = _evidence(tmp_path)
    draft_path = fixture["root"] / ".local" / "decision" / "draft.json"
    prepare_decision_draft(
        source_root=fixture["root"], disposition_path=fixture["disposition"],
        review_result_path=fixture["review"], output=draft_path,
        owner="bao.nguyen",
    )
    approval_path = draft_path.with_name("approval.json")
    _write_json(approval_path, {
        "schema": "query-controlled-demo-owner-approval-v1",
        "draft_sha256": "0" * 64,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-25T09:55:00Z",
        "expires_at": "2026-08-25T10:55:00Z",
        "authorization": {
            "materialize_controlled_demo_decision": True,
            "build_offline_activation_bundle": True,
            "provider_traffic_authorized": False,
            "pilot_dispatch_authorized": False,
            "runtime_start_authorized": False,
            "default_rollout_authorized": False,
            "push_authorized": False,
            "merge_authorized": False,
        },
    })

    with pytest.raises(ValueError, match="approval_draft_sha256"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=approval_path,
            output_dir=fixture["root"] / ".local" / "decision" / "final",
            now=NOW,
        )


@pytest.mark.parametrize(
    ("widen_traffic", "expires_at", "expected_error"),
    (
        (True, "2026-08-25T10:55:00Z", "approval_authorization_exact"),
        (False, "2026-08-25T09:59:00Z", "approval_time_order"),
    ),
)
def test_finalize_rejects_widened_or_expired_approval(
    tmp_path, widen_traffic, expires_at, expected_error,
):
    fixture = _evidence(tmp_path)
    decision_root = fixture["root"] / ".local" / "decision"
    draft_path = decision_root / "draft.json"
    draft, draft_sha = prepare_decision_draft(
        source_root=fixture["root"], disposition_path=fixture["disposition"],
        review_result_path=fixture["review"], output=draft_path,
        owner="bao.nguyen",
    )
    authorization = dict(draft["requested_authorization"])
    authorization["provider_traffic_authorized"] = widen_traffic
    approval_path = decision_root / "approval.json"
    _write_json(approval_path, {
        "schema": "query-controlled-demo-owner-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-25T09:55:00Z",
        "expires_at": expires_at,
        "authorization": authorization,
    })

    with pytest.raises(ValueError, match=expected_error):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=approval_path,
            output_dir=decision_root / "final", now=NOW,
        )


def test_finalize_rejects_draft_bound_to_another_tool(tmp_path):
    fixture = _evidence(tmp_path)
    decision_root = fixture["root"] / ".local" / "decision"
    draft_path = decision_root / "draft.json"
    draft, _ = prepare_decision_draft(
        source_root=fixture["root"], disposition_path=fixture["disposition"],
        review_result_path=fixture["review"], output=draft_path,
        owner="bao.nguyen",
    )
    draft["preparation_tool"]["sha256"] = "0" * 64
    draft_sha = _write_json(draft_path, draft)
    approval_path = decision_root / "approval.json"
    _write_json(approval_path, {
        "schema": "query-controlled-demo-owner-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-25T09:55:00Z",
        "expires_at": "2026-08-25T10:55:00Z",
        "authorization": dict(draft["requested_authorization"]),
    })

    with pytest.raises(ValueError, match="preparation_tool_binding"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=approval_path,
            output_dir=decision_root / "final", now=NOW,
        )


def test_finalize_rejects_existing_output_directory(tmp_path):
    fixture = _evidence(tmp_path)
    decision_root = fixture["root"] / ".local" / "decision"
    draft_path = decision_root / "draft.json"
    draft, draft_sha = prepare_decision_draft(
        source_root=fixture["root"], disposition_path=fixture["disposition"],
        review_result_path=fixture["review"], output=draft_path,
        owner="bao.nguyen",
    )
    approval_path = decision_root / "approval.json"
    _write_json(approval_path, {
        "schema": "query-controlled-demo-owner-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-25T09:55:00Z",
        "expires_at": "2026-08-25T10:55:00Z",
        "authorization": dict(draft["requested_authorization"]),
    })
    output_dir = decision_root / "final"
    output_dir.mkdir()

    with pytest.raises(ValueError, match="final_output_dir_must_not_exist"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=approval_path, output_dir=output_dir, now=NOW,
        )


def test_finalize_builds_query_only_bundle_after_exact_approval(tmp_path):
    fixture = _evidence(tmp_path)
    decision_root = fixture["root"] / ".local" / "decision"
    draft_path = decision_root / "draft.json"
    _, draft_sha = prepare_decision_draft(
        source_root=fixture["root"], disposition_path=fixture["disposition"],
        review_result_path=fixture["review"], output=draft_path,
        owner="bao.nguyen",
    )
    approval_path = decision_root / "approval.json"
    _write_json(approval_path, {
        "schema": "query-controlled-demo-owner-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-25T09:55:00Z",
        "expires_at": "2026-08-25T10:55:00Z",
        "authorization": {
            "materialize_controlled_demo_decision": True,
            "build_offline_activation_bundle": True,
            "provider_traffic_authorized": False,
            "pilot_dispatch_authorized": False,
            "runtime_start_authorized": False,
            "default_rollout_authorized": False,
            "push_authorized": False,
            "merge_authorized": False,
        },
    })

    receipt = finalize_decision(
        source_root=fixture["root"], draft_path=draft_path,
        approval_path=approval_path, output_dir=decision_root / "final",
        now=NOW,
    )

    bundle = json.loads(Path(receipt["activation_bundle"]["path"]).read_text())
    assert bundle["scope"] == "controlled_demo"
    assert bundle["activation_profile"] == "selective"
    assert [name for name, enabled in bundle["feature_flags"].items() if enabled] == [
        "RAG_QUERY_DECOMPOSITION_ENABLED"
    ]
    assert receipt["provider_traffic_authorized"] is False
    assert receipt["pilot_dispatch_authorized"] is False
    assert receipt["runtime_start_authorized"] is False
    assert receipt["default_rollout_authorized"] is False
