"""Metadata-only CLI tests for the Query pilot review pack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops import query_pilot_review_pack as review_pack
from scripts.ops import query_pilot_review_capture as review_capture
from scripts.ops.query_pilot_review_artifacts import (
    load_manifest_questions,
    load_metadata_artifact,
    review_label_valid,
    write_metadata_artifact,
)


def test_metadata_artifact_requires_canonical_bytes(tmp_path: Path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text(
        '{"schema": "synthetic-v1", "metadata": true}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not_canonical"):
        load_metadata_artifact(artifact, schema="synthetic-v1")


def test_one_review_label_requires_exact_identity_and_decision_contract():
    item = {
        "card_id": "query-pilot-001",
        "trace_id_sha256": "1" * 64,
    }
    accepted = {
        "card_id": "query-pilot-001",
        "trace_id_sha256": "1" * 64,
        "answer_correct": True,
        "citation_correct": True,
        "safety_correct": True,
        "decision": "accepted",
        "reason_code": "pass",
    }
    assert review_label_valid(item, accepted) is True
    assert review_label_valid(item, {**accepted, "extra": False}) is False
    assert review_label_valid(
        item, {**accepted, "citation_correct": False},
    ) is False
    rejected = {
        **accepted,
        "answer_correct": False,
        "decision": "rejected",
        "reason_code": "answer_incorrect",
    }
    assert review_label_valid(item, rejected) is True
    assert review_label_valid(
        item, {**rejected, "reason_code": "private_customer_detail"},
    ) is False
    with pytest.raises(ValueError, match="contains_raw_content"):
        write_metadata_artifact(
            Path("unused.json"), {"nested": {"content": "private"}},
        )


def test_capture_read_after_write_mismatch_is_terminal(tmp_path, monkeypatch):
    def corrupt_write(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw + b"corrupt")

    monkeypatch.setattr(review_capture, "_exclusive_atomic_bytes", corrupt_write)
    with pytest.raises(ValueError, match="persistence_verification_failed"):
        review_capture.capture_answer(
            capture_dir=tmp_path,
            source_commit="a" * 40,
            pilot_draft_sha256="b" * 64,
            consolidated_launch_draft_sha256="f" * 64,
            pilot_authorization_sha256="c" * 64,
            schedule_sha256="d" * 64,
            card={
                "card_id": "query-pilot-001",
                "case_id": "complex-01",
                "request_sha256": "1" * 64,
                "review_capture_required": True,
            },
            trace_id="private-trace",
            answer=bytearray(b"private answer"),
            protect=lambda raw: b"protected:" + bytes(raw),
            unprotect=lambda raw: bytearray(raw.removeprefix(b"protected:")),
        )


def test_manifest_loader_and_review_pack_cli_emit_metadata_only(
    tmp_path: Path, monkeypatch, capsys,
):
    manifest = tmp_path / "manifest.jsonl"
    raw = "".join(
        json.dumps({
            "id": f"complex-{index:02d}",
            "evaluation_group": "complex",
            "question": f"private question {index}",
        }) + "\n"
        for index in range(1, 11)
    ).encode()
    manifest.write_bytes(raw)

    questions = load_manifest_questions(
        manifest,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert len(questions) == 10
    with pytest.raises(ValueError, match="review_manifest_drift"):
        load_manifest_questions(manifest, expected_sha256="0" * 64)

    monkeypatch.setattr(
        review_pack,
        "build_review_pack_from_paths",
        lambda **_kwargs: {
            "schema": "query-decomposition-pilot-review-pack-build-v1",
            "status": "ready_for_local_owner_review",
            "review_pack_sha256": "a" * 64,
            "capture_count": 20,
            "review_item_count": 20,
            "raw_content_emitted": False,
        },
    )
    result = review_pack.main([
        "--source-root", str(tmp_path),
        "--authorization", str(tmp_path / "authorization.json"),
        "--schedule", str(tmp_path / "schedule.json"),
        "--wal", str(tmp_path / "wal.jsonl"),
        "--trace", str(tmp_path / "trace.jsonl"),
        "--capture-dir", str(tmp_path / "captures"),
        "--output", str(tmp_path / "pack.json"),
    ])

    emitted = capsys.readouterr().out
    assert result == 0
    assert json.loads(emitted)["raw_content_emitted"] is False
    assert "private question" not in emitted
