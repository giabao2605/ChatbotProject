"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import os
from mech_chatbot.config.logging import logger
from mech_chatbot.config.settings import QDRANT_COLLECTION

__all__ = [
    '_get_qdrant_client',
    '_qdrant_client_singleton',
    'update_qdrant_metadata',
    'batch_update_qdrant_metadata',
]

_qdrant_client_singleton = None

def _get_qdrant_client():
    """QdrantClient nhẹ, KHÔNG nạp model RAG (torch/onnxruntime) vào tiến tr��nh hiện tại.
    Dùng cho thao tác admin (publish/reject/archive) gọi từ Streamlit để tránh crash native."""
    global _qdrant_client_singleton
    if _qdrant_client_singleton is None:
        from qdrant_client import QdrantClient
        _qdrant_client_singleton = QdrantClient(
            url=os.getenv("QDRANT_URL"),
            api_key=os.getenv("QDRANT_API_KEY"),
            timeout=120,
        )
    return _qdrant_client_singleton

def update_qdrant_metadata(doc_id, metadata_updates, require_points=False):
    """Cap nhat payload metadata cho tat ca Qdrant points cua doc_id.

    Dung mot filter-based write voi ``key=metadata`` de Qdrant merge cac field
    nested trong mot operation. Cach nay tranh trang thai nua point da cap nhat,
    nua point chua cap nhat khi publish/unpublish.
    """
    from qdrant_client import models
    client = _get_qdrant_client()
    try:
        doc_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.doc_id",
                    match=models.MatchValue(value=doc_id),
                )
            ]
        )
        count_result = client.count(
            collection_name=QDRANT_COLLECTION,
            count_filter=doc_filter,
            exact=True,
        )
        total_updated = int(getattr(count_result, "count", 0) or 0)
        if total_updated <= 0:
            logger.warning(
                f"update_qdrant_metadata: khong co Qdrant points cho DocID {doc_id}. "
                "Tai lieu co the chua embed hoac da bi xoa truoc do."
            )
            # Cac flow metadata thong thuong giu tuong thich nguoc. Rieng publish
            # phai fail-closed: khong duoc danh dau SQL la published khi khong co
            # staging points de kich hoat trong Qdrant.
            return not require_points

        client.set_payload(
            collection_name=QDRANT_COLLECTION,
            payload=dict(metadata_updates or {}),
            key="metadata",
            points=doc_filter,
            wait=True,
            ordering=models.WriteOrdering.STRONG,
        )
        logger.info(f"Updated Qdrant payload cho {total_updated} chunks cua DocID {doc_id}")
        return True
    except Exception as e:
        logger.error(f"Loi update Qdrant payload cho DocID {doc_id}: {e}", exc_info=True)
        return False


def batch_update_qdrant_metadata(updates_by_doc_id, require_points=False):
    """Apply all document metadata changes in one strongly-ordered Qdrant batch.

    Version publication needs opposite visibility mutations for old and new
    documents. Sending them through ``batch_update_points`` avoids a sequence
    of independent network writes that could expose a prolonged mixed state.
    """
    from qdrant_client import models

    normalized = {}
    for raw_doc_id, raw_updates in (updates_by_doc_id or {}).items():
        try:
            doc_id = int(raw_doc_id)
        except (TypeError, ValueError):
            return False
        if raw_updates:
            normalized[doc_id] = dict(raw_updates)
    if not normalized:
        return True

    client = _get_qdrant_client()
    try:
        operations = []
        for doc_id, metadata_updates in normalized.items():
            doc_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.doc_id",
                        match=models.MatchValue(value=doc_id),
                    )
                ]
            )
            count_result = client.count(
                collection_name=QDRANT_COLLECTION,
                count_filter=doc_filter,
                exact=True,
            )
            point_count = int(getattr(count_result, "count", 0) or 0)
            if point_count <= 0:
                logger.warning("batch_update_qdrant_metadata: khong co points cho DocID %s", doc_id)
                if require_points:
                    return False
                continue
            operations.append(
                models.SetPayloadOperation(
                    set_payload=models.SetPayload(
                        payload=metadata_updates,
                        key="metadata",
                        filter=doc_filter,
                    )
                )
            )

        if not operations:
            return not require_points
        client.batch_update_points(
            collection_name=QDRANT_COLLECTION,
            update_operations=operations,
            wait=True,
            ordering=models.WriteOrdering.STRONG,
        )
        logger.info("Batch updated Qdrant payload for %s document(s)", len(operations))
        return True
    except Exception as exc:
        logger.error("Loi batch update Qdrant payload: %s", exc, exc_info=True)
        return False
