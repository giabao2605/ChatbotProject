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


def _evidence(tmp_path: Path) -> dict[str, Path | str]:
    root, commit = _source_repo(tmp_path)
    run_root = root / ".local" / "query-window"
    pack_dir = run_root / "human-review-pack"
    disposition = {
        "schema": "query-decomposition-formal-window-disposition-v2",
        "run_id": "query-window", "source_commit": commit,
        "status": "completed_technical_eligible_pending_human_review",
        "terminal": True, "consumed": True, "immutable": True, "tombstoned": False,
        "execution": {
            "formal_pairs_started": 3, "formal_pairs_completed": 3,
            "formal_pairs_gate_passed": 3, "provider_calls": 111,
            "provider_successes": 111, "provider_failures": 0,
            "provider_retries": 0, "disallowed_fallback_count": 0,
        },
        "evidence_eligibility": {
            "formal_evidence": True, "rollout_evidence": True,
            "provider_health_passed": True, "query_quality_evaluated": True,
            "zero_retry_formal_path_exercised": True,
            "three_pair_gate_passed": True, "full_window_contract_passed": True,
            "technical_eligible": True, "production_eligible": False,
            "decision_status": "pending_human_review", "reuse_authorized": False,
            "carry_forward_authorized": False,
        },
        "governance": {
            "provider_smoke_rerun_authorized": False,
            "retry_or_catch_up_authorized": False,
            "same_root_reuse_authorized": False,
            "additional_formal_pairs_authorized": False,
            "pilot_authorized": False, "feature_activation_authorized": False,
            "default_rollout_authorized": False, "push_authorized": False,
            "merge_authorized": False, "query_decomposition_remains_off": True,
        },
    }
    disposition_path = run_root / "window-disposition.json"
    disposition_sha = _write_json(disposition_path, disposition)
    artifact_path = run_root / "artifact.json"
    artifact_sha = _write_json(artifact_path, {"schema": "fixture-v1"})
    pack = {
        "schema": "query-decomposition-human-review-pack-v2",
        "status": "locked_unreviewed", "scope": "controlled_demo_quality_review",
        "capability": "query_decomposition", "source_commit": commit,
        "source_owner": "bao.nguyen", "run_id": "query-window",
        "disposition_sha256": disposition_sha, "case_count": 13,
        "output_instance_count": 39, "review_mode": "independent_human",
        "minimum_reviewers": 1, "source_owner_may_review": False,
        "codex_may_review": False, "review_contract_sha256": "a" * 64,
        "artifact_bindings": {
            "fixture": {"base": "run_root", "path": "artifact.json", "sha256": artifact_sha},
        },
        "pilot_authorized": False, "feature_activation_authorized": False,
        "default_rollout_authorized": False, "push_authorized": False,
        "merge_authorized": False, "query_decomposition_remains_off": True,
        "production_eligible": False,
    }
    pack_path = pack_dir / "pack.json"
    _write_json(pack_path, pack)
    canonical_pack_sha = pack_sha256(pack)
    review = {
        "schema": "query-decomposition-human-review-result-v1",
        "scope": "controlled_demo_quality_review", "capability": "query_decomposition",
        "source_commit": commit, "run_id": "query-window",
        "disposition_sha256": disposition_sha, "review_mode": "independent_human",
        "reviewer": "tran.nghi", "evaluated_at": "2026-08-25T09:00:00Z",
        "pack_sha256": canonical_pack_sha, "expected_pack_sha256": canonical_pack_sha,
        "pack_hash_matches": True, "review_contract_sha256": "a" * 64,
        "validation_passed": True, "review_complete": True, "quality_passed": True,
        "case_count": 13, "reviewed_output_count": 39, "accepted_count": 39,
        "rejected_count": 0, "needs_discussion_count": 0,
        "production_eligible": False, "pilot_authorized": False,
        "feature_activation_authorized": False, "default_rollout_authorized": False,
        "push_authorized": False, "merge_authorized": False,
        "query_decomposition_remains_off": True,
    }
    review_path = pack_dir / "review-result.json"
    review_sha = _write_json(review_path, review)
    return {
        "root": root, "commit": commit, "run_id": "query-window",
        "disposition": disposition_path, "disposition_sha": disposition_sha,
        "review": review_path, "review_sha": review_sha,
    }


