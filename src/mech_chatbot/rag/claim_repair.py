"""Single-pass grounded claim repair used after deterministic post-checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from mech_chatbot.rag.answer_checks import (
    has_unsupported_codes,
    has_unsupported_materials,
    has_unsupported_units_symbols,
    has_valid_source_citation,
)
from mech_chatbot.rag.evidence_gate import find_unsupported_numbers


@dataclass(frozen=True)
class RepairResult:
    answer: str
    attempted: bool
    accepted: bool
    violation_reason: str = ""


def _validate(answer, *, context_text, question, documents, require_citation):
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
    prompt = (
        "Repair the draft using only facts and numbers present in CONTEXT or QUESTION. "
        "Remove unsupported claims. Preserve or add exact file/page/version/SourceID citations. "
        "Do not explain the repair. Return only the repaired answer.\n\n"
        f"QUESTION:\n{question}\n\nCONTEXT:\n{context_text[:12000]}\n\nDRAFT:\n{answer}"
    )
    repaired = str(invoke(prompt) or "").strip()
    violation = _validate(
        repaired,
        context_text=context_text,
        question=question,
        documents=list(documents or []),
        require_citation=require_citation,
    )
    return RepairResult(
        answer=repaired if not violation else answer,
        attempted=True,
        accepted=not violation,
        violation_reason=violation,
    )

