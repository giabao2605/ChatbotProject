from __future__ import annotations

from unittest.mock import Mock

import pytest

from mech_chatbot.db.repositories import semantic_cache

from ._small_repository_fakes import Engine, Result


pytestmark = pytest.mark.unit


def _install(monkeypatch, outcomes=(), **engine_kwargs):
    fake = Engine(outcomes, **engine_kwargs)
    monkeypatch.setattr(semantic_cache, "_ensure_engine", lambda: None)
    monkeypatch.setattr(semantic_cache, "engine", fake)
    return fake


def test_put_caps_boundary_fields_and_persists_snapshots(monkeypatch):
    fake = _install(monkeypatch, (Result(),))

    semantic_cache.sc_put(
        "question",
        "[1, 0]",
        "answer",
        "reference",
        "[]",
        "[41]",
        "scope",
        "model-v1",
        0.25,
        question_hash="hash",
        citation_snapshot="citations",
        evidence_snapshot="evidence",
    )

    assert fake.connection.calls[0][1] == {
        "q": "question",
        "qh": "hash",
        "emb": "[1, 0]",
        "a": "answer",
        "rt": "reference",
        "ri": "[]",
        "sd": "[41]",
        "sc": "scope",
        "m": "model-v1",
        "ec": 0.25,
        "citations": "citations",
        "evidence": "evidence",
    }


def test_put_and_best_effort_mutations_do_not_leak_database_errors(monkeypatch):
    _install(monkeypatch, begin_error=RuntimeError("offline"))

    semantic_cache.sc_put("q", "e", "a", "r", "i", "d", "s", "m", 0)
    semantic_cache.sc_delete(1)
    semantic_cache.sc_record_hit(1, 1.0)
    semantic_cache.sc_record_lookup(True, 1.0)


def test_candidates_shape_rows_and_coerce_numeric_query_limits(monkeypatch):
    fake = _install(
        monkeypatch,
        (
            Result(rows=((7, "[1,0]", "a", "r", "[]", "[41]", 0.2, "c", "e"),)),
        ),
    )

    result = semantic_cache.sc_get_candidates("scope", "12", limit="25")

    assert result == [
        {
            "cache_id": 7,
            "embedding": "[1,0]",
            "answer": "a",
            "ref_text": "r",
            "ref_images": "[]",
            "source_doc_ids": "[41]",
            "est_cost": 0.2,
            "citation_snapshot": "c",
            "evidence_snapshot": "e",
        }
    ]
    assert fake.connection.calls[0][1] == {"lim": 25, "sc": "scope", "ttl": 12}


def test_candidates_fail_closed_on_database_error(monkeypatch):
    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert semantic_cache.sc_get_candidates("scope", 12) == []


def test_exact_lookup_returns_snapshot_without_backfill_for_hashed_row(monkeypatch):
    fake = _install(
        monkeypatch,
        (Result(row=(7, "answer", "ref", "[]", "[41]", 0.2, "hash", "c", "e")),),
    )

    result = semantic_cache.sc_get_exact("scope", "hash", "question", "12")

    assert result == {
        "cache_id": 7,
        "answer": "answer",
        "ref_text": "ref",
        "ref_images": "[]",
        "source_doc_ids": "[41]",
        "est_cost": 0.2,
        "citation_snapshot": "c",
        "evidence_snapshot": "e",
    }
    assert len(fake.connection.calls) == 1


def test_exact_lookup_backfills_hash_for_legacy_exact_text_row(monkeypatch):
    fake = _install(
        monkeypatch,
        (
            Result(row=(7, "answer", "ref", "[]", "[41]", 0.2, None, "c", "e")),
            Result(),
        ),
    )

    result = semantic_cache.sc_get_exact("scope", "new-hash", "question", 12)

    assert result["cache_id"] == 7
    assert fake.connection.calls[1][1] == {"question_hash": "new-hash", "cache_id": 7}