def _prepare(fixture, output: Path, **overrides):
    arguments = {
        "source_root": fixture["root"], "disposition_path": fixture["disposition"],
        "review_result_path": fixture["review"], "output": output,
        "owner": "bao.nguyen", "expected_source_commit": fixture["commit"],
        "expected_run_id": fixture["run_id"],
        "expected_disposition_sha256": fixture["disposition_sha"],
        "expected_review_result_sha256": fixture["review_sha"],
        "expected_reviewer": "tran.nghi",
    }
    arguments.update(overrides)
    return prepare_decision_draft(**arguments)


def _approval(path: Path, draft_sha: str, *, authorization: dict, expires_at=None):
    return _write_json(path, {
        "schema": "query-controlled-demo-owner-approval-v1",
        "draft_sha256": draft_sha, "actor": "bao.nguyen",
        "authorized_at": "2026-08-25T09:55:00Z",
        "expires_at": expires_at or "2026-08-25T10:55:00Z",
        "authorization": authorization,
    })


def test_prepare_creates_exact_non_authorizing_owner_decision_draft(tmp_path):
    fixture = _evidence(tmp_path)
    output = fixture["root"] / ".local" / "decision" / "draft.json"
    draft, digest = _prepare(fixture, output)

    assert hashlib.sha256(output.read_bytes()).hexdigest() == digest
    assert draft["status"] == "AWAITING_EXACT_OWNER_APPROVAL"
    assert draft["source_commit"] == fixture["commit"]
    assert draft["evidence"]["window_disposition"]["sha256"] == fixture["disposition_sha"]
    assert draft["evidence"]["review_result"]["sha256"] == fixture["review_sha"]
    assert draft["proposed_owner_decision"]["technical_eligible"] is True
    assert draft["proposed_owner_decision"]["production_eligible"] is False
    assert draft["proposed_owner_decision"]["independent_human_review"]["reviewer"] == "tran.nghi"
    assert draft["requested_authorization"] == {
        "materialize_controlled_demo_owner_decision": True,
        "provider_traffic_authorized": False, "pilot_dispatch_authorized": False,
        "feature_activation_authorized": False, "runtime_start_authorized": False,
        "default_rollout_authorized": False, "push_authorized": False,
        "merge_authorized": False,
    }


@pytest.mark.parametrize(
    ("override", "expected_error"),
    (
        ({"expected_source_commit": "0" * 40}, "expected_source_commit"),
        ({"expected_run_id": "other"}, "expected_run_id"),
        ({"expected_disposition_sha256": "0" * 64}, "expected_disposition_sha256"),
        ({"expected_review_result_sha256": "0" * 64}, "expected_review_result_sha256"),
        ({"expected_reviewer": "other"}, "expected_reviewer"),
    ),
)
def test_prepare_rejects_exact_evidence_identity_drift(tmp_path, override, expected_error):
    fixture = _evidence(tmp_path)
    with pytest.raises(ValueError, match=expected_error):
        _prepare(fixture, fixture["root"] / ".local" / "draft.json", **override)


def test_prepare_rejects_incomplete_review_or_wrong_owner(tmp_path):
    fixture = _evidence(tmp_path)
    review = json.loads(fixture["review"].read_text(encoding="utf-8"))
    review["quality_passed"] = False
    fixture["review_sha"] = _write_json(fixture["review"], review)
    with pytest.raises(ValueError, match="review_quality_passed"):
        _prepare(fixture, fixture["root"] / ".local" / "draft.json")

    fixture = _evidence(tmp_path / "second")
    with pytest.raises(ValueError, match="owner_matches_source_owner"):
        _prepare(
            fixture, fixture["root"] / ".local" / "draft.json", owner="tran.nghi",
        )


