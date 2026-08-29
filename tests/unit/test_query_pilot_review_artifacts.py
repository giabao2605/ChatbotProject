"""Strict owner-review artifact loading and metadata validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops import query_pilot_review_artifacts as review_artifacts
from scripts.ops.query_pilot_review_artifacts import (
    build_review_pack as _build_review_pack,
    delete_review_captures,
    finalize_review_result,
    load_metadata_artifact,
    load_metadata_rows,
    load_review_item_content,
    review_artifacts_valid as _review_artifacts_valid,
)
from scripts.ops.query_pilot_review_capture import capture_answer


def build_review_pack(**kwargs):
    return _build_review_pack(
        wal_sha256="6" * 64,
        trace_artifact_sha256="7" * 64,
        review_tool_sha256={"scripts/ops/review.py": "8" * 64},
        **kwargs,
    )


def review_artifacts_valid(**kwargs):
    receipt = kwargs["deletion_receipt"]
    return _review_artifacts_valid(
        wal_sha256="6" * 64,
        trace_artifact_sha256="7" * 64,
        review_tool_sha256={"scripts/ops/review.py": "8" * 64},
        deletion_journal={
            "schema": "query-pilot-capture-deletion-journal-v1",
            "operation": "owner_review",
            "status": "finalized",
            "remaining_capture_count": 0,
            "binding_sha256": receipt.get("review_result_sha256"),
        },
        deletion_journal_sha256=receipt.get("deletion_journal_sha256"),
        **kwargs,
    )


def _protect(plaintext: bytearray) -> bytes:
    return b"protected:" + bytes(reversed(plaintext))


def _unprotect(ciphertext: bytes) -> bytearray:
    assert ciphertext.startswith(b"protected:")
    return bytearray(reversed(ciphertext.removeprefix(b"protected:")))


def _evidence(refusal: bool) -> dict:
    return {
        "route": "query_decomposition",
        "query_result_status": "safe_refusal" if refusal else "valid",
        "completion_outcome": "refused" if refusal else "answered",
        "refusal_reason_code": "evidence_gate" if refusal else None,
        "refusal_template_passed": refusal,
        "owner_review_required": refusal,
        "security_passed": True,
        "citation_structure_passed": not refusal,
        "provenance_passed": not refusal,
        "leakage_detected": False,
        "planner_calls": 0,
        "subquery_count": 2,
        "correction_count": 0,
        "intent_count": 2,
        "intent_coverage_complete": True,
        "deterministic_split_used": True,
        "intent_overflow": False,
        "final_latency_ms": 100,
        "request_deadline_ms": 120000,
        "estimated_cost": 0.001,
        "provider_retries": 0,
        "final_generations": 0 if refusal else 1,
    }


def _review_fixture(tmp_path: Path):
    cards = [
        {
            "card_id": f"query-pilot-{index + 1:03d}",
            "case_id": f"complex-{index % 10 + 1:02d}",
            "request_sha256": hashlib.sha256(
                f"private question {index % 10 + 1}".encode()
            ).hexdigest(),
            "review_capture_required": index < 20,
        }
        for index in range(100)
    ]
    schedule = {
        "schema": "query-decomposition-pilot-schedule-v1",
        "cards": cards,
        "review_capture_card_ids": [card["card_id"] for card in cards[:20]],
        "manifest": {"sha256": "e" * 64},
    }
    authorization = {
        "schema": "query-controlled-demo-pilot-authorization-v1",
        "source_commit": "a" * 40,
        "actor": "bao.nguyen",
        "pilot_draft": {"sha256": "b" * 64},
        "consolidated_launch_draft": {"sha256": "f" * 64},
        "pilot_run_root": ".local/run",
    }
    rows = []
    capture_dir = tmp_path / "review-captures"
    capture_dir.mkdir(parents=True)
    for index, card in enumerate(cards):
        trace_id = f"trace-{index + 1:03d}"
        refusal = index in {0, 30}
        rows.append({
            "schema": "query-decomposition-pilot-wal-v1",
            "authorization_sha256": "c" * 64,
            "schedule_sha256": "d" * 64,
            "card_id": card["card_id"],
            "case_id": card["case_id"],
            "trace_id_sha256": hashlib.sha256(trace_id.encode()).hexdigest(),
            "runtime_identity_sha256": "a" * 64,
            "attempt_number": 1,
            "attempted_at": "2026-08-28T00:00:00Z",
            "completed_at": "2026-08-28T00:00:01Z",
            "evidence": _evidence(refusal),
        })
        if index < 20:
            capture_answer(
                capture_dir=capture_dir,
                source_commit=authorization["source_commit"],
                pilot_draft_sha256=authorization["pilot_draft"]["sha256"],
                consolidated_launch_draft_sha256=(
                    authorization["consolidated_launch_draft"]["sha256"]
                ),
                pilot_authorization_sha256="c" * 64,
                schedule_sha256="d" * 64,
                card=card,
                trace_id=trace_id,
                answer=bytearray(f"answer {index}".encode()),
                protect=_protect,
                unprotect=_unprotect,
            )
    return authorization, schedule, rows, capture_dir


def test_review_ui_loader_decrypts_one_item_and_renders_refusal_in_memory(
    tmp_path: Path,
):
    authorization, schedule, rows, capture_dir = _review_fixture(tmp_path)
    pack = build_review_pack(
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=rows,
        capture_dir=capture_dir,
    )
    questions = {
        f"complex-{index:02d}": f"private question {index}"
        for index in range(1, 11)
    }

    question, answer = load_review_item_content(
        pack,
        pack["items"][0],
        capture_dir=capture_dir,
        questions=questions,
        unprotect=_unprotect,
    )
    try:
        assert question == "private question 1"
        assert answer == b"answer 0"
    finally:
        answer[:] = b"\0" * len(answer)
    with pytest.raises(ValueError, match="question_binding_mismatch"):
        load_review_item_content(
            pack, pack["items"][0], capture_dir=capture_dir,
            questions={**questions, "complex-01": "different question"},
            unprotect=_unprotect,
        )

    encrypted_item = pack["items"][0]
    encrypted_item["capture_file"] = "../escaped.capture.json"
    with pytest.raises(ValueError, match="review_capture_path_invalid"):
        load_review_item_content(
            pack,
            encrypted_item,
            capture_dir=capture_dir,
            questions=questions,
            unprotect=_unprotect,
        )

    refusal = next(
        item for item in pack["items"]
        if item["review_mode"] == "deterministic_safe_refusal"
    )
    assert refusal["evidence"]["query_result_status"] == "safe_refusal"
    question, answer = load_review_item_content(
        pack,
        refusal,
        capture_dir=capture_dir,
        questions=questions,
        unprotect=_unprotect,
    )
    try:
        assert question == "private question 1"
        assert "không ghi thông tin đủ" in answer.decode()
    finally:
        answer[:] = b"\0" * len(answer)

    refusal["evidence"]["refusal_reason_code"] = "unbound_reason"
    with pytest.raises(ValueError, match="review_refusal_reason_invalid"):
        load_review_item_content(
            pack,
            refusal,
            capture_dir=capture_dir,
            questions=questions,
            unprotect=_unprotect,
        )


def test_metadata_loader_rejects_nested_raw_content_and_unknown_fields(
    tmp_path: Path,
):
    artifact = tmp_path / "pack.json"
    artifact.write_text(json.dumps({
        "schema": "synthetic-v1",
        "items": [{"answer": "must not persist"}],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="contains_raw_content"):
        load_metadata_artifact(artifact, schema="synthetic-v1")
    rows = tmp_path / "wal.jsonl"
    rows.write_text(
        json.dumps({"card_id": "query-pilot-001", "answer": "private"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="contains_raw_content"):
        load_metadata_rows(rows)


def test_review_pack_rejects_unknown_wal_fields(tmp_path: Path):
    authorization, schedule, rows, capture_dir = _review_fixture(tmp_path)
    rows[0] = {**rows[0], "full_answer": "must not be accepted"}

    with pytest.raises(ValueError, match="review_pack_inputs_invalid"):
        build_review_pack(
            authorization=authorization,
            authorization_sha256="c" * 64,
            schedule=schedule,
            schedule_sha256="d" * 64,
            rows=rows,
            capture_dir=capture_dir,
        )

    authorization, schedule, rows, capture_dir = _review_fixture(
        tmp_path / "valid",
    )
    pack = build_review_pack(
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=rows,
        capture_dir=capture_dir,
    )
    labels = [{
        "card_id": item["card_id"],
        "trace_id_sha256": item["trace_id_sha256"],
        "answer_correct": True,
        "citation_correct": True,
        "safety_correct": True,
        "decision": "accepted",
        "reason_code": "pass",
    } for item in pack["items"]]
    result = finalize_review_result(
        pack, labels, reviewer="bao.nguyen",
        evaluated_at="2026-08-29T02:00:00Z",
    )
    receipt = delete_review_captures(
        capture_dir, pack=pack, review_result=result,
        deleted_at="2026-08-29T02:01:00Z",
    )
    assert review_artifacts_valid(
        pack={**pack, "unknown": False},
        review_result=result,
        deletion_receipt=receipt,
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=rows,
    ) is False
