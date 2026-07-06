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

def update_qdrant_metadata(doc_id, metadata_updates):
    """Cap nhat payload metadata cho tat ca Qdrant points cua doc_id.

    Dung cursor pagination thay vi limit=10000 co dinh de xu ly tai lieu
    co so luong chunk tuy y (> 10k trang).
    """
    from qdrant_client import models
    client = _get_qdrant_client()
    BATCH = 500   # so points lay moi lan scroll
    try:
        total_updated = 0
        next_offset = None
        found_any = False

        while True:
            scroll_res = client.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id))]
                ),
                limit=BATCH,
                offset=next_offset,
                with_payload=True,
            )
            points, next_offset = scroll_res

            if not points:
                break  # het du lieu hoac khong co points nao

            found_any = True
            ids_to_update = []
            for p in points:
                meta = p.payload.get("metadata", {}) if p.payload else {}
                meta.update(metadata_updates)
                # Batch set_payload theo tung point (Qdrant chua ho tro batch update payload)
                client.set_payload(
                    collection_name=QDRANT_COLLECTION,
                    payload={"metadata": meta},
                    points=[p.id],
                )
                ids_to_update.append(p.id)

            total_updated += len(ids_to_update)

            if next_offset is None:
                break  # het trang

        if not found_any:
            logger.warning(
                f"update_qdrant_metadata: khong co Qdrant points cho DocID {doc_id}. "
                "Tai lieu co the chua embed hoac da bi xoa truoc do."
            )
            return True  # giu True de khong lam gay publish/reject flow

        logger.info(f"Updated Qdrant payload cho {total_updated} chunks cua DocID {doc_id}")
        return True
    except Exception as e:
        logger.error(f"Loi update Qdrant payload cho DocID {doc_id}: {e}", exc_info=True)
        return False
