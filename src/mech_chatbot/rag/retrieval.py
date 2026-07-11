# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

from mech_chatbot.config.logging import logger, log_trace
from qdrant_client import QdrantClient, models
from mech_chatbot.rag.rbac import (
    compose_retrieval_filters,
    create_rbac_filter,
    _security_filter,
    _site_filter,
    _allowed_levels,
    LEVEL_ORDER,
)

def current_published_filter(rbac_filter=None):
    must = [
        models.FieldCondition(
            key="metadata.servable",
            match=models.MatchValue(value=True)
        ),
        models.FieldCondition(
            key="metadata.publication_state",
            match=models.MatchValue(value="published")
        ),
        models.FieldCondition(
            key="metadata.lifecycle_status",
            match=models.MatchValue(value="published")
        ),
        models.FieldCondition(
            key="metadata.review_status",
            match=models.MatchValue(value="approved")
        ),
        models.FieldCondition(
            key="metadata.is_current",
            match=models.MatchValue(value=True)
        ),
    ]

    if rbac_filter:
        must.append(rbac_filter)

    # P0#4: LOAI TRU tai lieu het hieu luc. Dung must_not (blacklist) de tai lieu CU
    # thieu metadata.effective_status VAN qua (tuong thich nguoc); chi loai cac trang thai xau.
    must_not = [
        models.FieldCondition(
            key="metadata.effective_status",
            match=models.MatchAny(any=["expired", "superseded", "draft"]),
        ),
    ]

    return models.Filter(must=must, must_not=must_not)


def probe_restricted_access(query_text, user_department=None, allowed_departments=None,
                            max_security_level="public", allowed_sites=None):
    """P0-2: Kiem tra co ton tai tai lieu KHOP pham vi phong ban cua user nhung bi CHAN
    CHI vi muc mat cao hon clearance. Tra ve (exists: bool, needed_level: str|None).
    Best-effort, stateless; loi -> (False, None) de khong pha luong RAG.
    """
    try:
        # Lazy import de module filter van thuan va unit test khong tai model.
        from mech_chatbot.rag.bootstrap import vectorstore

        user_order = LEVEL_ORDER.get((max_security_level or "public"), 0)
        allowed = list(allowed_departments) if allowed_departments else []
        if user_department and user_department not in allowed:
            allowed.append(user_department)
        from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT as _SHARE
        if _SHARE not in allowed:
            allowed.append(_SHARE)
        must = [
            models.FieldCondition(key="metadata.servable", match=models.MatchValue(value=True)),
            models.FieldCondition(key="metadata.publication_state", match=models.MatchValue(value="published")),
            models.FieldCondition(key="metadata.lifecycle_status", match=models.MatchValue(value="published")),
            models.FieldCondition(key="metadata.review_status", match=models.MatchValue(value="approved")),
            models.FieldCondition(key="metadata.is_current", match=models.MatchValue(value=True)),
            models.FieldCondition(key="metadata.phong_ban_quyen", match=models.MatchAny(any=allowed)),
        ]
        site_cond = _site_filter(allowed_sites)
        if site_cond is not None:
            must.append(site_cond)
        probe_filter = models.Filter(must=must)
        retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 10, "filter": probe_filter})
        docs = retriever.invoke(query_text)
        levels_above = []
        for d in docs:
            lvl = (d.metadata or {}).get("security_level") or "confidential"
            if LEVEL_ORDER.get(lvl, 2) > user_order:
                levels_above.append(lvl)
        if levels_above:
            needed = min(levels_above, key=lambda l: LEVEL_ORDER.get(l, 2))
            return True, needed
        return False, None
    except Exception as e:
        logger.warning(f"probe_restricted_access loi: {e}")
        return False, None

__all__ = [
    'current_published_filter',
    'probe_restricted_access',
]
