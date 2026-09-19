"""Encrypted, hash-bound owner-review capture for the Query pilot."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess

import sys

import pytest

from scripts.ops import query_pilot_review_artifacts as review_artifacts
from scripts.ops import query_pilot_review_capture as review_capture
from scripts.ops.query_pilot_review_pack import (
    build_review_pack_from_paths,
    validate_review_pack_from_paths,
)
from scripts.ops.query_pilot_review_artifacts import (
    build_review_pack as _build_review_pack,
    delete_review_captures,
    finalize_review_result,
    review_artifacts_valid as _review_artifacts_valid,
)
from scripts.ops.query_pilot_review_capture import (
    MAX_CAPTURE_ARTIFACT_BYTES,
    REVIEW_CAPTURE_DESIGN_SHA256,
    _utf8_bytes_valid,
    capture_answer,
    dpapi_protect_current_user,
    dpapi_unprotect_current_user,
    load_captured_answer,
    prepare_capture_directory,
    review_capture_cards,
)


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


def _card() -> dict:
    return {
        "card_id": "query-pilot-001",
        "case_id": "complex-01",
        "request_sha256": "1" * 64,
        "review_capture_required": True,
    }


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


@pytest.mark.parametrize(
    ("raw", "valid"),
    (
        (b"ascii", True),
        ("việt".encode("utf-8"), True),
        (bytes([0xE0, 0xA0, 0x80]), True),
        (bytes([0xED, 0x9F, 0xBF]), True),
        (bytes([0xF0, 0x90, 0x80, 0x80]), True),
        (bytes([0xF4, 0x8F, 0xBF, 0xBF]), True),
        (bytes([0x80]), False),
        (bytes([0xC0, 0xAF]), False),
        (bytes([0xE0, 0x80, 0x80]), False),
        (bytes([0xED, 0xA0, 0x80]), False),
        (bytes([0xF0, 0x80, 0x80, 0x80]), False),
        (bytes([0xF4, 0x90, 0x80, 0x80]), False),
        (bytes([0xF5, 0x80, 0x80, 0x80]), False),
    ),
)
def test_utf8_validator_avoids_decode_copy_for_edge_bytes(raw: bytes, valid: bool):
    assert _utf8_bytes_valid(bytearray(raw)) is valid


def test_capture_round_trip_is_ciphertext_only_and_hash_bound(tmp_path: Path):
    answer = bytearray("Câu trả lời có nguồn [SRC:D41P1]".encode())
    capture = capture_answer(
        capture_dir=tmp_path,
        source_commit="a" * 40,
        pilot_draft_sha256="b" * 64,
        consolidated_launch_draft_sha256="f" * 64,
        pilot_authorization_sha256="c" * 64,
        schedule_sha256="d" * 64,
        card=_card(),
        trace_id="private-trace-01",
        answer=answer,
        protect=_protect,
        unprotect=_unprotect,
    )

    artifact = tmp_path / "query-pilot-001.capture.json"
    serialized = artifact.read_text(encoding="utf-8")
    assert "Câu trả lời" not in serialized
    assert "private-trace-01" not in serialized
    assert capture == {
        "schema": "query-decomposition-pilot-review-capture-v1",
        "source_commit": "a" * 40,
        "review_capture_design_sha256": REVIEW_CAPTURE_DESIGN_SHA256,
        "pilot_draft_sha256": "b" * 64,
        "consolidated_launch_draft_sha256": "f" * 64,
        "pilot_authorization_sha256": "c" * 64,
        "schedule_sha256": "d" * 64,
        "card_id": "query-pilot-001",
        "case_id": "complex-01",
        "request_sha256": "1" * 64,
        "trace_id_sha256": hashlib.sha256(b"private-trace-01").hexdigest(),
        "answer_sha256": hashlib.sha256(bytes(answer)).hexdigest(),
        "answer_utf8_bytes": len(answer),
        "citation_count": 0,
        "citations_sha256": hashlib.sha256(b"[]").hexdigest(),
        "citations_utf8_bytes": 2,
        "attempt_number": 1,
        "ciphertext_sha256": capture["ciphertext_sha256"],
    }

    recovered, metadata = load_captured_answer(
        artifact,
        expected=capture,
        unprotect=_unprotect,
    )
    try:
        assert recovered == answer
        assert metadata == capture
    finally:
        recovered[:] = b"\0" * len(recovered)


def test_capture_rejects_unselected_or_oversized_answer(tmp_path: Path):
    common = {
        "capture_dir": tmp_path,
        "source_commit": "a" * 40,
        "pilot_draft_sha256": "b" * 64,
        "consolidated_launch_draft_sha256": "f" * 64,
        "pilot_authorization_sha256": "c" * 64,
        "schedule_sha256": "d" * 64,
        "trace_id": "trace-01",
        "protect": _protect,
        "unprotect": _unprotect,
    }
    with pytest.raises(ValueError, match="capture_card_not_selected"):
        capture_answer(
            **common,
            card={**_card(), "review_capture_required": False},
            answer=bytearray(b"answer"),
        )
    with pytest.raises(ValueError, match="capture_answer_too_large"):
        capture_answer(
            **common,
            card=_card(),
            answer=bytearray(256 * 1024 + 1),
        )
    with pytest.raises(ValueError, match="capture_binding_invalid"):
        capture_answer(
            **common,
            card={**_card(), "card_id": "../escaped"},
            answer=bytearray(b"answer"),
        )
    with pytest.raises(ValueError, match="capture_answer_utf8_invalid"):
        capture_answer(
            **common,
            card=_card(),
            answer=bytearray([0xC0, 0xAF]),
        )

    (tmp_path / "foreign-entry.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="capture_directory_invalid"):
        capture_answer(
            **common,
            card=_card(),
            answer=bytearray(b"answer"),
        )


def test_capture_loader_rejects_oversized_artifact_before_decryption(
    tmp_path: Path,
):
    artifact = tmp_path / "oversized.capture.json"
    artifact.write_bytes(b"x" * (MAX_CAPTURE_ARTIFACT_BYTES + 1))

    with pytest.raises(ValueError, match="capture_artifact_too_large"):
        load_captured_answer(
            artifact,
            expected={},
            unprotect=lambda _ciphertext: pytest.fail("must not decrypt"),
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_dpapi_current_user_round_trip_uses_mutable_plaintext_buffer():
    plaintext = bytearray(b"synthetic review answer")
    ciphertext = dpapi_protect_current_user(plaintext)
    recovered = dpapi_unprotect_current_user(ciphertext)
    try:
        assert bytes(recovered) == plaintext
        assert b"synthetic review answer" not in ciphertext
    finally:
        recovered[:] = b"\0" * len(recovered)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_dpapi_failure_releases_any_returned_output_buffer(monkeypatch):
    output_buffer = (ctypes.c_ubyte * 3)(1, 2, 3)
    freed = []

    class FakeFunction:
        def __init__(self, callback):
            self.callback = callback
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.callback(*args)

    def fail_with_output(*args):
        output = args[-1]._obj
        output.cbData = len(output_buffer)
        output.pbData = ctypes.cast(
            output_buffer, ctypes.POINTER(ctypes.c_ubyte),
        )
        return False

    crypt32 = type("Crypt32", (), {})()
    crypt32.CryptProtectData = FakeFunction(fail_with_output)
    kernel32 = type("Kernel32", (), {})()
    kernel32.LocalFree = FakeFunction(lambda pointer: freed.append(pointer))
    monkeypatch.setattr(
        review_capture.ctypes,
        "WinDLL",
        lambda name, **_kwargs: crypt32 if name == "crypt32" else kernel32,
    )

    with pytest.raises(RuntimeError, match="windows_dpapi_operation_failed"):
        review_capture._dpapi("CryptProtectData", bytearray(b"secret"))

    assert len(freed) == 1
    assert bytes(output_buffer) == b"\0\0\0"


def test_review_capture_sample_requires_two_frozen_cards_per_case():
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
    selected = [card["card_id"] for card in cards[:20]]
    schedule = {"cards": cards, "review_capture_card_ids": selected}

    assert review_capture_cards(schedule) == frozenset(selected)
    with pytest.raises(ValueError, match="review_capture_sample_invalid"):
        review_capture_cards({
            **schedule,
            "review_capture_card_ids": [*selected[:-1], selected[0]],
        })


def test_prepare_capture_directory_is_new_dot_local_with_restricted_acl(
    tmp_path: Path,
):
    capture_dir = tmp_path / ".local" / "run" / "review-captures"
    restricted = []

    assert prepare_capture_directory(
        capture_dir,
        source_root=tmp_path,
        acl_setter=lambda path: restricted.append(path),
    ) == capture_dir.resolve()
    assert restricted == [capture_dir.resolve()]
    with pytest.raises(ValueError, match="capture_directory_not_fresh"):
        prepare_capture_directory(
            capture_dir,
            source_root=tmp_path,
            acl_setter=lambda _path: None,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL only")
def test_capture_directory_acl_allows_only_current_user_and_system(tmp_path: Path):
    capture_dir = prepare_capture_directory(
        tmp_path / ".local" / "run" / "review-captures",
        source_root=tmp_path,
    )
    identity = subprocess.check_output(["whoami"], text=True).strip()
    acl = subprocess.check_output(
        ["icacls", str(capture_dir)], text=True,
    )

    assert identity.casefold() in acl.casefold()
    assert "system" in acl.casefold()
    assert "(I)" not in acl
    assert "Successfully processed 1 files" in acl
    with pytest.raises(ValueError, match="capture_directory_outside_dot_local"):
        prepare_capture_directory(
            tmp_path / "outside",
            source_root=tmp_path,
            acl_setter=lambda _path: None,
        )


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


@pytest.mark.skipif(sys.platform != "win32", reason="exercises Windows ACL or process boundary")
def test_review_result_and_deletion_receipt_bind_complete_review(tmp_path: Path):
    authorization, schedule, rows, capture_dir = _review_fixture(tmp_path)
    pack = build_review_pack(
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=rows,
        capture_dir=capture_dir,
    )
    assert pack["items"][0]["evidence"]["query_result_status"] == "safe_refusal"
    assert pack["capture_count"] == 20
    assert pack["review_item_count"] == 21
    assert {item["review_mode"] for item in pack["items"]} == {
        "encrypted_answer", "deterministic_safe_refusal",
    }
    assert "answer 0" not in json.dumps(pack)

    labels = [
        {
            "card_id": item["card_id"],
            "trace_id_sha256": item["trace_id_sha256"],
            "answer_correct": True,
            "citation_correct": True,
            "safety_correct": True,
            "decision": "accepted",
            "reason_code": "pass",
        }
        for item in pack["items"]
    ]
    result = finalize_review_result(
        pack,
        labels,
        reviewer="bao.nguyen",
        evaluated_at="2026-08-29T02:00:00Z",
    )
    receipt = delete_review_captures(
        capture_dir,
        pack=pack,
        review_result=result,
        deleted_at="2026-08-29T02:01:00Z",
    )

    assert result["all_accepted"] is True
    assert not list(capture_dir.glob("*.capture.json"))
    assert receipt["deleted_capture_count"] == 20
    assert review_artifacts_valid(
        pack=pack,
        review_result=result,
        deletion_receipt=receipt,
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=rows,
    ) is True
    malformed_rows = [*rows]
    malformed_rows[0] = []
    assert review_artifacts_valid(
        pack=pack,
        review_result=result,
        deletion_receipt=receipt,
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=malformed_rows,
    ) is False
    forged_rows = [*rows]
    forged_rows[0] = {
        **forged_rows[0],
        "authorization_sha256": "0" * 64,
    }
    assert review_artifacts_valid(
        pack=pack,
        review_result=result,
        deletion_receipt=receipt,
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=forged_rows,
    ) is False


@pytest.mark.parametrize(
    ("mutate_row", "expected_error"),
    (
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "card_id": rows[1]["card_id"]},
            ),
            "review_pack_inputs_invalid",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "case_id": "complex-99"},
            ),
            "review_pack_inputs_invalid",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "attempt_number": 2},
            ),
            "review_pack_inputs_invalid",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "trace_id_sha256": "9" * 64},
            ),
            "review_capture_binding_invalid",
        ),
    ),
)
def test_build_review_pack_requires_exact_wal_mapping(
    tmp_path: Path, mutate_row, expected_error: str,
):
    authorization, schedule, rows, capture_dir = _review_fixture(tmp_path)
    mutate_row(rows)

    with pytest.raises(ValueError, match=expected_error):
        build_review_pack(
            authorization=authorization,
            authorization_sha256="c" * 64,
            schedule=schedule,
            schedule_sha256="d" * 64,
            rows=rows,
            capture_dir=capture_dir,
        )


def test_build_review_pack_from_paths_stays_under_dot_local(
    tmp_path: Path, monkeypatch,
):
    authorization, schedule, rows, capture_dir = _review_fixture(tmp_path)
    local = tmp_path / ".local" / "run"
    local.mkdir(parents=True)
    captures = local / "review-captures"
    capture_dir.rename(captures)
    authorization_path = local / "pilot-authorization.json"
    schedule_path = local / "schedule.json"
    wal_path = local / "pilot.wal.jsonl"
    trace_path = local / "trace.jsonl"
    output_path = local / "review-pack.json"
    authorization_path.write_text(
        json.dumps(authorization), encoding="utf-8",
    )
    schedule_path.write_text(json.dumps(schedule), encoding="utf-8")
    for path in captures.glob("*.capture.json"):
        path.unlink()
    authorization_sha = hashlib.sha256(
        authorization_path.read_bytes(),
    ).hexdigest()
    schedule_sha = hashlib.sha256(schedule_path.read_bytes()).hexdigest()
    rows = [
        {
            **row,
            "authorization_sha256": authorization_sha,
            "schedule_sha256": schedule_sha,
        }
        for row in rows
    ]
    wal_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    trace_path.write_text('{"event":"metadata-only"}\n', encoding="utf-8")
    monkeypatch.setattr(
        "scripts.ops.query_pilot_review_pack.validate_authorized_source",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "scripts.ops.query_pilot_review_pack.review_tool_hashes",
        lambda *_args: {"scripts/ops/review.py": "8" * 64},
    )
    for index, card in enumerate(schedule["cards"][:20]):
        capture_answer(
            capture_dir=captures,
            source_commit=authorization["source_commit"],
            pilot_draft_sha256=authorization["pilot_draft"]["sha256"],
            consolidated_launch_draft_sha256=(
                authorization["consolidated_launch_draft"]["sha256"]
            ),
            pilot_authorization_sha256=authorization_sha,
            schedule_sha256=schedule_sha,
            card=card,
            trace_id=f"trace-{index + 1:03d}",
            answer=bytearray(f"answer {index}".encode()),
            protect=_protect,
            unprotect=_unprotect,
        )
    result = build_review_pack_from_paths(
        source_root=tmp_path,
        authorization_path=authorization_path,
        schedule_path=schedule_path,
        wal_path=wal_path,
        trace_path=trace_path,
        capture_dir=captures,
        output_path=output_path,
    )

    assert result["status"] == "ready_for_local_owner_review"
    assert result["raw_content_emitted"] is False
    assert output_path.exists()
    assert result["review_pack_sha256"] == hashlib.sha256(
        output_path.read_bytes()
    ).hexdigest()
    assert validate_review_pack_from_paths(
        source_root=tmp_path,
        authorization_path=authorization_path,
        schedule_path=schedule_path,
        wal_path=wal_path,
        trace_path=trace_path,
        capture_dir=captures,
        pack_path=output_path,
    )["schema"] == review_artifacts.REVIEW_PACK_SCHEMA
    tampered_pack = json.loads(output_path.read_text(encoding="utf-8"))
    tampered_pack["reviewer"] = "not-the-authorized-owner"
    output_path.write_bytes(review_artifacts._canonical(tampered_pack) + b"\n")
    with pytest.raises(ValueError, match="run_binding_invalid"):
        validate_review_pack_from_paths(
            source_root=tmp_path,
            authorization_path=authorization_path,
            schedule_path=schedule_path,
            wal_path=wal_path,
            trace_path=trace_path,
            capture_dir=captures,
            pack_path=output_path,
        )
    assert len(list(captures.glob("*.capture.json"))) == 20
    with pytest.raises(ValueError, match="run_root_mismatch"):
        build_review_pack_from_paths(
            source_root=tmp_path,
            authorization_path=authorization_path,
            schedule_path=schedule_path,
            wal_path=wal_path,
            trace_path=trace_path,
            capture_dir=local / "wrong-captures",
            output_path=output_path.with_name("wrong-pack.json"),
        )
    with pytest.raises(ValueError, match="outside_dot_local"):
        build_review_pack_from_paths(
            source_root=tmp_path,
            authorization_path=authorization_path,
            schedule_path=schedule_path,
            wal_path=wal_path,
            trace_path=trace_path,
            capture_dir=captures,
            output_path=tmp_path / "outside.json",
        )


def test_review_rejects_missing_label_and_capture_tamper(tmp_path: Path):
    authorization, schedule, rows, capture_dir = _review_fixture(tmp_path)
    artifact = capture_dir / "query-pilot-001.capture.json"
    value = json.loads(artifact.read_text(encoding="utf-8"))
    value["case_id"] = "complex-99"
    artifact.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="review_capture_binding_invalid"):
        build_review_pack(
            authorization=authorization,
            authorization_sha256="c" * 64,
            schedule=schedule,
            schedule_sha256="d" * 64,
            rows=rows,
            capture_dir=capture_dir,
        )


def test_review_pack_and_deletion_reject_extra_capture_directory_entry(
    tmp_path: Path,
):
    authorization, schedule, rows, capture_dir = _review_fixture(tmp_path)
    extra = capture_dir / "renamed-ciphertext.bin"
    extra.write_bytes(b"not-an-allowed-capture-entry")

    with pytest.raises(ValueError, match="review_capture_count_invalid"):
        build_review_pack(
            authorization=authorization,
            authorization_sha256="c" * 64,
            schedule=schedule,
            schedule_sha256="d" * 64,
            rows=rows,
            capture_dir=capture_dir,
        )

    extra.unlink()
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
    extra.write_bytes(b"renamed-ciphertext")
    with pytest.raises(ValueError, match="capture_deletion_set_invalid"):
        delete_review_captures(
            capture_dir, pack=pack, review_result=result,
            deleted_at="2026-08-29T02:01:00Z",
        )
    assert len(list(capture_dir.glob("*.capture.json"))) == 20


@pytest.mark.skipif(sys.platform != "win32", reason="exercises Windows ACL or process boundary")
def test_rejected_review_still_deletes_ciphertext_but_never_passes_gate(
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
    labels = [{
        "card_id": item["card_id"],
        "trace_id_sha256": item["trace_id_sha256"],
        "answer_correct": index != 0,
        "citation_correct": True,
        "safety_correct": True,
        "decision": "rejected" if index == 0 else "accepted",
        "reason_code": "answer_incorrect" if index == 0 else "pass",
    } for index, item in enumerate(pack["items"])]
    result = finalize_review_result(
        pack, labels, reviewer="bao.nguyen",
        evaluated_at="2026-08-29T02:00:00Z",
    )

    receipt = delete_review_captures(
        capture_dir, pack=pack, review_result=result,
        deleted_at="2026-08-29T02:01:00Z",
    )

    assert result["all_accepted"] is False
    assert receipt["review_result_sha256"] == hashlib.sha256(
        review_artifacts._canonical(result) + b"\n"
    ).hexdigest()
    assert receipt["deleted_capture_count"] == 20
    assert not list(capture_dir.glob("*.capture.json"))
    assert review_artifacts_valid(
        pack=pack,
        review_result=result,
        deletion_receipt=receipt,
        authorization=authorization,
        authorization_sha256="c" * 64,
        schedule=schedule,
        schedule_sha256="d" * 64,
        rows=rows,
    ) is False
