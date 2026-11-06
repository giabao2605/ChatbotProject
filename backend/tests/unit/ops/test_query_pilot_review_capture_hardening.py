"""Fail-closed citation and accumulated-chain capture tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops import query_pilot_review_capture as capture_module
from scripts.ops.query_pilot_review_capture import (
    MAX_CITATION_COUNT,
    capture_answer,
    load_captured_review,
    validate_capture_chain,
)


def _card(index: int) -> dict:
    return {
        "card_id": f"query-pilot-{index:03d}",
        "case_id": f"complex-{index:02d}",
        "request_sha256": f"{index:x}" * 64,
        "review_capture_required": True,
    }


def _citation(index: int = 1) -> dict:
    return {
        "doc_id": 41,
        "page_no": index,
        "file_name": "confidential-policy.pdf",
        "file_goc": "confidential-policy.pdf",
        "version_no": 3,
        "score": 0.91,
        "trang": index,
        "source_id": f"D41P{index}",
    }


def _protect(plaintext: bytearray) -> bytes:
    return b"protected:" + bytes(reversed(plaintext))


def _unprotect(ciphertext: bytes) -> bytearray:
    assert ciphertext.startswith(b"protected:")
    return bytearray(reversed(ciphertext.removeprefix(b"protected:")))


def _capture(tmp_path: Path, index: int = 1, **overrides) -> dict:
    values = {
        "capture_dir": tmp_path,
        "source_commit": "a" * 40,
        "pilot_draft_sha256": "b" * 64,
        "consolidated_launch_draft_sha256": "f" * 64,
        "pilot_authorization_sha256": "c" * 64,
        "schedule_sha256": "d" * 64,
        "card": _card(index),
        "trace_id": f"private-trace-{index}",
        "answer": bytearray(f"answer {index} [SRC:D41P{index}]".encode()),
        "citations": (_citation(index),),
        "protect": _protect,
        "unprotect": _unprotect,
    }
    values.update(overrides)
    return capture_answer(**values)


def test_structured_citations_are_ciphertext_only_and_hash_bound(
    tmp_path: Path,
):
    metadata = _capture(tmp_path)
    artifact = tmp_path / "query-pilot-001.capture.json"
    raw = artifact.read_bytes()

    assert b"confidential-policy.pdf" not in raw
    assert b"D41P1" not in raw
    assert metadata["citation_count"] == 1
    assert metadata["citations_utf8_bytes"] > 0
    assert len(metadata["citations_sha256"]) == 64

    answer, citations, recovered_metadata = load_captured_review(
        artifact,
        expected=metadata,
        unprotect=_unprotect,
    )
    try:
        assert answer == b"answer 1 [SRC:D41P1]"
        assert citations == (_citation(),)
        assert recovered_metadata == metadata
    finally:
        answer[:] = b"\0" * len(answer)


@pytest.mark.parametrize(
    "citations",
    (
        ({**_citation(), "unexpected": "private"},),
        tuple(_citation() for _ in range(MAX_CITATION_COUNT + 1)),
        ({**_citation(), "file_name": "x" * (64 * 1024)},),
    ),
)
def test_capture_rejects_unbounded_or_non_schema_citations(
    tmp_path: Path, citations: tuple[dict, ...],
):
    with pytest.raises(ValueError, match="capture_citations_invalid"):
        _capture(tmp_path, citations=citations)
    assert not list(tmp_path.iterdir())


def test_capture_deletes_ciphertext_when_post_write_decrypt_validation_fails(
    tmp_path: Path,
):
    with pytest.raises(ValueError, match="capture_roundtrip_verification_failed"):
        _capture(tmp_path, unprotect=lambda _ciphertext: bytearray(b"corrupt"))

    assert not (tmp_path / "query-pilot-001.capture.json").exists()


@pytest.mark.parametrize("mutation", ("delete", "tamper"))
def test_accumulated_capture_chain_rejects_deletion_or_tamper(
    tmp_path: Path, mutation: str,
):
    first = _capture(tmp_path, 1)
    second = _capture(tmp_path, 2)
    expected = (first, second)

    assert validate_capture_chain(tmp_path, expected=expected) == expected

    path = tmp_path / "query-pilot-001.capture.json"
    if mutation == "delete":
        path.unlink()
    else:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        artifact["ciphertext_base64"] = "dGFtcGVyZWQ="
        path.write_bytes(capture_module._canonical(artifact) + b"\n")

    with pytest.raises(ValueError, match="capture_chain_invalid"):
        validate_capture_chain(tmp_path, expected=expected)


def test_citation_hash_is_over_canonical_structured_events(tmp_path: Path):
    citations = (_citation(), _citation(2))
    metadata = _capture(tmp_path, citations=citations)
    expected_raw = capture_module._canonical(list(citations))

    assert metadata["citations_sha256"] == hashlib.sha256(expected_raw).hexdigest()
    assert metadata["citations_utf8_bytes"] == len(expected_raw)
