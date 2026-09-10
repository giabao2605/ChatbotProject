"""Metadata-only validation for capability pilot trace evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mech_chatbot.evaluation.decomposition import normalize_decomposition_usage
from mech_chatbot.governance.graph_pilot_review import APPROVED_GRAPH_REFUSALS
from mech_chatbot.rag.answer_checks import extract_source_ids
from mech_chatbot.rag.evidence_gate import make_insufficient_evidence_message


_SOURCE_ID_RE = re.compile(
    r"(?:source[_ ]?id\s*[:#]?\s*|\[SRC:)(?P<source_id>D\d+P\d+)",
    re.IGNORECASE,
)
_REFUSAL_TOKEN = "GRAPH_REFUSAL_REASON"
_GRAPH_CONFIDENCE_THRESHOLD = 0.5


def _refusal_pattern(language: str) -> re.Pattern[str]:
    template = make_insufficient_evidence_message("", _REFUSAL_TOKEN, language)
    escaped = re.escape(template).replace(
        re.escape(_REFUSAL_TOKEN), r"[^()\r\n]{1,200}"
    )
    return re.compile(escaped)


_REFUSAL_PATTERNS = tuple(_refusal_pattern(lang) for lang in ("vi", "en"))


@dataclass(frozen=True, slots=True)
class GraphPilotValidation:
    graph_result_status: str
    security_passed: bool
    citation_structure_passed: bool
    provenance_passed: bool
    leakage_detected: bool
    graph_evidence_count: int
    rendered_citation_count: int
    graph_citation_count: int
    full_answer: bool
    low_confidence: bool


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _nonnegative_float(value: object) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return 0.0
    return normalized if normalized >= 0 else 0.0


def _confidence(value: object) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if 0.0 <= normalized <= 1.0 else None


def _source_id(item: Mapping[str, Any]) -> str | None:
    doc_id = _positive_int(item.get("doc_id"))
    page = _positive_int(
        item.get("trang") or item.get("trang_so") or item.get("page_no")
    )
    if doc_id is None or page is None:
        return None
    expected = f"D{doc_id}P{page}"
    declared = str(item.get("source_id") or expected).strip().upper()
    return expected if declared == expected else None


def _metadata_rows(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _graph_provenance_complete(item: Mapping[str, Any]) -> bool:
    return all(
        (
            _source_id(item) is not None,
            _positive_int(item.get("version_no")) is not None,
            bool(str(item.get("graph_edge_id") or "").strip()),
            bool(str(item.get("graph_relation_type") or "").strip()),
            bool(str(item.get("graph_source_key") or "").strip()),
            bool(str(item.get("graph_target_key") or "").strip()),
        )
    )


def _source_ids(
    rows: tuple[Mapping[str, Any], ...],
    *,
    require_version: bool = False,
) -> set[str]:
    return {
        source_id
        for row in rows
        if (source_id := _source_id(row)) is not None
        and (
            not require_version
            or _positive_int(row.get("version_no")) is not None
        )
    }


def _answer_contract(payload: Mapping[str, Any], answer: str):
    rendered = {
        match.group("source_id").upper()
        for match in _SOURCE_ID_RE.finditer(str(answer or ""))
    }
    citations = _source_ids(
        _metadata_rows(payload.get("citation_docs")), require_version=True
    )
    graph_rows = _metadata_rows(payload.get("graph_evidence"))
    low_confidence = not graph_rows or any(
        (score := _confidence(row.get("graph_confidence"))) is None
        or score < _GRAPH_CONFIDENCE_THRESHOLD
        for row in graph_rows
    )
    graph_citation_count = len(rendered & _source_ids(graph_rows))
    full_answer = payload.get("answer_outcome") == "full_answer"
    citation_passed = (
        bool(rendered) and rendered <= citations if full_answer else not rendered
    )
    provenance_passed = all(
        _graph_provenance_complete(row) for row in graph_rows
    ) and (
        bool(graph_rows) and graph_citation_count > 0 if full_answer else True
    )
    return (
        full_answer,
        citation_passed,
        provenance_passed,
        len(graph_rows),
        len(rendered),
        graph_citation_count,
        low_confidence,
    )


def _request_safety(payload: Mapping[str, Any]) -> tuple[bool, bool]:
    validation = payload.get("pilot_request_validation")
    validation = validation if isinstance(validation, Mapping) else {}
    return (
        validation.get("access_scope_passed") is True,
        validation.get("leakage_passed") is True,
    )


def _declared_source_ids(value: object) -> set[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    normalized = tuple(str(item or "").strip().upper() for item in value)
    if not normalized or any(
        re.fullmatch(r"D[1-9]\d*P[1-9]\d*", item) is None
        for item in normalized
    ):
        return None
    return set(normalized)


def _query_answer_contract(
    diagnostics: Mapping[str, Any], answer: str
) -> tuple[bool, bool]:
    rendered = extract_source_ids(answer)
    available = _source_ids(
        _metadata_rows(diagnostics.get("citation_docs")),
        require_version=True,
    )
    full_answer_branches = tuple(
        branch
        for branch in _metadata_rows(diagnostics.get("decomposition_branches"))
        if branch.get("outcome") == "full_answer"
    )
    branch_citation_ids: set[str] = set()
    branch_rendered_ids: set[str] = set()
    branch_provenance_passed = bool(full_answer_branches)
    for branch in full_answer_branches:
        citations = _source_ids(
            _metadata_rows(branch.get("citations")), require_version=True
        )
        branch_rendered = _declared_source_ids(
            branch.get("rendered_source_ids")
        )
        branch_citation_ids.update(citations)
        branch_rendered_ids.update(branch_rendered or set())
        if branch_rendered is None:
            branch_provenance_passed = False
            continue
        branch_provenance_passed = branch_provenance_passed and all((
            branch_rendered <= citations,
            branch_rendered <= rendered,
        ))
    return (
        bool(rendered) and rendered <= available,
        branch_provenance_passed
        and rendered <= branch_citation_ids
        and rendered <= branch_rendered_ids,
    )


def graph_pilot_validation(
    diagnostics: Mapping[str, Any] | None,
    answer: str,
) -> GraphPilotValidation:
    """Validate a routed Graph answer without persisting private content."""
    payload = diagnostics if isinstance(diagnostics, Mapping) else {}
    (
        full_answer,
        citation_passed,
        provenance_passed,
        graph_evidence_count,
        rendered_citation_count,
        graph_citation_count,
        low_confidence,
    ) = _answer_contract(payload, answer)
    security_passed, leakage_passed = _request_safety(payload)
    passed = all(
        (
            security_passed,
            citation_passed,
            provenance_passed,
            leakage_passed,
        )
    )
    return GraphPilotValidation(
        graph_result_status=(
            "valid" if passed and full_answer else "invalid"
        ),
        security_passed=security_passed,
        citation_structure_passed=citation_passed,
        provenance_passed=provenance_passed,
        leakage_detected=not leakage_passed,
        graph_evidence_count=graph_evidence_count,
        rendered_citation_count=rendered_citation_count,
        graph_citation_count=graph_citation_count,
        full_answer=full_answer,
        low_confidence=low_confidence,
    )


def _approved_refusal_code(reason: object) -> str | None:
    normalized = str(reason or "").strip()
    return normalized if normalized in APPROVED_GRAPH_REFUSALS else None


def _refusal_template_valid(answer: str, reason: str | None) -> bool:
    return bool(
        reason == "evidence_gate"
        and any(pattern.fullmatch(str(answer or "")) for pattern in _REFUSAL_PATTERNS)
    )


def _deterministic_refusal(
    payload: Mapping[str, Any],
    validation: GraphPilotValidation,
    budget: Any,
    answer: str,
    completion_outcome: str,
    refusal_reason: str | None,
) -> bool:
    return all((
        not validation.full_answer,
        validation.security_passed,
        validation.citation_structure_passed,
        validation.provenance_passed,
        not validation.leakage_detected,
        payload.get("answer_outcome") == "insufficient_evidence",
        payload.get("evidence_stage") == "terminal",
        completion_outcome == "refused",
        _approved_refusal_code(refusal_reason) is not None,
        _refusal_template_valid(answer, refusal_reason),
        budget.final_generations == 0,
    ))


def _budget_fields(payload: Mapping[str, Any], budget: Any, latency: int):
    metrics = payload.get("generation_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    return {
        "final_latency_ms": max(0, int(latency)),
        "request_deadline_ms": max(
            0, int(budget.limits.deadline_seconds * 1000)
        ),
        "estimated_cost": _nonnegative_float(metrics.get("estimated_cost")),
        "provider_retries": budget.provider_retries,
        "final_generations": budget.final_generations,
    }


def graph_pilot_event_fields(
    diagnostics: Mapping[str, Any] | None,
    answer: str,
    budget: Any,
    *,
    final_latency_ms: int,
    completion_outcome: str,
    refusal_reason: str | None,
) -> dict[str, Any]:
    """Return the complete metadata-only event payload for a routed Graph request."""
    payload = diagnostics if isinstance(diagnostics, Mapping) else {}
    validation = graph_pilot_validation(payload, answer)
    safe_refusal = _deterministic_refusal(
        payload, validation, budget, answer, completion_outcome, refusal_reason
    )
    graph_max_hops = _positive_int(payload.get("graph_max_hops")) or 0
    within_budget = all((
        0 < budget.graph_edges <= budget.limits.graph_edges,
        0 < graph_max_hops <= 2,
        budget.provider_retries == 0,
        budget.final_generations <= budget.limits.final_generations,
        not budget.deadline_exceeded,
    ))
    result_status = "safe_refusal" if safe_refusal else validation.graph_result_status
    result_valid = result_status in {"valid", "safe_refusal"} and within_budget
    emitted_status = result_status if result_valid else "invalid"
    refusal_code = _approved_refusal_code(refusal_reason)
    return {
        "route": "graph_relational",
        "graph_result_status": emitted_status,
        "completion_outcome": completion_outcome,
        "refusal_reason_code": refusal_code,
        "refusal_template_passed": _refusal_template_valid(
            answer, refusal_reason
        ),
        "low_confidence": validation.low_confidence,
        "owner_review_required": (
            emitted_status != "valid" or validation.low_confidence
        ),
        "security_passed": validation.security_passed,
        "citation_structure_passed": validation.citation_structure_passed,
        "provenance_passed": validation.provenance_passed,
        "leakage_detected": validation.leakage_detected,
        "graph_edges": budget.graph_edges,
        "graph_max_hops": graph_max_hops,
        "graph_evidence_count": validation.graph_evidence_count,
        "rendered_citation_count": validation.rendered_citation_count,
        "graph_citation_count": validation.graph_citation_count,
        **_budget_fields(payload, budget, final_latency_ms),
    }


def calculation_pilot_event_fields(
    diagnostics: Mapping[str, Any],
    budget: Any,
    *,
    final_latency_ms: int,
) -> dict[str, Any]:
    metrics = diagnostics.get("generation_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    validation = diagnostics.get("pilot_request_validation")
    validation = validation if isinstance(validation, Mapping) else {}
    return {
        "route": "calculation",
        "calculation_result_status": (
            "valid" if metrics.get("calculation_result_status") == "valid"
            else "invalid"
        ),
        "security_passed": validation.get("access_scope_passed") is True,
        "citation_structure_passed": (
            validation.get("citation_structure_passed") is True
        ),
        "provenance_passed": validation.get("provenance_passed") is True,
        "leakage_detected": validation.get("leakage_passed") is not True,
        "calculations": budget.calculations,
        **_budget_fields(diagnostics, budget, final_latency_ms),
    }


def query_decomposition_pilot_event_fields(
    diagnostics: Mapping[str, Any],
    answer: str,
    budget: Any,
    *,
    final_latency_ms: int,
    completion_outcome: str,
    refusal_reason: str | None,
) -> dict[str, Any] | None:
    """Return metadata-only evidence for an actually decomposed request."""
    if not 2 <= budget.subqueries <= 3:
        return None
    try:
        usage = normalize_decomposition_usage(
            diagnostics.get("decomposition_usage")
        )
    except ValueError:
        usage = None
    validation = diagnostics.get("pilot_request_validation")
    validation = validation if isinstance(validation, Mapping) else {}
    branches = usage["branches"] if usage is not None else ()
    try:
        intent_count = int(diagnostics.get("decomposition_intent_count") or 0)
    except (OverflowError, TypeError, ValueError):
        intent_count = 0
    coverage = diagnostics.get("decomposition_intent_coverage")
    coverage_complete = bool(
        isinstance(coverage, Sequence)
        and not isinstance(coverage, (str, bytes))
        and len(coverage) == intent_count
        and all(value is True for value in coverage)
    )
    security_passed = validation.get("access_scope_passed") is True
    citation_passed, provenance_passed = _query_answer_contract(
        diagnostics, answer
    )
    leakage_detected = validation.get("leakage_passed") is not True
    execution_contract_passed = all((
        usage is not None,
        len(branches) == budget.subqueries == intent_count,
        len(_metadata_rows(diagnostics.get("decomposition_branches")))
        == budget.subqueries,
        coverage_complete,
        diagnostics.get("decomposition_intent_overflow") is False,
        budget.planners <= 1,
        budget.corrections <= 1,
        budget.provider_retries == 0,
        not budget.deadline_exceeded,
        security_passed,
        not leakage_detected,
    ))
    answered = all((
        execution_contract_passed,
        completion_outcome == "answered",
        budget.final_generations == 1,
        citation_passed,
        provenance_passed,
    ))
    safe_refusal = all((
        execution_contract_passed,
        diagnostics.get("answer_outcome") == "insufficient_evidence",
        diagnostics.get("evidence_stage") == "terminal",
        completion_outcome == "refused",
        _approved_refusal_code(refusal_reason) is not None,
        _refusal_template_valid(answer, refusal_reason),
        budget.final_generations == 0,
    ))
    result_status = (
        "valid" if answered else "safe_refusal" if safe_refusal else "invalid"
    )
    return {
        "route": "query_decomposition",
        "query_result_status": result_status,
        "completion_outcome": completion_outcome,
        "refusal_reason_code": _approved_refusal_code(refusal_reason),
        "refusal_template_passed": _refusal_template_valid(
            answer, refusal_reason
        ),
        "owner_review_required": result_status != "valid",
        "security_passed": security_passed,
        "citation_structure_passed": citation_passed,
        "provenance_passed": provenance_passed,
        "leakage_detected": leakage_detected,
        "planner_calls": budget.planners,
        "subquery_count": budget.subqueries,
        "correction_count": budget.corrections,
        "intent_count": intent_count,
        "intent_coverage_complete": coverage_complete,
        "deterministic_split_used": bool(
            diagnostics.get("decomposition_used_fallback")
        ),
        "intent_overflow": bool(
            diagnostics.get("decomposition_intent_overflow")
        ),
        **_budget_fields(diagnostics, budget, final_latency_ms),
    }


def pilot_request_event_fields(
    diagnostics: Mapping[str, Any],
    answer: str,
    budget: Any,
    *,
    final_latency_ms: int,
    completion_outcome: str,
    refusal_reason: str | None,
) -> dict[str, Any] | None:
    if budget.calculations > 0:
        return calculation_pilot_event_fields(
            diagnostics, budget, final_latency_ms=final_latency_ms
        )
    if diagnostics.get("graph_routed") is True:
        return graph_pilot_event_fields(
            diagnostics,
            answer,
            budget,
            final_latency_ms=final_latency_ms,
            completion_outcome=completion_outcome,
            refusal_reason=refusal_reason,
        )
    if budget.subqueries > 1:
        return query_decomposition_pilot_event_fields(
            diagnostics,
            answer,
            budget,
            final_latency_ms=final_latency_ms,
            completion_outcome=completion_outcome,
            refusal_reason=refusal_reason,
        )
    return None


__all__ = [
    "GraphPilotValidation",
    "calculation_pilot_event_fields",
    "graph_pilot_event_fields",
    "graph_pilot_validation",
    "pilot_request_event_fields",
    "query_decomposition_pilot_event_fields",
]
