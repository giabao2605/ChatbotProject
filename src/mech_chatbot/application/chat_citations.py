"""Application-owned citation attribution and browser reference text rules."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_citation_list(
    retrieved_docs: Iterable[Any],
    *,
    page_has_vision: Callable[[int, int], bool],
) -> list[dict[str, Any]]:
    seen: set[tuple[int, int]] = set()
    citations: list[dict[str, Any]] = []
    for item in retrieved_docs:
        if not isinstance(item, Mapping):
            continue
        doc_id = _safe_int(item.get("doc_id"))
        page_no = _safe_int(item.get("trang") or item.get("trang_so"))
        if doc_id is None or page_no is None:
            continue
        key = (doc_id, page_no)
        if key in seen:
            continue
        seen.add(key)
        has_vision = page_has_vision(doc_id, page_no)
        citation = {
            "doc_id": doc_id,
            "page_no": page_no,
            "file_name": item.get("file_goc"),
            "score": item.get("score"),
            "source_id": item.get("source_id") or f"D{doc_id}P{page_no}",
            "has_vision": has_vision,
            "page_url": (
                f"/api/files/documents/{doc_id}/pages/{page_no}"
                if has_vision
                else None
            ),
            "original_url": f"/api/files/documents/{doc_id}/original",
        }
        version_no = item.get("version_no")
        if version_no is None:
            version_no = item.get("version")
        if version_no not in (None, ""):
            citation["version_no"] = version_no
        citations.append(citation)
    return citations


def filter_citations_by_answer(
    citations: Iterable[Mapping[str, Any]],
    answer: str,
    *,
    allow_legacy_fallback: bool = False,
) -> list[dict[str, Any]]:
    """Keep sources explicitly referenced by the generated answer."""

    citation_list = [dict(citation) for citation in citations or []]
    text_answer = str(answer or "").lower().replace("\\", "/")
    cited_source_ids = {
        value.upper()
        for value in re.findall(
            r"(?:source[_ ]?id\s*[:#]?\s*|\[SRC:)(D\d+P\d+)",
            text_answer,
            flags=re.IGNORECASE,
        )
    }
    if cited_source_ids:
        return [
            citation
            for citation in citation_list
            if str(citation.get("source_id") or "").upper() in cited_source_ids
        ]
    if not allow_legacy_fallback:
        return []

    cited_doc_ids = {
        int(value)
        for value in re.findall(
            r"\bdocid\s*[:#]?\s*(\d+)\b",
            text_answer,
            flags=re.IGNORECASE,
        )
    }
    matched = []
    for citation in citation_list:
        file_name = (
            str(citation.get("file_name") or "").strip().lower().replace("\\", "/")
        )
        base_name = os.path.basename(file_name)
        doc_id = _safe_int(citation.get("doc_id"))
        if (
            (base_name and base_name in text_answer)
            or (file_name and file_name in text_answer)
            or (doc_id is not None and doc_id in cited_doc_ids)
        ):
            matched.append(citation)
    return matched


def citation_ref_text(citations: Iterable[Mapping[str, Any]]) -> str:
    citation_list = [dict(citation) for citation in citations or []]
    if not citation_list:
        return ""
    lines = []
    for citation in citation_list:
        name = citation.get("file_name") or f"Doc {citation.get('doc_id')}"
        page = citation.get("page_no") or "?"
        lines.append(f"- **{name}** (Trang {page})")
    return "\n\n---\n**Nguồn tham chiếu:**\n" + "\n".join(lines)


def resolve_chat_citations(
    citation_docs: Iterable[Mapping[str, Any]],
    streamed_candidates: Iterable[Mapping[str, Any]],
    answer: str,
    *,
    page_has_vision: Callable[[int, int], bool],
) -> tuple[tuple[dict[str, Any], ...], str]:
    citations = build_citation_list(
        citation_docs,
        page_has_vision=page_has_vision,
    ) or [dict(item) for item in streamed_candidates]
    citations = filter_citations_by_answer(citations, answer)
    return tuple(citations), citation_ref_text(citations)


__all__ = [
    "build_citation_list",
    "citation_ref_text",
    "filter_citations_by_answer",
    "resolve_chat_citations",
]
