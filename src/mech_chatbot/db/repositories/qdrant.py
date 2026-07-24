"""Qdrant metadata operations with explicitly supplied runtime dependencies."""

from __future__ import annotations

from typing import Any

from mech_chatbot.config.logging import logger

__all__ = [
    "batch_update_qdrant_metadata",
    "update_qdrant_metadata",
]


def _require_runtime(qdrant_client: Any, collection_name: str | None) -> tuple[Any, str]:
    if qdrant_client is None or not str(collection_name or "").strip():
        from mech_chatbot.config.repository_runtime import current_qdrant_runtime

        bound_client, bound_collection = current_qdrant_runtime()
        qdrant_client = qdrant_client or bound_client
        collection_name = collection_name or bound_collection
    collection = str(collection_name or "").strip()
    if qdrant_client is None or not collection:
        raise RuntimeError(
            "Qdrant metadata runtime is not configured. "
            "Pass qdrant_client and collection_name from the composition root."
        )
    return qdrant_client, collection


def update_qdrant_metadata(
    doc_id,
    metadata_updates,
    require_points=False,
    *,
    qdrant_client=None,
    collection_name=None,
):
    """Update nested metadata for all points belonging to one document."""

    from qdrant_client import models

    try:
        client, collection = _require_runtime(qdrant_client, collection_name)
        doc_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.doc_id",
                    match=models.MatchValue(value=doc_id),
                )
            ]
        )
        count_result = client.count(
            collection_name=collection,
            count_filter=doc_filter,
            exact=True,
        )
        total_updated = int(getattr(count_result, "count", 0) or 0)
        if total_updated <= 0:
            logger.warning(
                "update_qdrant_metadata: khong co Qdrant points cho DocID %s.",
                doc_id,
            )
            return not require_points

        client.set_payload(
            collection_name=collection,
            payload=dict(metadata_updates or {}),
            key="metadata",
            points=doc_filter,
            wait=True,
            ordering=models.WriteOrdering.STRONG,
        )
        logger.info(
            "Updated Qdrant payload cho %s chunks cua DocID %s",
            total_updated,
            doc_id,
        )
        return True
    except Exception as error:
        logger.error(
            "Loi update Qdrant payload cho DocID %s: %s",
            doc_id,
            error,
            exc_info=True,
        )
        return False


def batch_update_qdrant_metadata(
    updates_by_doc_id,
    require_points=False,
    *,
    qdrant_client=None,
    collection_name=None,
):
    """Apply document metadata changes in one strongly ordered Qdrant batch."""

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

    try:
        client, collection = _require_runtime(qdrant_client, collection_name)
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
                collection_name=collection,
                count_filter=doc_filter,
                exact=True,
            )
            point_count = int(getattr(count_result, "count", 0) or 0)
            if point_count <= 0:
                logger.warning(
                    "batch_update_qdrant_metadata: khong co points cho DocID %s",
                    doc_id,
                )
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
            collection_name=collection,
            update_operations=operations,
            wait=True,
            ordering=models.WriteOrdering.STRONG,
        )
        logger.info(
            "Batch updated Qdrant payload for %s document(s)",
            len(operations),
        )
        return True
    except Exception as error:
        logger.error(
            "Loi batch update Qdrant payload: %s",
            error,
            exc_info=True,
        )
        return False
