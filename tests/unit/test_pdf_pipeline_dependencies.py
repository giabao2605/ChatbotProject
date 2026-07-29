from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion import file_ingestor
from mech_chatbot.ingestion.pdf import pipeline as public_pipeline
from mech_chatbot.ingestion.pdf import pipeline_implementation as pipeline
from mech_chatbot.ingestion.pdf.pipeline_dependencies import (
    CompatibilityIngestionResources,
    IngestionPipelineDependencies,
    build_compatibility_ingestion_resources,
)


pytestmark = pytest.mark.unit


class _VectorStore:
    def __init__(self) -> None:
        self.batches = []

    def add_documents(self, documents) -> None:
        self.batches.append(list(documents))


class _QdrantClient:
    def __init__(self) -> None:
        self.delete_calls = []

    def delete(self, **kwargs) -> None:
        self.delete_calls.append(kwargs)


def _dependencies() -> IngestionPipelineDependencies:
    return IngestionPipelineDependencies(
        vector_store=_VectorStore(),
        qdrant_client=_QdrantClient(),
        collection_name="technical-documents",
    )


def test_pipeline_dependency_bundle_is_frozen() -> None:
    dependencies = _dependencies()

    with pytest.raises(FrozenInstanceError):
        dependencies.collection_name = "other"  # type: ignore[misc]


def test_vector_helpers_use_only_explicit_dependencies() -> None:
    dependencies = _dependencies()

    pipeline._add_docs_with_retry(["chunk"], dependencies=dependencies)
    pipeline._delete_vectors_for_file(
        "drawing.pdf",
        "Welding",
        doc_id=41,
        dependencies=dependencies,
    )

    assert dependencies.vector_store.batches == [["chunk"]]
    assert len(dependencies.qdrant_client.delete_calls) == 2
    assert all(
        call["collection_name"] == "technical-documents"
        for call in dependencies.qdrant_client.delete_calls
    )


def test_pipeline_implementation_has_no_rag_or_vector_singleton() -> None:
    source = Path(pipeline.__file__).read_text(encoding="utf-8")

    assert "mech_chatbot.rag" not in source
    assert "_LazyRagAttr" not in source
    assert not hasattr(pipeline, "client")
    assert not hasattr(pipeline, "vectorstore")


def test_direct_pipeline_call_without_vector_dependencies_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="vector dependencies"):
        pipeline.process_and_ingest_file("manual.md", "manual.md", "quality")


def test_file_ingestor_forwards_vector_and_vision_dependencies(monkeypatch) -> None:
    dependencies = _dependencies()
    vision_model = object()
    calls = []
    monkeypatch.setattr(file_ingestor.os.path, "exists", lambda _: True)
    monkeypatch.setattr(
        file_ingestor.pipeline_implementation,
        "process_and_ingest_file",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or {"status": "success", "message": "done"},
    )

    result = file_ingestor.learn_new_file_typed(
        "manual.md",
        "manual.md",
        dependencies=dependencies,
        vision_model=vision_model,
    )

    assert result[0] is True
    assert calls[0][0][3] is vision_model
    assert calls[0][1]["dependencies"] is dependencies


def test_file_ingestor_does_not_build_vision_model_at_import() -> None:
    source = Path(file_ingestor.__file__).read_text(encoding="utf-8")

    assert "build_vision_model()" not in source


def test_public_pipeline_builds_vector_resources_and_preserves_no_vision_default(
    monkeypatch,
) -> None:
    dependencies = _dependencies()
    calls = []
    monkeypatch.setattr(
        public_pipeline,
        "_build_compatibility_resources",
        lambda **kwargs: calls.append(kwargs)
        or CompatibilityIngestionResources(dependencies, object()),
    )
    monkeypatch.setattr(
        public_pipeline._implementation,
        "process_and_ingest_file",
        lambda *args, **kwargs: (args, kwargs),
    )

    args, kwargs = public_pipeline.process_and_ingest_file(
        "manual.md",
        "manual.md",
        "quality",
    )

    assert calls == [{"include_dependencies": True, "include_vision": False}]
    assert args[3] is None
    assert kwargs["dependencies"] is dependencies


def test_file_ingestor_compatibility_factory_is_skipped_when_fully_injected(
    monkeypatch,
) -> None:
    dependencies = _dependencies()
    monkeypatch.setattr(file_ingestor.os.path, "exists", lambda _: True)
    monkeypatch.setattr(
        file_ingestor,
        "_build_compatibility_resources",
        lambda **_kwargs: pytest.fail("worker injection must skip fallback"),
    )
    monkeypatch.setattr(
        file_ingestor.pipeline_implementation,
        "process_and_ingest_file",
        lambda *_args, **_kwargs: {"status": "success", "message": "done"},
    )

    result = file_ingestor.learn_new_file_typed(
        "manual.md",
        "manual.md",
        dependencies=dependencies,
        vision_model=None,
    )

    assert result[0] is True


def test_compatibility_factory_builds_fresh_resources_from_one_settings_snapshot() -> None:
    settings = SimpleNamespace(
        QDRANT_URL="http://qdrant.test",
        QDRANT_API_KEY="qdrant-key",
        QDRANT_COLLECTION="technical-documents",
        EMBEDDING_MODEL="embedding-model",
        EMBEDDING_DEVICE="cpu",
        EMBEDDING_DIM=1024,
        LLM_API_KEY="llm-key",
        LLM_BASE_URL="http://llm.test",
        GPT_VISION_MODEL_NAME="vision-model",
        GPT_VISION_IMAGE_FORMAT="jpeg",
        GPT_VISION_MAX_EDGE=0,
        GPT_VISION_JPEG_QUALITY=85,
        GPT_VISION_TEMPERATURE=0.0,
        GPT_VISION_MAX_OUTPUT_TOKENS=4096,
        GPT_TIMEOUT_SECONDS=180.0,
        GPT_MIN_INTERVAL_SECONDS=0.0,
    )
    settings_calls = []
    qdrant_settings = []
    vision_models = []

    def build_qdrant(projection):
        qdrant_settings.append(projection)
        return _dependencies()

    def build_vision(projection):
        vision_models.append(projection)
        return object()

    for _ in range(2):
        resources = build_compatibility_ingestion_resources(
            include_dependencies=True,
            include_vision=True,
            settings_factory=lambda: settings_calls.append(True) or settings,
            qdrant_runtime_factory=build_qdrant,
            vision_model_factory=build_vision,
        )
        assert resources.dependencies is not None
        assert resources.vision_model is not None

    assert settings_calls == [True, True]
    assert [item.collection for item in qdrant_settings] == [
        "technical-documents",
        "technical-documents",
    ]
    assert [item.model_name for item in vision_models] == [
        "vision-model",
        "vision-model",
    ]
    assert all(item.timeout_seconds == 180.0 for item in vision_models)
