"""Governed BOM and image helpers for retrieval enrichment."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any, Protocol

from langchain_core.documents import Document

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.rag.execution import RequestBudgetExceeded


class _EnrichmentContextLike(Protocol):
    decision: Any
    trace_id: str
    user_question: str
    user_roles: tuple[str, ...]
    allowed_departments: tuple[str, ...]
    allowed_sites: tuple[str, ...]


def _bom_document_ids(
    documents: Sequence[Any], user_question: str, grounded_math_enabled: bool
) -> list[int]:
    if not grounded_math_enabled:
        return []
    from mech_chatbot.rag.grounded_math import select_grounded_bom_document_ids

    return select_grounded_bom_document_ids(documents, user_question)


def _search_bom_rows(
    context: _EnrichmentContextLike,
    part_ids: Sequence[str],
    document_ids: Sequence[int],
    search_bom_facts: Callable[..., Sequence[Any]],
) -> Sequence[Any]:
    request = context.decision.request

    def find(codes: Sequence[str]) -> Sequence[Any]:
        return search_bom_facts(
            part_codes=list(codes), document_ids=list(document_ids),
            version_policy=context.decision.intent_data.get("version_policy", "current_only"),
            detected_versions=context.decision.intent_data.get("detected_versions"),
            user_department=request.user_department, user_roles=list(context.user_roles),
            allowed_departments=list(context.allowed_departments),
            max_security_level=request.max_security_level,
            allowed_sites=list(context.allowed_sites),
        )

    rows = find(part_ids)
    if rows or not (part_ids and document_ids):
        return rows
    return find(())


def _group_bom_rows(
    rows: Sequence[Any], grounded_math_enabled: bool
) -> dict[tuple[int, int], dict[str, Any]]:
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for row in rows:
        try:
            source_key = (int(row.doc_id), int(row.page))
        except (TypeError, ValueError):
            continue
        source = grouped.setdefault(source_key, {
            "file_goc": row.document, "version_no": row.version,
            "security_level": row.security_level, "site": row.site,
            "external_processing_policy": row.external_processing_policy,
            "lines": [], "facts": [],
        })
        source["lines"].append(
            f"- Mã: {row.part_code}, Tên: {row.description}, "
            f"Vật liệu: {row.material}, SL: {row.quantity} "
            f"{row.unit or ''}, Ghi chú: {row.note}"
        )
        fact = _make_grounded_fact(row) if grounded_math_enabled else None
        if fact is not None:
            source["facts"].append(fact)
    return grouped


def _make_grounded_fact(row: Any) -> Any | None:
    if row.quantity is None:
        return None
    from mech_chatbot.rag.grounded_math import GroundedFact

    try:
        return GroundedFact(
            value=Decimal(str(row.quantity)), unit=str(row.unit or "").strip(),
            doc_id=int(row.doc_id), page=int(row.page), version=int(row.version),
            source_id=str(row.source_row_id), label=str(row.part_code or "").strip(),
        )
    except (ValueError, TypeError, ArithmeticError):
        return None


def _calculation_claims(
    grouped: Mapping[tuple[int, int], Mapping[str, Any]],
    user_question: str,
    grounded_math_enabled: bool,
) -> dict[tuple[int, int], tuple[Any, Any]]:
    if not grounded_math_enabled:
        return {}
    from mech_chatbot.rag.grounded_math import solve_grounded_calculation

    facts_by_document: dict[tuple[int, int], list[Any]] = {}
    for (document_id, _), source in grouped.items():
        claim_key = (document_id, int(source["version_no"]))
        facts_by_document.setdefault(claim_key, []).extend(source["facts"])
    all_facts = tuple(
        fact for facts in facts_by_document.values() for fact in facts
    )
    calculation = solve_grounded_calculation(user_question, all_facts)
    if calculation.plan is None or not facts_by_document:
        return {}
    operand_keys = tuple(dict.fromkeys(
        (fact.doc_id, fact.version) for fact in calculation.plan.operands
    ))
    claim_key = operand_keys[0] if operand_keys else next(iter(facts_by_document))
    return {claim_key: (calculation.plan, calculation.claim)}


def _append_calculation(
    text: str, plan: Any, claim: Any
) -> tuple[str, Mapping[str, Any]]:
    from mech_chatbot.rag.grounded_math import make_calculation_provenance

    provenance = make_calculation_provenance(plan, claim)
    if claim.status == "valid":
        qualifier = "xấp xỉ " if claim.approximate else ""
        text += (
            f"\n- Kết quả tính xác định từ các dòng BOM trên: {qualifier}"
            f"{claim.display_value} {claim.unit}. Công thức: {claim.formula}."
        )
    else:
        text += (
            "\n- Tôi trả lời được một phần: không thể hoàn thành phép tính có kiểm soát "
            f"vì dữ kiện không hợp lệ ({claim.status}); không tự suy diễn."
        )
    return text, provenance


def _build_bom_documents(
    grouped: Mapping[tuple[int, int], Mapping[str, Any]],
    claims: Mapping[tuple[int, int], tuple[Any, Any]],
) -> list[Document]:
    documents: list[Document] = []
    emitted_calculations: set[tuple[int, int]] = set()
    for (document_id, page_number), source in grouped.items():
        text = (
            "Dữ liệu cấu trúc Bảng Kê Vật Tư (BOM) đã trích xuất "
            "từ đúng trang tài liệu:\n" + "\n".join(source["lines"])
        )
        provenance = None
        claim_key = (document_id, int(source["version_no"]))
        if claim_key in claims and claim_key not in emitted_calculations:
            emitted_calculations.add(claim_key)
            text, provenance = _append_calculation(text, *claims[claim_key])
        documents.append(Document(page_content=text, metadata={
            "doc_id": document_id, "trang_so": page_number,
            "file_goc": source["file_goc"], "version_no": source["version_no"],
            "security_level": source["security_level"], "site": source["site"],
            "domain": "mechanical", "loai_du_lieu": "sql_bom",
            "doc_status": "published",
            "external_processing_policy": (
                source["external_processing_policy"] or "internal_only"
            ),
            "calculation_provenance": provenance,
        }))
    return documents


def inject_bom(
    context: _EnrichmentContextLike,
    documents: Sequence[Any],
    part_ids: Sequence[str],
    *,
    env_bool: Callable[[str, bool], bool],
    context_is_mechanical: Callable[[Sequence[Any], Sequence[str]], bool],
    search_bom_facts: Callable[..., Sequence[Any]],
    lookup_documents: Sequence[Any] | None = None,
) -> tuple[tuple[Any, ...], bool]:
    grounded_math_enabled = env_bool("RAG_GROUNDED_MATH_ENABLED", False)
    document_ids = _bom_document_ids(
        documents if lookup_documents is None else lookup_documents,
        context.user_question,
        grounded_math_enabled,
    )
    if not (part_ids or document_ids) or not context_is_mechanical(documents, part_ids):
        return tuple(documents), grounded_math_enabled
    started_at = time.time()
    try:
        rows = _search_bom_rows(
            context, part_ids, document_ids, search_bom_facts
        )
        if not rows:
            _log_bom(context, started_at, 0, part_ids, document_ids)
            return tuple(documents), grounded_math_enabled
        grouped = _group_bom_rows(rows, grounded_math_enabled)
        claims = _calculation_claims(
            grouped, context.user_question, grounded_math_enabled
        )
        bom_documents = _build_bom_documents(grouped, claims)
        logger.info(
            "Da them %s dong BOM tu SQL vao context (%s nguon co the citation).",
            len(rows), len(bom_documents),
        )
        _log_bom(context, started_at, len(rows), part_ids, document_ids)
        return tuple(bom_documents) + tuple(documents), grounded_math_enabled
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as exc:
        logger.error("Loi inject SQL BOM: %s", exc)
        log_trace(
            "sql_bom", context.trace_id,
            latency_ms=int((time.time() - started_at) * 1000), error=str(exc),
            part_ids=list(part_ids), document_ids=list(document_ids),
        )
        return tuple(documents), grounded_math_enabled


def _log_bom(
    context: _EnrichmentContextLike,
    started_at: float,
    row_count: int,
    part_ids: Sequence[str],
    document_ids: Sequence[int],
) -> None:
    log_trace(
        "sql_bom", context.trace_id,
        latency_ms=int((time.time() - started_at) * 1000), rows=row_count,
        part_ids=list(part_ids), document_ids=list(document_ids),
    )


def prepend_image(
    documents: Sequence[Any], image_analysis: str
) -> tuple[Any, ...]:
    if not image_analysis:
        return tuple(documents)
    image_document = Document(
        page_content=f"Phan tich noi dung anh nguoi dung tai len: {image_analysis}",
        metadata={
            "file_goc": "Anh dinh kem tu nguoi dung",
            "loai_du_lieu": "image_summary", "trang_so": "1",
            "cong_doan": "Anh truc tiep",
        },
    )
    return (image_document, *documents)


__all__ = ["inject_bom", "prepend_image"]
