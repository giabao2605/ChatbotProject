from dataclasses import FrozenInstanceError

import pytest

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_runtime
from mech_chatbot.config.settings import QdrantSettings


pytestmark = pytest.mark.unit


class _Client:
    def __init__(self, *, collection_exists):
        self._collection_exists = collection_exists
        self.created = []

    def collection_exists(self, collection):
        return self._collection_exists

    def create_collection(self, **kwargs):
        self.created.append(kwargs)


def _settings(**overrides):
    values = {
        "url": "https://qdrant.example",
        "api_key": "secret",
        "collection": "KnowledgeBase",
        "embedding_model": "BAAI/bge-m3",
        "embedding_device": "cpu",
        "embedding_dimension": 1024,
    }
    values.update(overrides)
    return QdrantSettings(**values)


def test_qdrant_runtime_builds_explicit_vector_dependencies():
    client = _Client(collection_exists=False)
    dense = object()
    sparse = object()
    vector_store = object()
    client_calls = []
    dense_calls = []
    sparse_calls = []
    vector_calls = []

    dependencies = build_qdrant_runtime(
        _settings(),
        client_factory=lambda **kwargs: client_calls.append(kwargs) or client,
        dense_embedding_factory=lambda **kwargs: dense_calls.append(kwargs) or dense,
        sparse_embedding_factory=lambda **kwargs: sparse_calls.append(kwargs) or sparse,
        vector_store_factory=lambda **kwargs: vector_calls.append(kwargs)
        or vector_store,
    )

    assert dependencies.qdrant_client is client
    assert dependencies.vector_store is vector_store
    assert dependencies.collection_name == "KnowledgeBase"
    assert client_calls == [
        {
            "url": "https://qdrant.example",
            "api_key": "secret",
            "timeout": 120,
        }
    ]
    assert dense_calls[0]["model_name"] == "BAAI/bge-m3"
    assert dense_calls[0]["model_kwargs"] == {"device": "cpu"}
    assert sparse_calls == [{"model_name": "Qdrant/bm25"}]
    assert vector_calls[0]["client"] is client
    assert len(client.created) == 1

    with pytest.raises(FrozenInstanceError):
        dependencies.collection_name = "other"


@pytest.mark.parametrize(
    ("overrides", "missing_key"),
    [
        ({"url": None}, "QDRANT_URL"),
        ({"api_key": None}, "QDRANT_API_KEY"),
        ({"collection": ""}, "QDRANT_COLLECTION"),
    ],
)
def test_qdrant_runtime_fails_fast_without_leaking_secret(overrides, missing_key):
    with pytest.raises(ValueError) as error:
        build_qdrant_runtime(
            _settings(**overrides),
            client_factory=lambda **_: pytest.fail("must validate before client"),
            dense_embedding_factory=lambda **_: object(),
            sparse_embedding_factory=lambda **_: object(),
            vector_store_factory=lambda **_: object(),
        )

    assert missing_key in str(error.value)
    assert "secret" not in str(error.value)
