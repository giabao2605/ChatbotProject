from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from mech_chatbot.rag import late_interaction
from mech_chatbot.rag.late_interaction import candidate_key, preflight, rerank_with_shadow


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


def test_shadow_rerank_only_returns_input_candidates_and_keeps_missing_candidates():
    candidates = [doc(1, 1, 0, "one"), doc(2, 1, 0, "two"), doc(3, 1, 0, "three")]
    keys = [candidate_key(item) for item in candidates]

    class ShadowClient:
        def query_points(self, **kwargs):
            assert kwargs["collection_name"] == "MechChatbot_LateInteraction_v1"
            return SimpleNamespace(points=[
                SimpleNamespace(score=0.9, payload={"candidate_key": keys[1]}),
                SimpleNamespace(score=0.7, payload={"candidate_key": "not-an-input"}),
                SimpleNamespace(score=0.5, payload={"candidate_key": keys[0]}),
            ])

    ranked = rerank_with_shadow(candidates, [[0.1, 0.2]], ShadowClient(), top_n=3)

    assert [item.page_content for item in ranked] == ["two", "one", "three"]
    assert ranked[0].metadata["relevance_score"] == 0.9


def test_shadow_failure_uses_existing_fallback_without_leaking_new_documents():
    candidates = [doc(1, 1, 0, "one"), doc(2, 1, 0, "two")]

    class BrokenClient:
        def query_points(self, **kwargs):
            raise RuntimeError("shadow unavailable")

    ranked = rerank_with_shadow(
        candidates,
        [[0.1]],
        BrokenClient(),
        fallback=lambda docs: list(reversed(docs)),
    )

    assert [item.page_content for item in ranked] == ["two", "one"]


def test_missing_shadow_points_are_ranked_by_existing_fallback():
    candidates = [doc(1, 1, 0, "one"), doc(2, 1, 0, "two"), doc(3, 1, 0, "three")]
    first_key = candidate_key(candidates[0])

    class PartialClient:
        def query_points(self, **kwargs):
            return SimpleNamespace(points=[
                SimpleNamespace(score=0.9, payload={"candidate_key": first_key}),
            ])

    ranked = rerank_with_shadow(
        candidates,
        [[0.1]],
        PartialClient(),
        fallback=lambda docs: list(reversed(docs)),
    )

    assert [item.page_content for item in ranked] == ["one", "three", "two"]


def test_late_interaction_requires_encoder_smoke_gate(monkeypatch):
    monkeypatch.setenv("RAG_LATE_INTERACTION_ENABLED", "true")
    monkeypatch.delenv("RAG_LATE_ENCODER_READY", raising=False)
    assert late_interaction.enabled() is False

    monkeypatch.setenv("RAG_LATE_ENCODER_READY", "true")
    assert late_interaction.enabled() is True


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

    assert preflight(Client("1.9.9"), "source")["passed"] is False
    assert preflight(Client("1.10.0"), "source")["passed"] is True


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
