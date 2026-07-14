from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from mech_chatbot.rag import late_interaction
from mech_chatbot.rag.late_interaction import (
    attempt_shadow_rerank,
    candidate_key,
    preflight,
)


pytestmark = pytest.mark.unit


def doc(doc_id, page, chunk, text):
    return Document(
        page_content=text,
        metadata={"doc_id": doc_id, "trang_so": page, "chunk_index": chunk, "security_level": "internal"},
    )


def test_candidate_key_is_stable_and_changes_with_content():
    first = doc(41, 1, 0, "alpha")
    same = doc(41, 1, 0, "alpha")
    changed = doc(41, 1, 0, "beta")

    assert candidate_key(first) == candidate_key(same)
    assert candidate_key(first) != candidate_key(changed)


def test_document_encoder_uses_bounded_colbert_length(monkeypatch):
    seen = {}

    class Encoder:
        def encode(self, texts, **kwargs):
            seen.update(kwargs)
            return {"colbert_vecs": [[[0.1] * 1024] for _ in texts]}

    monkeypatch.setattr(late_interaction, "_encoder", lambda: Encoder())
    monkeypatch.setenv("RAG_LATE_DOCUMENT_MAX_LENGTH", "48")

    late_interaction.encode_documents(["document"])

    assert seen["max_length"] == 48


def test_shadow_attempt_uses_maxsim_only_with_complete_candidate_coverage(monkeypatch):
    candidates = [doc(1, 1, 0, "one"), doc(2, 1, 0, "two"), doc(3, 1, 0, "three")]
    keys = [candidate_key(item) for item in candidates]
    monkeypatch.setattr(late_interaction, "encode_query", lambda _query: [[0.1, 0.2]])

    class ShadowClient:
        def query_points(self, **kwargs):
            assert kwargs["limit"] == 3
            assert kwargs["query_filter"].must[1].match.value == "late-v2"
            return SimpleNamespace(points=[
                SimpleNamespace(score=0.9, payload={"candidate_key": keys[1], "index_version": "late-v2"}),
                SimpleNamespace(score=0.7, payload={"candidate_key": "not-an-input", "index_version": "late-v2"}),
                SimpleNamespace(score=0.5, payload={"candidate_key": keys[0], "index_version": "late-v2"}),
                SimpleNamespace(score=0.4, payload={"candidate_key": keys[2], "index_version": "late-v2"}),
            ])

    result = attempt_shadow_rerank(candidates, "query", ShadowClient(), top_n=2)

    assert result.used_shadow is True
    assert [item.page_content for item in result.documents] == ["two", "one"]
    assert result.candidate_count == 3
    assert result.shadow_hits == 3
    assert result.coverage == 1.0
    assert result.fallback_reason is None
    assert result.total_latency_ms >= result.encode_latency_ms


def test_shadow_attempt_returns_untouched_candidates_on_partial_coverage(monkeypatch):
    candidates = [doc(1, 1, 0, "one"), doc(2, 1, 0, "two")]
    first_key = candidate_key(candidates[0])
    monkeypatch.setattr(late_interaction, "encode_query", lambda _query: [[0.1]])

    class PartialClient:
        def query_points(self, **_kwargs):
            return SimpleNamespace(points=[
                SimpleNamespace(score=0.9, payload={"candidate_key": first_key, "index_version": "late-v2"}),
            ])

    result = attempt_shadow_rerank(candidates, "query", PartialClient(), top_n=1)

    assert result.used_shadow is False
    assert result.documents == tuple(candidates)
    assert result.shadow_hits == 1
    assert result.coverage == 0.5
    assert result.fallback_reason == "partial_coverage"
    assert all("rerank_backend" not in item.metadata for item in candidates)


def test_shadow_attempt_rejects_stale_index_version(monkeypatch):
    candidates = [doc(1, 1, 0, "one")]
    key = candidate_key(candidates[0])
    monkeypatch.setattr(late_interaction, "encode_query", lambda _query: [[0.1]])

    class StaleClient:
        def query_points(self, **_kwargs):
            return SimpleNamespace(points=[SimpleNamespace(
                score=0.9,
                payload={"candidate_key": key, "index_version": "late-v1"},
            )])

    result = attempt_shadow_rerank(candidates, "query", StaleClient())

    assert result.used_shadow is False
    assert result.fallback_reason == "partial_coverage"
    assert result.documents == tuple(candidates)


def test_shadow_attempt_fails_closed_when_encoder_raises(monkeypatch):
    candidates = [doc(1, 1, 0, "one")]
    monkeypatch.setattr(
        late_interaction,
        "encode_query",
        lambda _query: (_ for _ in ()).throw(RuntimeError("encoder unavailable")),
    )

    result = attempt_shadow_rerank(candidates, "query", object())

    assert result.used_shadow is False
    assert result.fallback_reason == "encoder_error"
    assert result.documents == tuple(candidates)


def test_shadow_attempt_fails_closed_when_qdrant_raises(monkeypatch):
    candidates = [doc(1, 1, 0, "one")]
    monkeypatch.setattr(late_interaction, "encode_query", lambda _query: [[0.1]])

    class BrokenClient:
        def query_points(self, **_kwargs):
            raise RuntimeError("qdrant unavailable")

    result = attempt_shadow_rerank(candidates, "query", BrokenClient())

    assert result.used_shadow is False
    assert result.fallback_reason == "shadow_query_error"
    assert result.documents == tuple(candidates)


def test_late_interaction_requires_encoder_smoke_gate(monkeypatch):
    monkeypatch.setenv("RAG_LATE_INTERACTION_ENABLED", "true")
    monkeypatch.delenv("RAG_LATE_ENCODER_READY", raising=False)
    assert late_interaction.enabled() is False

    monkeypatch.setenv("RAG_LATE_ENCODER_READY", "true")
    assert late_interaction.enabled() is True


def test_late_interaction_flags_default_to_off(monkeypatch):
    monkeypatch.delenv("RAG_LATE_INTERACTION_ENABLED", raising=False)
    monkeypatch.delenv("RAG_LATE_ENCODER_READY", raising=False)

    assert late_interaction.enabled() is False


def test_preflight_requires_server_multivector_version():
    class Client:
        def __init__(self, version):
            self.version = version

        def info(self):
            return SimpleNamespace(version=self.version)

        def get_collection(self, name):
            return SimpleNamespace(points_count=10, vectors_count=10)

        def collection_exists(self, name):
            return False

    unsupported = preflight(
        Client("1.9.9"), "source", encoder_report={"passed": True},
    )
    absent_shadow = preflight(
        Client("1.10.0"), "source", encoder_report={"passed": True},
    )

    assert unsupported["capability_passed"] is False
    assert absent_shadow["capability_passed"] is True
    assert absent_shadow["ready_for_serving"] is False
    assert absent_shadow["passed"] is False


def test_preflight_rejects_an_existing_shadow_with_wrong_schema():
    class Client:
        def info(self):
            return SimpleNamespace(version="1.18.2")

        def collection_exists(self, name):
            return name == "MechChatbot_LateInteraction_v1"

        def get_collection(self, name):
            if name == "source":
                return SimpleNamespace(points_count=10, vectors_count=10)
            return SimpleNamespace(
                points_count=2,
                config=SimpleNamespace(params=SimpleNamespace(vectors={})),
                payload_schema={},
            )

    report = preflight(Client(), "source")

    assert report["passed"] is False
    assert report["shadow_schema"]["checks"]["max_sim"] is False
