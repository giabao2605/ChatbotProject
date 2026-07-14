# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

from mech_chatbot.config.logging import logger, log_trace
from qdrant_client import models
from mech_chatbot.config.settings import QDRANT_COLLECTION
from mech_chatbot.rag.rbac import (
    PART_ID_KEYS_BROAD,
    compose_retrieval_filters,
    create_rbac_filter,
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
                            max_security_level="public", allowed_sites=None, part_ids=None):
    """Detect an exact-code document blocked by security or site policy.

    The probe reads only security/site payload fields and never returns evidence.
    It returns ``(blocked, access_reason)`` and fails closed to ``(False, None)``
    on operational errors so it cannot break the primary RAG path.
    """
    try:
        # Lazy import de module filter van thuan va unit test khong tai model.
        from mech_chatbot.rag.bootstrap import client

        exact_ids = [str(value).strip().lower() for value in (part_ids or []) if str(value).strip()]
        if not exact_ids:
            return False, None

        user_order = LEVEL_ORDER.get((max_security_level or "public"), 0)
        allowed = list(allowed_departments) if allowed_departments else []
        if user_department and user_department not in allowed:
            allowed.append(user_department)
        from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT as _SHARE
        if _SHARE not in allowed:
            allowed.append(_SHARE)
        governance = current_published_filter()
        must = list(governance.must or ()) + [
            models.FieldCondition(key="metadata.phong_ban_quyen", match=models.MatchAny(any=allowed)),
            models.Filter(should=[
                models.FieldCondition(
                    key=key,
                    match=models.MatchAny(any=exact_ids),
                )
                for key in PART_ID_KEYS_BROAD
            ]),
        ]
        probe_filter = models.Filter(must=must, must_not=governance.must_not)
        points, _ = client.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=probe_filter,
            limit=10,
            with_payload=["metadata.security_level", "metadata.site"],
            with_vectors=False,
        )
        levels_above = []
        site_restricted = False
        normalized_sites = {
            str(site).strip().lower() for site in (allowed_sites or []) if str(site).strip()
        }
        for point in points:
            payload = getattr(point, "payload", {}) or {}
            metadata = payload.get("metadata") or {}
            lvl = metadata.get("security_level") or "confidential"
            if LEVEL_ORDER.get(lvl, 2) > user_order:
                levels_above.append(lvl)
            doc_site = str(metadata.get("site") or "").strip().lower()
            if not normalized_sites or doc_site not in normalized_sites:
                site_restricted = True
        if levels_above:
            needed = min(levels_above, key=lambda l: LEVEL_ORDER.get(l, 2))
            return True, needed
        if site_restricted:
            return True, "site_restricted"
        return False, None
    except Exception as e:
        logger.warning(f"probe_restricted_access loi: {e}")
        return False, None

__all__ = [
    'current_published_filter',
    'probe_restricted_access',
]
