"""In-memory content loading for local Query pilot owner review."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Callable, Mapping

from mech_chatbot.rag.evidence_gate import make_insufficient_evidence_message
from scripts.ops.query_pilot_review_capture import (
    CAPTURE_SCHEMA,
    _METADATA_FIELDS,
    _canonical,
    _digest,
    _read_capture_bytes,
    _sha256,
    _strict_object,
    dpapi_unprotect_current_user,
    load_captured_review,
)


REVIEW_PACK_SCHEMA = "query-decomposition-pilot-review-pack-v3"


def load_manifest_questions(
    path: str | Path, *, expected_sha256: str,
) -> dict[str, str]:
    raw = Path(path).read_bytes()
    if not _digest(expected_sha256) or _sha256(raw) != expected_sha256:
        raise ValueError("review_manifest_drift")
    questions = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        value = _strict_object(line)
        if value.get("evaluation_group") != "complex":
            continue
        case_id, question = value.get("id"), value.get("question")
        if not all((
            isinstance(case_id, str), isinstance(question, str),
            question.strip() if isinstance(question, str) else False,
            case_id not in questions,
        )):
            raise ValueError("review_manifest_invalid")
        questions[case_id] = question
    if len(questions) != 10:
        raise ValueError("review_manifest_invalid")
    return questions


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _capture_record(path: Path) -> dict:
    raw = _read_capture_bytes(path)
    artifact = _strict_object(raw)
    if set(artifact) != _METADATA_FIELDS | {"ciphertext_base64"}:
        raise ValueError("review_capture_binding_invalid")
    if raw != _canonical(artifact) + b"\n":
        raise ValueError("review_capture_binding_invalid")
    try:
        ciphertext = base64.b64decode(artifact["ciphertext_base64"], validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("review_capture_binding_invalid") from exc
    metadata = {name: artifact[name] for name in _METADATA_FIELDS}
    if metadata.get("schema") != CAPTURE_SCHEMA or (
        _sha256(ciphertext) != metadata.get("ciphertext_sha256")
    ):
        raise ValueError("review_capture_binding_invalid")
    return {**metadata, "capture_artifact_sha256": _sha256(raw)}


def load_review_item_content_with_citations(
    pack: Mapping[str, object], item: Mapping[str, object], *,
    capture_dir: str | Path, questions: Mapping[str, str],
    unprotect: Callable[[bytes], bytearray] = dpapi_unprotect_current_user,
) -> tuple[str, bytearray, tuple[dict, ...]]:
    if pack.get("schema") != REVIEW_PACK_SCHEMA or item not in (pack.get("items") or ()):
        raise ValueError("review_item_invalid")
    case_id = item.get("case_id")
    question = questions.get(case_id) if isinstance(case_id, str) else None
    if not isinstance(question, str) or not question.strip():
        raise ValueError("review_question_missing")
    if _sha256(question.encode("utf-8")) != item.get("request_sha256"):
        raise ValueError("review_question_binding_mismatch")
    if item.get("review_mode") == "deterministic_safe_refusal":
        evidence = item.get("evidence")
        reason = evidence.get("refusal_reason_code") if isinstance(evidence, dict) else None
        if reason != "evidence_gate":
            raise ValueError("review_refusal_reason_invalid")
        answer = make_insufficient_evidence_message(question, reason, "vi")
        return question, bytearray(answer.encode("utf-8")), ()
    if item.get("review_mode") != "encrypted_answer":
        raise ValueError("review_item_mode_invalid")
    root = Path(capture_dir).resolve()
    name = item.get("capture_file")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError("review_capture_path_invalid")
    path = (root / name).resolve()
    if not _inside(path, root) or path.is_symlink():
        raise ValueError("review_capture_path_invalid")
    capture = _capture_record(path)
    bound = (
        "capture_artifact_sha256", "ciphertext_sha256", "answer_sha256",
        "answer_utf8_bytes", "citation_count", "citations_sha256",
        "citations_utf8_bytes", "card_id", "case_id", "trace_id_sha256",
    )
    if any(capture[field] != item.get(field) for field in bound):
        raise ValueError("review_item_capture_mismatch")
    expected = {name: capture[name] for name in _METADATA_FIELDS}
    answer, citations, _ = load_captured_review(path, expected=expected, unprotect=unprotect)
    return question, answer, citations


def load_review_item_content(
    pack: Mapping[str, object], item: Mapping[str, object], *,
    capture_dir: str | Path, questions: Mapping[str, str],
    unprotect: Callable[[bytes], bytearray] = dpapi_unprotect_current_user,
) -> tuple[str, bytearray]:
    question, answer, _ = load_review_item_content_with_citations(
        pack, item, capture_dir=capture_dir, questions=questions, unprotect=unprotect,
    )
    return question, answer


__all__ = [
    "load_manifest_questions", "load_review_item_content",
    "load_review_item_content_with_citations",
]
