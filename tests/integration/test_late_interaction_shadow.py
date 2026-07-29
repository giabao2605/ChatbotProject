import os
import uuid
from functools import partial

import pytest
from dotenv import load_dotenv
from qdrant_client import QdrantClient

from mech_chatbot.config.settings import Settings
from mech_chatbot.rag.late_interaction import (
    LateInteractionConfig,
    attempt_shadow_rerank,
    build_encoder,
    candidate_key,
    encode_query,
)
from scripts.late_interaction.backfill_shadow import _document, backfill


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_LATE_INTERACTION_TESTS") != "1",
    reason="set RUN_LATE_INTERACTION_TESTS=1 in the isolated encoder environment",
)
def test_qdrant_shadow_backfill_maxsim_is_idempotent_and_preserves_governance():
    load_dotenv()
    settings = Settings.from_env()
    client = QdrantClient(
        url=os.environ["QDRANT_URL"],
        api_key=os.environ.get("QDRANT_API_KEY"),
        timeout=120,
    )
    source = os.getenv("QDRANT_COLLECTION", "TaiLieuKyThuat_v2")
    shadow = f"MechChatbot_LateInteraction_Test_{uuid.uuid4().hex[:12]}"
    index_version = "late-test-v1"
    late_config = LateInteractionConfig(
        interaction_enabled=True,
        encoder_ready=True,
        model_name=settings.RAG_LATE_MODEL,
        use_fp16=settings.EMBEDDING_DEVICE.lower().startswith("cuda"),
        query_max_length=settings.RAG_LATE_QUERY_MAX_LENGTH,
        document_max_length=settings.RAG_LATE_DOCUMENT_MAX_LENGTH,
        collection_name=shadow,
        index_version=index_version,
    )
    encoder = build_encoder(late_config)
    try:
        first = backfill(client, source, shadow, batch_size=32, index_version=index_version)
        shadow_candidates, _ = client.scroll(
            collection_name=shadow,
            limit=2,
            with_payload=True,
            with_vectors=False,
        )
        source_points = client.retrieve(
            collection_name=source,
            ids=[point.payload["source_point_id"] for point in shadow_candidates],
            with_payload=True,
            with_vectors=False,
        )
        candidates = [_document(point) for point in source_points]
        result = attempt_shadow_rerank(
            candidates,
            candidates[0].page_content,
            client,
            collection_name=shadow,
            query_encoder=partial(
                encode_query,
                encoder=encoder,
                max_length=late_config.query_max_length,
            ),
            config=late_config,
        )
        second = backfill(client, source, shadow, batch_size=32, index_version=index_version)

        points, _ = client.scroll(
            collection_name=shadow,
            limit=256,
            with_payload=True,
            with_vectors=False,
        )
        assert first["coverage"] == 1.0
        assert result.used_shadow is True
        assert result.coverage == 1.0
        assert {candidate_key(item) for item in result.documents}.issubset(
            {candidate_key(item) for item in candidates}
        )
        assert second["uploaded"] == 0
        assert second["stale_reindexed"] == 0
        assert second["coverage"] == 1.0
        assert points
        assert all((point.payload or {}).get("department") for point in points)
        assert all((point.payload or {}).get("site") for point in points)
        assert all((point.payload or {}).get("security_level") for point in points)
        assert all((point.payload or {}).get("lifecycle_status") for point in points)
    finally:
        if client.collection_exists(shadow):
            client.delete_collection(shadow)
