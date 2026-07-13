"""Single-pass grounded claim repair used after deterministic post-checks."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable

from mech_chatbot.rag.answer_checks import (
    has_unsupported_codes,
    has_unsupported_materials,
    has_unsupported_units_symbols,
    has_valid_source_citation,
)
from mech_chatbot.rag.evidence_gate import find_unsupported_numbers, normalized_number_values


@dataclass(frozen=True)
class RepairResult:
    answer: str
    attempted: bool
    accepted: bool
    violation_reason: str = ""


def _validate(answer, *, context_text, question, documents, require_citation, required_source_ids):
    bad_materials, _ = has_unsupported_materials(answer, context_text)
    bad_codes, _ = has_unsupported_codes(answer, context_text, question)
    bad_units, _ = has_unsupported_units_symbols(answer, context_text, question)
    if bad_materials or bad_codes:
        return "materials_or_codes"
    if bad_units:
        return "units"
    if find_unsupported_numbers(answer, context_text, question, strict_mode=True):
        return "numbers"
    if require_citation and not has_valid_source_citation(
        answer, documents, require_version=True
    ):
        return "citation"
    answer_source_ids = set(re.findall(r"\bD\d+P\d+\b", answer, flags=re.IGNORECASE))
    if required_source_ids and not {value.upper() for value in required_source_ids}.issubset(
        {value.upper() for value in answer_source_ids}
    ):
        return "citation"
    return ""


def repair_grounded_answer(
    answer: str,
    *,
    context_text: str,
    question: str,
    documents: Iterable,
    invoke: Callable[[str], str],
    require_citation: bool,
    enabled: bool,
) -> RepairResult:
    """Attempt exactly one rewrite and accept it only if every guard passes."""
    if not enabled:
        return RepairResult(answer=answer, attempted=False, accepted=False)
    documents = list(documents or [])
    violations = find_unsupported_numbers(
        answer, context_text, question, strict_mode=True
    )
    allowed_numbers = sorted(
        normalized_number_values(context_text) | normalized_number_values(question)
    )
    original_source_ids = sorted(set(re.findall(r"\bD\d+P\d+\b", answer, flags=re.IGNORECASE)))
    allowed_source_ids = sorted(
        {
            f"D{metadata.get('doc_id')}P{metadata.get('trang_so')}"
            for document in documents
            for metadata in [getattr(document, "metadata", {}) or {}]
            if metadata.get("doc_id") is not None and metadata.get("trang_so") is not None
        }
    )
    prompt = (
        "Repair the draft using only facts and numbers present in CONTEXT or QUESTION. "
        "Remove unsupported claims. Preserve every REQUIRED_SOURCE_ID exactly; only use ALLOWED_SOURCE_IDS. "
        "Do not explain the repair. Return only the repaired answer.\n\n"
        f"UNSUPPORTED_NUMBERS: {[item.normalized for item in violations]}\n"
        f"ALLOWED_NUMBERS: {allowed_numbers}\n"
        f"REQUIRED_SOURCE_IDS: {original_source_ids}\n"
        f"ALLOWED_SOURCE_IDS: {allowed_source_ids}\n\n"
        f"QUESTION:\n{question}\n\nCONTEXT:\n{context_text[:12000]}\n\nDRAFT:\n{answer}"
    )
    repaired = str(invoke(prompt) or "").strip()
    violation = _validate(
        repaired,
        context_text=context_text,
        question=question,
        documents=documents,
        require_citation=require_citation,
        required_source_ids=original_source_ids,
    )
    return RepairResult(
        answer=repaired if not violation else answer,
        attempted=True,
        accepted=not violation,
        violation_reason=violation,
    )