def test_exact_lookup_returns_none_for_miss_or_database_failure(monkeypatch):
    _install(monkeypatch, (Result(row=None),))
    assert semantic_cache.sc_get_exact("scope", "hash", "question", 12) is None

    _install(monkeypatch, begin_error=RuntimeError("offline"))
    assert semantic_cache.sc_get_exact("scope", "hash", "question", 12) is None


def test_document_revalidation_accepts_empty_input_without_database_access(monkeypatch):
    fake = _install(monkeypatch)

    assert semantic_cache.sc_docs_all_current([]) is True
    assert fake.connection.calls == []


def test_document_revalidation_deduplicates_ids_and_checks_every_source(monkeypatch):
    fake = _install(monkeypatch, (Result(row=(2,)),))

    assert semantic_cache.sc_docs_all_current([41, "41", 42]) is True
    sql = fake.connection.calls[0][0]
    assert "DocID IN (41,42)" in sql
    assert "PublicationState = 'published'" in sql


def test_document_revalidation_fails_closed_for_stale_or_unavailable_sources(monkeypatch):
    _install(monkeypatch, (Result(row=(1,)),))
    assert semantic_cache.sc_docs_all_current([41, 42]) is False

    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert semantic_cache.sc_docs_all_current([41]) is False


def test_mutation_helpers_persist_expected_metrics(monkeypatch):
    fake = _install(monkeypatch, (Result(), Result(), Result()))

    semantic_cache.sc_delete(7)
    semantic_cache.sc_record_hit(7, 0.5)
    semantic_cache.sc_record_lookup(True, "0.125")

    assert [params for _, params in fake.connection.calls] == [
        {"id": 7},
        {"id": 7},
        {"h": 1, "c": 0.125},
    ]


def test_lookup_miss_records_zero_hit_and_zero_cost(monkeypatch):
    fake = _install(monkeypatch, (Result(),))
    semantic_cache.sc_record_lookup(False, None)
    assert fake.connection.calls[0][1] == {"h": 0, "c": 0.0}


def test_clear_all_returns_deleted_count_and_resets_stats(monkeypatch):
    fake = _install(monkeypatch, (Result(rowcount=4), Result()))

    assert semantic_cache.sc_clear_all() == 4
    assert len(fake.connection.calls) == 2


def test_clear_all_returns_zero_for_unknown_count_or_database_error(monkeypatch):
    _install(monkeypatch, (Result(rowcount=0), Result()))
    assert semantic_cache.sc_clear_all() == 0

    _install(monkeypatch, begin_error=RuntimeError("offline"))
    assert semantic_cache.sc_clear_all() == 0


def test_stats_returns_counts_rate_and_rounded_savings(monkeypatch):
    _install(monkeypatch, (Result(scalar_value=5), Result(row=(10, 4, 1.23456))))

    assert semantic_cache.sc_stats() == {
        "entries": 5,
        "lookups": 10,
        "hits": 4,
        "hit_rate": 40.0,
        "cost_saved": 1.2346,
    }


def test_stats_preserves_empty_shape_for_missing_row_or_database_error(monkeypatch):
    _install(monkeypatch, (Result(scalar_value=None), Result(row=None)))
    assert semantic_cache.sc_stats() == {
        "entries": 0,
        "lookups": 0,
        "hits": 0,
        "hit_rate": 0.0,
        "cost_saved": 0.0,
    }

    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert semantic_cache.sc_stats()["entries"] == 0


def test_invalidation_clears_cache_but_never_breaks_caller(monkeypatch):
    clear = Mock(return_value=3)
    monkeypatch.setattr(semantic_cache, "sc_clear_all", clear)
    semantic_cache._invalidate_semantic_cache("document.updated")
    clear.assert_called_once_with()

    clear.side_effect = RuntimeError("offline")
    semantic_cache._invalidate_semantic_cache("document.updated")
