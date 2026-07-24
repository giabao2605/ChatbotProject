from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mech_chatbot.db import repository
from mech_chatbot.rag import semantic_cache


pytestmark = pytest.mark.unit


@pytest.fixture
def cache_repository(monkeypatch: pytest.MonkeyPatch):
    boundary = SimpleNamespace(
        sc_delete=Mock(),
        sc_docs_all_current=Mock(return_value=True),
        sc_get_candidates=Mock(return_value=[]),
        sc_get_exact=Mock(return_value=None),
        sc_put=Mock(),
        sc_record_hit=Mock(),
        sc_record_lookup=Mock(),
    )
    for name, operation in vars(boundary).items():
        monkeypatch.setattr(repository, name, operation)
    return boundary


def _cache_row(**overrides):
    return {
        "cache_id": 7,
        "embedding": "[1.0, 0.0]",
        "answer": "Pressure is 12 bar.",
        "ref_text": "Manual section 4",
        "ref_images": '["diagram.png"]',
        "source_doc_ids": "[41]",
        "est_cost": "0.0025",
        "citation_snapshot": '[{"doc_id": 41, "page": 4}]',
        "evidence_snapshot": '[{"doc_id": 41, "quote": "12 bar"}]',
        **overrides,
    }


def _store(cache_repository, **overrides):
    arguments = {
        "question": "  MAX PRESSURE?  ",
        "embedding": [0.12345678, 1],
        "answer": "Pressure is 12 bar.",
        "ref_text": "Manual section 4",
        "ref_images": ["diagram.png"],
        "source_doc_ids": [41],
        "scope_sig": "department=maintenance",
        "model": "answer-model-v1",
        "est_cost": "0.0025",
        "citation_snapshot": [{"doc_id": 41, "page": 4}],
        "evidence_snapshot": [{"doc_id": 41, "quote": "12 bar"}],
        **overrides,
    }
    semantic_cache.store(**arguments)
    return cache_repository.sc_put


def test_exact_lookup_miss_returns_none(cache_repository):
    assert semantic_cache.lookup_exact("missing", "scope") is None


def test_exact_lookup_returns_authorized_snapshot(cache_repository):
    cache_repository.sc_get_exact.return_value = _cache_row()

    result = semantic_cache.lookup_exact("  MAX PRESSURE?  ", "scope")

    assert result == {
        "answer": "Pressure is 12 bar.",
        "ref_text": "Manual section 4",
        "ref_images": ["diagram.png"],
        "score": 1.0,
        "citation_snapshot": [{"doc_id": 41, "page": 4}],
        "evidence_snapshot": [{"doc_id": 41, "quote": "12 bar"}],
    }


@pytest.mark.parametrize(
    ("row_change", "documents_are_current"),
    [
        ({"source_doc_ids": "not-json"}, True),
        ({"citation_snapshot": "not-json"}, True),
        ({"evidence_snapshot": "{}"}, True),
        ({"citation_snapshot": '[1, {"doc_id": 41}]'}, False),
    ],
    ids=("bad-document-ids", "bad-citations", "bad-evidence", "stale-document"),
)
def test_exact_lookup_rejects_unverifiable_payload(
    cache_repository,
    row_change,
    documents_are_current,
):
    cache_repository.sc_get_exact.return_value = _cache_row(**row_change)
    cache_repository.sc_docs_all_current.return_value = documents_are_current

    result = semantic_cache.lookup_exact("pressure", "scope")

    assert (
        result,
        cache_repository.sc_delete.call_args_list,
        cache_repository.sc_record_lookup.call_args_list,
    ) == (None, [((7,), {})], [((False, 0.0), {})])


def test_exact_lookup_degrades_malformed_image_references_to_empty(cache_repository):
    cache_repository.sc_get_exact.return_value = _cache_row(ref_images="not-json")

    result = semantic_cache.lookup_exact("pressure", "scope")

    assert result["ref_images"] == []


def test_semantic_lookup_ignores_invalid_embeddings_and_returns_best_authorized_hit(
    cache_repository,
):
    cache_repository.sc_get_candidates.return_value = [
        _cache_row(cache_id=1, embedding="not-json"),
        _cache_row(cache_id=2, embedding="[]"),
        _cache_row(cache_id=3, embedding="[1.0, 0.0]"),
    ]

    result = semantic_cache.lookup("pressure", [1.0, 0.0], "scope")

    assert result == {
        "answer": "Pressure is 12 bar.",
        "ref_text": "Manual section 4",
        "ref_images": ["diagram.png"],
        "score": 1.0,
        "citation_snapshot": [{"doc_id": 41, "page": 4}],
        "evidence_snapshot": [{"doc_id": 41, "quote": "12 bar"}],
    }


