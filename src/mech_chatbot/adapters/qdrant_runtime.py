"""Qdrant and embedding construction at an explicit composition boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mech_chatbot.application.vector_ingestion import (
    IngestionPipelineDependencies,
)
from mech_chatbot.config.settings import QdrantSettings


def _validate(settings: QdrantSettings) -> None:
    missing = []
    if not str(settings.url or "").strip():
        missing.append("QDRANT_URL")
    if not str(settings.api_key or "").strip():
        missing.append("QDRANT_API_KEY")
    if not str(settings.collection or "").strip():
        missing.append("QDRANT_COLLECTION")
    if missing:
        raise ValueError(
            "Missing required Qdrant configuration: " + ", ".join(missing)
        )


@dataclass(frozen=True, slots=True)
class QdrantAdminRuntime:
    """Lightweight Qdrant client for metadata and lifecycle operations."""

    client: Any
    collection_name: str

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def build_qdrant_admin_runtime(
    settings: QdrantSettings,
    *,
    client_factory: Callable[..., Any] | None = None,
) -> QdrantAdminRuntime:
    _validate(settings)
    if client_factory is None:
        from qdrant_client import QdrantClient

        client_factory = QdrantClient
    return QdrantAdminRuntime(
        client=client_factory(
            url=settings.url,
            api_key=settings.api_key,
            timeout=120,
        ),
        collection_name=settings.collection,
    )


def _default_factories() -> tuple[Callable[..., Any], ...]:
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_qdrant import FastEmbedSparse, QdrantVectorStore
    from qdrant_client import QdrantClient

    return (
        QdrantClient,
        HuggingFaceEmbeddings,
        FastEmbedSparse,
        QdrantVectorStore,
    )


def _ensure_collection(client: Any, settings: QdrantSettings) -> None:
    if client.collection_exists(settings.collection):
        return

    from qdrant_client import models

    client.create_collection(
        collection_name=settings.collection,
        vectors_config=models.VectorParams(
            size=settings.embedding_dimension,
            distance=models.Distance.COSINE,
        ),
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=False),
            )
        },
    )


def _resolve_factories(
    factories: tuple[Callable[..., Any] | None, ...],
) -> tuple[Callable[..., Any], ...]:
    if all(factory is not None for factory in factories):
        return factories  # type: ignore[return-value]
    defaults = _default_factories()
    return tuple(
        factory or default
        for factory, default in zip(factories, defaults, strict=True)
    )


def _build_vector_store(
    settings: QdrantSettings,
    *,
    client: Any,
    dense_builder: Callable[..., Any],
    sparse_builder: Callable[..., Any],
    store_builder: Callable[..., Any],
    use_default_store: bool,
) -> Any:
    dense = dense_builder(
        model_name=settings.embedding_model,
        model_kwargs={"device": settings.embedding_device},
        encode_kwargs={"normalize_embeddings": True},
    )
    sparse = sparse_builder(model_name="Qdrant/bm25")
    store_kwargs = {
        "client": client,
        "collection_name": settings.collection,
        "embedding": dense,
        "sparse_embedding": sparse,
        "sparse_vector_name": "sparse",
    }
    if use_default_store:
        from langchain_qdrant import RetrievalMode

        store_kwargs["retrieval_mode"] = RetrievalMode.HYBRID
    return store_builder(**store_kwargs)


def build_qdrant_runtime(
    settings: QdrantSettings,
    *,
    client_factory: Callable[..., Any] | None = None,
    dense_embedding_factory: Callable[..., Any] | None = None,
    sparse_embedding_factory: Callable[..., Any] | None = None,
    vector_store_factory: Callable[..., Any] | None = None,
) -> IngestionPipelineDependencies:
    """Construct one process-owned Qdrant client and hybrid vector store."""

    _validate(settings)
    builders = _resolve_factories(
        (
            client_factory,
            dense_embedding_factory,
            sparse_embedding_factory,
            vector_store_factory,
        )
    )
    client = builders[0](
        url=settings.url,
        api_key=settings.api_key,
        timeout=120,
    )
    _ensure_collection(client, settings)
    vector_store = _build_vector_store(
        settings,
        client=client,
        dense_builder=builders[1],
        sparse_builder=builders[2],
        store_builder=builders[3],
        use_default_store=vector_store_factory is None,
    )
    return IngestionPipelineDependencies(
        vector_store=vector_store,
        qdrant_client=client,
        collection_name=settings.collection,
    )


__all__ = [
    "QdrantAdminRuntime",
    "build_qdrant_admin_runtime",
    "build_qdrant_runtime",
]
