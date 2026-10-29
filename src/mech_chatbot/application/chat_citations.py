"""Application-owned citation attribution and browser reference text rules."""

from __future__ import annotations

import os
import re
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def citation_source_ids(answer: str) -> frozenset[str]:
    source_ids = re.findall(
        r"source[_\s-]?id\s*[:#]?\s*(D\d+(?:P\d+|L[a-f0-9]{16}))(?![a-z0-9])",
        str(answer or ''), flags=re.IGNORECASE)
    source_ids += re.findall(
        r"\[SRC:\s*(D\d+(?:P\d+|L[a-f0-9]{16}))(?![a-z0-9])\]",
        str(answer or ''), flags=re.IGNORECASE)
    return frozenset(value.upper() for value in source_ids)


def structured_citation(item: Mapping[str, Any], *, page_has_vision: Callable[[int, int], bool]) -> dict[str, Any] | None:
    locator = item.get('locator')
    if not isinstance(locator, Mapping):
        return None
    doc, version = _safe_int(locator.get('doc_id')), _safe_int(locator.get('version'))
    page = _safe_int(locator.get('page'))
    snapshot, source_hash = item.get('snapshot_id'), item.get('source_hash')
    if (doc is None or doc <= 0 or version is None or version <= 0
            or _safe_int(item.get('doc_id')) != doc or _safe_int(item.get('version_no')) != version
            or not isinstance(snapshot, str) or not 1 <= len(snapshot) <= 128
            or not isinstance(source_hash, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', source_hash)
            or (locator.get('page') is not None and (page is None or page <= 0))
            or not (page or locator.get('sheet') or locator.get('cell') or locator.get('row_key'))):
        return None
    normalized = {key: locator.get(key) for key in ('doc_id', 'version', 'page', 'sheet', 'cell', 'row_key', 'table_index', 'bbox')}
    scope_id = item.get('scope_id')
    if scope_id is not None and (not isinstance(scope_id, str) or not 1 <= len(scope_id) <= 255):
        return None
    scope_hash = item.get('scope_hash')
    if scope_hash is not None and (not isinstance(scope_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',scope_hash)):
        return None
    graph_context=item.get('graph_context')
    graph_hash=item.get('graph_hash')
    if graph_context is not None:
        from mech_chatbot.domain.graph_query import GraphQuery
        try:
            GraphQuery(**dict(graph_context))
        except (ValueError,TypeError):
            return None
        if scope_id is not None or not isinstance(graph_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',graph_hash):
            return None
    identity_values=(normalized,snapshot,source_hash,scope_id,scope_hash)
    if graph_context is not None:
        identity_values=(*identity_values,graph_context,graph_hash)
    identity = json.dumps(identity_values, sort_keys=True, ensure_ascii=False)
    source_id = f"D{doc}L{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}".upper()
    image_hash=item.get('image_hash')
    if image_hash is not None and (not isinstance(image_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',image_hash)):
        return None
    vision = bool(page and ((image_hash and (scope_hash or graph_context)) or page_has_vision(doc, page)))
    card = {'citation_schema': 'source-locator-v2', 'doc_id': doc, 'page_no': page,
            'file_name': item.get('file_goc') or item.get('file_name'), 'version_no': version,
            'score': item.get('score'), 'source_id': source_id, 'locator': normalized,
            'snapshot_id': snapshot, 'source_hash': source_hash, 'has_vision': vision,
            'image_hash':image_hash,
            'page_url': f'/api/files/documents/{doc}/pages/{page}' if vision else None,
            'original_url': f'/api/files/documents/{doc}/original'}
    if scope_id is not None:
        from urllib.parse import quote,urlencode
        card = {**card, 'scope_id': scope_id, 'scope_hash':scope_hash, 'fact_count': item.get('fact_count'),
                'facts_url': f'/api/rag-evidence/approved-scopes/{quote(scope_id, safe="")}/facts'}
        if scope_hash:
            query=urlencode({'version_no':version,'snapshot_id':snapshot,'source_hash':source_hash,
                             'scope_id':scope_id,'scope_hash':scope_hash})
            card={**card,'original_url':card['original_url']+'?'+query,
                  'has_vision':bool(vision and image_hash),
                  'page_url':card['page_url']+'?'+query+'&image_hash='+image_hash if vision and image_hash else None}
    if graph_context is not None:
        from urllib.parse import urlencode
        query=urlencode({'version_no':version,'snapshot_id':snapshot,'source_hash':source_hash,
                         'graph_context':json.dumps(dict(graph_context),sort_keys=True,separators=(',',':')),'graph_hash':graph_hash})
        card={**card,'graph_context':dict(graph_context),'graph_hash':graph_hash,
              'original_url':card['original_url']+'?'+query,'has_vision':bool(vision and image_hash),
              'page_url':card['page_url']+'?'+query+'&image_hash='+image_hash if vision and image_hash else None}
    if not (scope_id and scope_hash) and graph_context is None:
        suffix = f'?citation_version={version}'
        card = {**card, 'original_url': card['original_url'] + suffix,
                'page_url': card['page_url'] + suffix if card['page_url'] else None}
    return card


def build_citation_list(
    retrieved_docs: Iterable[Any],
    *,
    page_has_vision: Callable[[int, int], bool],
) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    citations: list[dict[str, Any]] = []
    for item in retrieved_docs:
        if not isinstance(item, Mapping):
            continue
        if 'locator' in item:
            citation = structured_citation(item, page_has_vision=page_has_vision)
            if citation and citation['source_id'] not in seen:
                seen.add(citation['source_id'])
                citations.append(citation)
            continue
        doc_id = _safe_int(item.get("doc_id"))
        page_no = _safe_int(item.get("trang") or item.get("trang_so") or item.get("page_no"))
        raw_version = item.get("version_no") if item.get("version_no") is not None else item.get("version")
        version = _safe_int(raw_version) if type(raw_version) in (int, str) else None
        if doc_id is None or page_no is None or version is None or version < 1:
            continue
        key = (doc_id, page_no)
        if key in seen:
            continue
        seen.add(key)
        has_vision = page_has_vision(doc_id, page_no)
        citation = {
            "doc_id": doc_id,
            "page_no": page_no,
            "file_name": item.get("file_goc") or item.get("file_name"),
            "score": item.get("score"),
            "source_id": item.get("source_id") or f"D{doc_id}P{page_no}",
            "has_vision": has_vision,
            "page_url": (
                f"/api/files/documents/{doc_id}/pages/{page_no}?citation_version={version}"
                if has_vision
                else None
            ),
            "original_url": f"/api/files/documents/{doc_id}/original?citation_version={version}",
            "version_no": version,
        }
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
    cited_source_ids = citation_source_ids(answer)
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
        if citation.get('citation_schema') == 'source-locator-v2':
            continue
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
        locator = citation.get('locator')
        location = ' · '.join(f'{label} {locator[key]}' for key, label in
                             (('page', 'Trang'), ('sheet', 'Sheet'), ('cell', 'Ô'), ('row_key', 'Dòng'))
                             if locator.get(key)) if isinstance(locator, Mapping) else f'Trang {page}'
        lines.append(f"- **{name}** ({location})")
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
    ) or build_citation_list(streamed_candidates, page_has_vision=page_has_vision)
    citations = filter_citations_by_answer(citations, answer)
    return tuple(citations), citation_ref_text(citations)


def attributed_rag_citations(diagnostics: Mapping[str, Any] | None, answer: str) -> tuple[Mapping[str, Any], ...]:
    source_ids = citation_source_ids(answer)
    result, seen = [], set()
    for item in (diagnostics or {}).get('citation_docs') or []:
        if not isinstance(item, Mapping):
            continue
        if 'locator' in item:
            card = structured_citation(item, page_has_vision=lambda *_: False)
        else:
            doc_id = _safe_int(item.get('doc_id'))
            page = _safe_int(item.get('trang') or item.get('trang_so') or item.get('page_no'))
            if doc_id is None or page is None:
                continue
            card = {'doc_id': doc_id, 'page_no': page,
                    'file_name': item.get('file_goc') or item.get('file_name'),
                    'file_goc': item.get('file_goc') or item.get('file_name'),
                    'version_no': item.get('version_no'), 'score': item.get('score'),
                    'trang': page, 'source_id': str(item.get('source_id') or f'D{doc_id}P{page}').upper()}
        if card and card['source_id'] in source_ids and card['source_id'] not in seen:
            seen.add(card['source_id'])
            result.append(card)
    return tuple(result)


__all__ = [
    "build_citation_list",
    "citation_ref_text",
    "filter_citations_by_answer",
    "resolve_chat_citations",
]