def test_semantic_lookup_miss_survives_repository_failures(cache_repository):
    cache_repository.sc_get_candidates.side_effect = RuntimeError("database unavailable")
    cache_repository.sc_record_lookup.side_effect = RuntimeError("metrics unavailable")

    assert semantic_cache.lookup("pressure", [1.0, 0.0], "scope") is None


def test_semantic_lookup_fails_closed_when_document_recheck_errors(cache_repository):
    cache_repository.sc_get_candidates.return_value = [_cache_row()]
    cache_repository.sc_docs_all_current.side_effect = RuntimeError("recheck unavailable")

    result = semantic_cache.lookup("pressure", [1.0, 0.0], "scope")

    assert (
        result,
        cache_repository.sc_record_lookup.call_args_list,
    ) == (None, [((False, 0.0), {})])


def test_semantic_lookup_hit_survives_bad_optional_fields_and_metric_failures(
    cache_repository,
):
    cache_repository.sc_get_candidates.return_value = [
        _cache_row(est_cost="not-a-number", ref_images="not-json")
    ]
    cache_repository.sc_record_hit.side_effect = RuntimeError("hit metric unavailable")
    cache_repository.sc_record_lookup.side_effect = RuntimeError("lookup metric unavailable")

    result = semantic_cache.lookup("pressure", [1.0, 0.0], "scope")

    assert (result["score"], result["ref_images"]) == (1.0, [])


@pytest.mark.parametrize(
    "overrides",
    [
        {"answer": ""},
        {"answer": "Tai lieu hien tai khong du de tra loi."},
        {"source_doc_ids": []},
        {"citation_snapshot": []},
        {"evidence_snapshot": []},
        {"embedding": [object()]},
        {"source_doc_ids": ["invalid-document-id"]},
    ],
    ids=(
        "empty-answer",
        "refusal",
        "missing-documents",
        "missing-citations",
        "missing-evidence",
        "invalid-embedding",
        "invalid-document-reference",
    ),
)
def test_store_rejects_incomplete_or_invalid_cache_entries(cache_repository, overrides):
    put = _store(cache_repository, **overrides)

    assert put.call_count == 0


def test_store_respects_disabled_cache(cache_repository):
    put = _store(cache_repository, cache_enabled=False)

    assert put.call_count == 0


def test_store_serializes_complete_entry_for_repository_boundary(cache_repository):
    put = _store(cache_repository)

    assert put.call_args.kwargs == {
        "question": "  MAX PRESSURE?  ",
        "embedding": "[0.123457, 1.0]",
        "answer": "Pressure is 12 bar.",
        "ref_text": "Manual section 4",
        "ref_images": '["diagram.png"]',
        "source_doc_ids": "[41]",
        "scope_sig": "department=maintenance",
        "model": "answer-model-v1",
        "est_cost": 0.0025,
        "question_hash": "808f57fd60122a9086d0d06f9329daef010585cc35b69347270442f0ca440569",
        "citation_snapshot": '[{"doc_id": 41, "page": 4}]',
        "evidence_snapshot": '[{"doc_id": 41, "quote": "12 bar"}]',
    }


def test_completed_stream_is_replayed_and_stored(cache_repository):
    result = list(
        semantic_cache.teeing_store_stream(
            iter(("abcd", "efgh")),
            question="pressure",
            embedding=[1.0, 0.0],
            scope_sig="scope",
            ref_text="manual",
            ref_images=[],
            source_doc_ids=[41],
            model="answer-model-v1",
            input_char_len=8,
            citation_snapshot=[{"doc_id": 41}],
            evidence_snapshot=[{"doc_id": 41}],
        )
    )

    assert (
        result,
        cache_repository.sc_put.call_args.kwargs["answer"],
        cache_repository.sc_put.call_args.kwargs["est_cost"],
    ) == (["abcd", "efgh"], "abcdefgh", 0.000035)


def test_failed_stream_propagates_error_without_storing(cache_repository):
    def failing_stream():
        yield "partial"
        raise RuntimeError("generation failed")

    stream = semantic_cache.teeing_store_stream(
        failing_stream(), "q", [1.0], "scope", "", [], [41], "model"
    )

    with pytest.raises(RuntimeError, match="generation failed"):
        list(stream)
    assert cache_repository.sc_put.call_count == 0


def test_cancelled_stream_does_not_store_partial_answer(cache_repository):
    stream = semantic_cache.teeing_store_stream(
        iter(("partial", "unconsumed")),
        "q",
        [1.0],
        "scope",
        "",
        [],
        [41],
        "model",
    )

    first_chunk = next(stream)
    stream.close()

    assert (first_chunk, cache_repository.sc_put.call_count) == ("partial", 0)