def test_prepare_rejects_bad_artifact_binding_and_output_path(tmp_path):
    fixture = _evidence(tmp_path)
    (fixture["disposition"].parent / "artifact.json").write_text("drift", encoding="utf-8")
    with pytest.raises(ValueError, match="pack_artifact_bindings"):
        _prepare(fixture, fixture["root"] / ".local" / "draft.json")

    fixture = _evidence(tmp_path / "second")
    with pytest.raises(ValueError, match="must stay under .local"):
        _prepare(fixture, fixture["root"] / "draft.json")


def test_finalize_rejects_hash_tool_authorization_and_time_drift(tmp_path):
    fixture = _evidence(tmp_path)
    root = fixture["root"] / ".local" / "decision"
    draft_path = root / "draft.json"
    draft, draft_sha = _prepare(fixture, draft_path)

    bad_hash = root / "bad-hash.json"
    _approval(bad_hash, "0" * 64, authorization=draft["requested_authorization"])
    with pytest.raises(ValueError, match="approval_draft_sha256"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=bad_hash, output_dir=root / "bad-hash", now=NOW,
        )

    widened = root / "widened.json"
    authorization = dict(draft["requested_authorization"])
    authorization["provider_traffic_authorized"] = True
    _approval(widened, draft_sha, authorization=authorization)
    with pytest.raises(ValueError, match="approval_authorization_exact"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=widened, output_dir=root / "widened", now=NOW,
        )

    expired = root / "expired.json"
    _approval(
        expired, draft_sha, authorization=draft["requested_authorization"],
        expires_at="2026-08-25T09:59:00Z",
    )
    with pytest.raises(ValueError, match="approval_time_order"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=expired, output_dir=root / "expired", now=NOW,
        )

    altered = dict(draft)
    altered["preparation_tool"] = {**altered["preparation_tool"], "sha256": "0" * 64}
    altered_sha = _write_json(draft_path, altered)
    wrong_tool = root / "wrong-tool.json"
    _approval(wrong_tool, altered_sha, authorization=draft["requested_authorization"])
    with pytest.raises(ValueError, match="preparation_tool_binding"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=wrong_tool, output_dir=root / "wrong-tool", now=NOW,
        )


def test_finalize_materializes_decision_but_no_activation_artifact(tmp_path):
    fixture = _evidence(tmp_path)
    root = fixture["root"] / ".local" / "decision"
    draft_path = root / "draft.json"
    draft, draft_sha = _prepare(fixture, draft_path)
    approval_path = root / "approval.json"
    _approval(approval_path, draft_sha, authorization=draft["requested_authorization"])

    receipt = finalize_decision(
        source_root=fixture["root"], draft_path=draft_path,
        approval_path=approval_path, output_dir=root / "final", now=NOW,
    )

    decision = json.loads(Path(receipt["owner_decision"]["path"]).read_text())
    assert decision["independent_human_review"]["reviewer"] == "tran.nghi"
    assert decision["owner_approval"]["owner"] == "bao.nguyen"
    assert decision["technical_eligible"] is True
    assert decision["production_eligible"] is False
    assert decision["feature_activation_authorized"] is False
    assert "activation_bundle" not in receipt
    assert "decision_ledger" not in receipt
    assert receipt["next_gate"] == "separate_activation_contract_and_preflight_authorization"


def test_finalize_rejects_existing_output_directory(tmp_path):
    fixture = _evidence(tmp_path)
    root = fixture["root"] / ".local" / "decision"
    draft_path = root / "draft.json"
    draft, draft_sha = _prepare(fixture, draft_path)
    approval_path = root / "approval.json"
    _approval(approval_path, draft_sha, authorization=draft["requested_authorization"])
    output_dir = root / "final"
    output_dir.mkdir()

    with pytest.raises(ValueError, match="final_output_dir_must_not_exist"):
        finalize_decision(
            source_root=fixture["root"], draft_path=draft_path,
            approval_path=approval_path, output_dir=output_dir, now=NOW,
        )
