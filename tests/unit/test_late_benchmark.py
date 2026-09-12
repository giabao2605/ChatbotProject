from types import SimpleNamespace

import pytest

from mech_chatbot.rag import late_interaction
from scripts.late_interaction.backfill_shadow import benchmark


pytestmark = pytest.mark.unit


def test_quality_evaluation_uses_declared_index_and_pooling():
    from scripts.eval import run_late_interaction_eval as evaluation

    settings = SimpleNamespace(
        RAG_LATE_MODEL="BAAI/bge-m3", EMBEDDING_DEVICE="cpu",
        RAG_LATE_QUERY_MAX_LENGTH=64, RAG_LATE_DOCUMENT_MAX_LENGTH=96,
        RAG_LATE_DOCUMENT_POOLING="adjacent_mean",
    )
    config = evaluation._evaluation_config(SimpleNamespace(
        shadow_collection="new-shadow", index_version="pooled-v1",
    ), settings)
    assert config.collection_name == "new-shadow"
    assert config.index_version == "pooled-v1"
    assert config.document_pooling == "adjacent_mean"
    assert config.document_max_length == 96
    assert config.query_max_length == 64


def test_readiness_matching_rejects_pooling_drift():
    from scripts.eval.run_late_interaction_eval import _evaluation_config, _readiness_matches
    args = SimpleNamespace(source_collection="source", shadow_collection="shadow", index_version="v1")
    settings = SimpleNamespace(RAG_LATE_MODEL="m", EMBEDDING_DEVICE="cpu", RAG_LATE_QUERY_MAX_LENGTH=64, RAG_LATE_DOCUMENT_MAX_LENGTH=96, RAG_LATE_DOCUMENT_POOLING="adjacent_mean")
    config = _evaluation_config(args, settings)
    readiness = {"ready_for_serving": True, "configuration": {"source_collection": "source", "shadow_collection": "shadow", "index_version": "v1", "document_pooling": "none"}}
    assert _readiness_matches(readiness, args, config) is False


def test_benchmark_measures_warm_encoder_without_reloading_model(monkeypatch):
    clock = [0.0]
    queries = []

    class Encoder:
        def __init__(self, *args, **kwargs):
            clock[0] += 10.0

        def encode(self, texts, **kwargs):
            clock[0] += 0.01
            return {"colbert_vecs": [[[1.0, 0.0]]]}

    class Client:
        def scroll(self, **kwargs):
            return [SimpleNamespace(payload={"candidate_key": "candidate-1"})], None

        def query_points(self, **kwargs):
            queries.append(kwargs["query"])
            clock[0] += 0.02

    monkeypatch.setattr(late_interaction, "_load_encoder_type", lambda: Encoder)
    monkeypatch.setattr("scripts.late_interaction.backfill_shadow.time.perf_counter", lambda: clock[0])

    report = benchmark(Client(), "shadow", iterations=3)

    assert report == {
        "passed": True,
        "iterations": 3,
        "encode_p50_ms": 10.0,
        "encode_p95_ms": 10.0,
        "query_p50_ms": 20.0,
        "query_p95_ms": 20.0,
    }
    assert queries == [[[1.0, 0.0]]] * 5


def test_empty_benchmark_does_not_load_encoder(monkeypatch):
    class Client:
        def scroll(self, **kwargs):
            return [], None

    def unexpected_load():
        pytest.fail("empty benchmark must not load the model")

    monkeypatch.setattr(late_interaction, "_load_encoder_type", unexpected_load)

    assert benchmark(Client(), "shadow") == {
        "passed": False, "reason": "empty_shadow_collection",
    }
