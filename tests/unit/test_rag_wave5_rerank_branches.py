from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from mech_chatbot.rag import rerank


pytestmark = pytest.mark.unit


def _doc(text, **metadata):
    return SimpleNamespace(page_content=text, metadata=metadata)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@contextmanager
def _audit_boundary(**kwargs):
    del kwargs
    yield


def _configure_voyage(monkeypatch, payload):
    monkeypatch.setattr(
        rerank,
        "get_provider_runtime",
        lambda *args, **kwargs: SimpleNamespace(
            api_key="wave5-key",
            model="rerank-2.5-lite",
            endpoint="https://rerank.example/v1",
        ),
    )
    monkeypatch.setattr(rerank, "audited_external_call", _audit_boundary)
    monkeypatch.setattr(
        rerank.requests,
        "post",
        lambda *args, **kwargs: _Response(payload),
    )


def test_rerank_policy_respects_external_processing_policy_and_provider_state(
    monkeypatch,
):
    policy = rerank.RerankPolicy()
    restricted = [_doc("internal", external_processing_policy="internal_only")]
    allowed = [_doc("public", external_processing_policy="all_external")]

    assert policy.select_backend(restricted) == "local_fusion"

    monkeypatch.setenv("USE_VOYAGE_RERANK", "true")
    monkeypatch.setattr(
        rerank,
        "get_provider_runtime",
        lambda *args, **kwargs: SimpleNamespace(api_key="configured"),
    )
    assert policy.select_backend(allowed) == "voyage"

    monkeypatch.setattr(
        rerank,
        "get_provider_runtime",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    assert policy.select_backend(allowed) == "local_fusion"

    monkeypatch.setenv("USE_VOYAGE_RERANK", "false")
    assert policy.select_backend(allowed) == "local_fusion"


def test_voyage_rerank_returns_empty_without_calling_the_provider():
    assert rerank.voyage_rerank_documents([], "query") == []


def test_voyage_rerank_rejects_a_response_without_any_valid_document(monkeypatch):
    _configure_voyage(
        monkeypatch,
        {
            "data": [
                {},
                {"index": "bad"},
                {"index": -1},
                {"index": 99},
            ]
        },
    )

    with pytest.raises(ValueError, match="khong co index document hop le"):
        rerank.voyage_rerank_documents([_doc("one")], "query")


def test_voyage_rerank_skips_duplicate_indexes_and_tolerates_metadata_without_mapping(
    monkeypatch,
):
    _configure_voyage(
        monkeypatch,
        {
            "data": [
                {"index": 0, "relevance_score": "invalid"},
                {"index": 0, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
            ]
        },
    )
    first = SimpleNamespace(page_content="one", metadata=None)
    second = _doc("two")

    assert rerank.voyage_rerank_documents([first, second], "query", top_n=2) == [
        first,
        second,
    ]
    assert second.metadata["relevance_score"] == 0.8


def test_document_type_priority_normalizes_aliases_and_marks_each_candidate():
    contract = _doc("contract", loai_tai_lieu="hop dong mua ban")
    purchase_order = _doc("po", document_type_family="purchase order")
    unrelated = _doc("manual", document_type="manual")

    result = rerank.prioritize_document_types(
        [unrelated, contract, purchase_order],
        ["contract", "purchase_order"],
    )

    assert result == [contract, purchase_order, unrelated]
    assert contract.metadata["document_type_rule_match"] is True
    assert purchase_order.metadata["document_type_rule_match"] is True
    assert unrelated.metadata["document_type_rule_match"] is False
    assert rerank.prioritize_document_types(result, []) == result


def test_candidate_diversity_enforces_the_per_section_limit():
    documents = [
        _doc("first", doc_id=41, parent_section="materials"),
        _doc("second", doc_id=41, parent_section="materials"),
        _doc("third", doc_id=41, parent_section="dimensions"),
    ]

    assert rerank.diversify_candidates(
        documents,
        max_per_document=3,
        max_per_section=1,
        cap=3,
    ) == [documents[0], documents[2]]


def test_legacy_priority_and_long_context_order_remain_deterministic():
    title = _doc("title", loai_du_lieu="title_block")
    text = _doc("text", loai_du_lieu="text")
    image = _doc("image", loai_du_lieu="image_summary")
    other = _doc("other", loai_du_lieu="unknown")

    ranked = rerank.rerank_docs([image, other, title, text])

    assert ranked == [title, other, text, image]
    assert rerank.long_context_reorder(ranked[:2]) == ranked[:2]
    assert rerank.long_context_reorder(ranked) == [title, text, image, other]
