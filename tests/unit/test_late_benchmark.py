from types import SimpleNamespace

import pytest

from mech_chatbot.rag import late_interaction
from scripts.late_interaction.backfill_shadow import benchmark


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("answer_id", [0, "677a3d4b-2a4b-4f19-9978-996eeb5b2808"])
def test_ranked_sources_preserve_qdrant_chunk_identity(answer_id):
    from langchain_qdrant import QdrantVectorStore
    from scripts.eval.run_late_interaction_eval import _ranked_sources
    from mech_chatbot.evaluation.late_interaction import build_report

    metadata = {"file_goc": "same.md", "doc_id": 71, "trang_so": 1, "version_no": 1}
    documents = [QdrantVectorStore._document_from_point(
        SimpleNamespace(id=point_id, payload={"page_content": text, "metadata": dict(metadata)}),
        "source", "page_content", "metadata",
    ) for point_id, text in [("title", "Title only"), (answer_id, "Answer")]]
    sources = _ranked_sources(documents)
    assert sources[1]["source_id"] == str(answer_id)
    report = build_report([{
        "case": {"case_id": "chunk", "scenario": "exact_code", "expected_sources": [
            {"doc_id": 71, "version": 1, "source_id": str(answer_id), "relevance": 3},
        ], "forbidden_sources": []},
        "ranked_sources": sources, "latency_ms": 1, "coverage": 1.0,
    }], variant="rrf", run_metadata={})
    assert report["ranked_retrieval"]["recall_at_5"] == 1.0
    assert report["ranked_retrieval"]["ndcg_at_5"] == pytest.approx(0.6309297536)


def test_retrieval_child_uses_declared_source_collection(monkeypatch, tmp_path):
    from scripts.eval import run_late_interaction_eval as evaluation
    cache = tmp_path / "candidates.json"
    monkeypatch.setenv("QDRANT_COLLECTION", "other-source")
    def run(command, *, check, env):
        assert check
        assert env["QDRANT_COLLECTION"] == "declared-source"
        cache.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(evaluation.subprocess, "run", run)
    assert evaluation._retrieve_in_worker(
        "manifest", 2, 20, cache, source_collection="declared-source",
    ) == {}
    import os
    assert os.environ["QDRANT_COLLECTION"] == "other-source"


@pytest.mark.parametrize("field,value", [
    ("document_max_length", 48), ("query_max_length", 32),
    ("document_max_length", None), ("query_max_length", None),
])
def test_readiness_rejects_encoder_length_drift(field, value):
    from scripts.eval.run_late_interaction_eval import _readiness_matches
    args = SimpleNamespace(source_collection="source", shadow_collection="shadow", index_version="v1")
    config = SimpleNamespace(document_pooling="adjacent_mean", document_max_length=96, query_max_length=64)
    configuration = dict(source_collection="source", shadow_collection="shadow", index_version="v1", document_pooling="adjacent_mean", document_max_length=96, query_max_length=64)
    assert _readiness_matches({"ready_for_serving": True, "configuration": configuration}, args, config)
    assert not _readiness_matches({"ready_for_serving": True, "configuration": {**configuration, field: value}}, args, config)


def test_voyage_pacing_waits_only_remaining_interval(monkeypatch):
    from scripts.eval import run_late_interaction_eval as evaluation
    waits = []
    monkeypatch.setattr(evaluation.time, "monotonic", lambda: 105.0)
    monkeypatch.setattr(evaluation.time, "sleep", waits.append)
    evaluation._pace_voyage(100.0, 21.0)
    assert waits == [16.0]
    evaluation._pace_voyage(None, 21.0)
    evaluation._pace_voyage(70.0, 21.0)
    assert waits == [16.0]


@pytest.mark.parametrize("fails", [False, True])
def test_evaluator_binds_and_closes_repository_context(monkeypatch, tmp_path, fails):
    from contextlib import contextmanager
    import sys
    from scripts.eval import run_late_interaction_eval as evaluation
    from mech_chatbot.composition import maintenance_runtime

    events = []
    def unexpected_client(*args, **kwargs):
        pytest.fail("context test must not create a network client")
    monkeypatch.setattr(evaluation, "QdrantClient", unexpected_client)
    @contextmanager
    def bind(settings, *, include_qdrant):
        assert include_qdrant is False
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")
    def run(*args):
        assert events == ["enter"]
        if fails:
            raise RuntimeError("evaluation failed")
        return 0
    monkeypatch.setattr(maintenance_runtime, "configured_repository_runtime", bind)
    monkeypatch.setattr(evaluation, "_run_evaluation", run, raising=False)
    argv = ["--output-dir", str(tmp_path), "--run-id", "new", "--readiness", "unused", "--encoder-python", sys.executable]
    if fails:
        with pytest.raises(RuntimeError, match="evaluation failed"):
            evaluation.main(argv)
    else:
        assert evaluation.main(argv) == 0
    assert events == ["enter", "exit"]


def test_provider_identity_uses_resolved_voyage_runtime():
    from scripts.eval.run_late_interaction_eval import _provider_configuration
    settings = SimpleNamespace(VOYAGE_RERANK_TIMEOUT_SECONDS=15)
    runtime = SimpleNamespace(model="resolved-model", endpoint="https://example.test/v1", api_key="private")
    configuration, fingerprint = _provider_configuration(20, settings, runtime)
    assert configuration["voyage_model"] == "resolved-model"
    assert configuration["voyage_endpoint"] == "https://example.test/v1"
    assert "private" not in str(configuration)
    assert len(fingerprint) == 64


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
