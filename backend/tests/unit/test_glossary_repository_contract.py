from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest

from mech_chatbot.db.repositories import glossary

from ._small_repository_fakes import Engine, Result


pytestmark = pytest.mark.unit


def _install(monkeypatch, outcomes=(), **engine_kwargs):
    fake = Engine(outcomes, **engine_kwargs)
    monkeypatch.setattr(glossary, "_ensure_engine", lambda: None)
    monkeypatch.setattr(glossary, "engine", fake)
    monkeypatch.setattr(glossary._r_catalog, "_catalog_cache_get", Mock(return_value=None))
    monkeypatch.setattr(glossary._r_catalog, "_catalog_cache_put", Mock())
    monkeypatch.setattr(glossary._r_catalog, "_catalog_cache_invalidate", Mock())
    monkeypatch.setattr(glossary._r_semantic_cache, "_invalidate_semantic_cache", Mock())
    return fake


def test_active_glossary_returns_defensive_copy_from_cache(monkeypatch):
    cached = [{"term": "PLC"}]
    ensure = Mock()
    monkeypatch.setattr(glossary, "_ensure_engine", ensure)
    monkeypatch.setattr(glossary._r_catalog, "_catalog_cache_get", Mock(return_value=cached))

    result = glossary.get_active_glossary(["automation"])

    assert result == cached
    assert result is not cached
    ensure.assert_not_called()


def test_active_glossary_filters_domains_and_normalizes_synonyms(monkeypatch):
    _install(
        monkeypatch,
        (
            Result(
                rows=(
                    (1, "automation", "PLC", '["controller", "cpu"]', "controller"),
                    (2, "automation", "HMI", '"display"', None),
                    (3, "mechanical", "SS", "steel, inox", None),
                )
            ),
        ),
    )

    result = glossary.get_active_glossary(["automation"])

    assert result == [
        {
            "glossary_id": 1,
            "domain": "automation",
            "term": "PLC",
            "synonyms": ["controller", "cpu"],
            "expansion": "controller",
        },
        {
            "glossary_id": 2,
            "domain": "automation",
            "term": "HMI",
            "synonyms": ["display"],
            "expansion": None,
        },
    ]
    glossary._r_catalog._catalog_cache_put.assert_called_once_with(
        "gloss_active:automation", result
    )


def test_active_glossary_falls_back_to_comma_list_and_fails_closed(monkeypatch):
    _install(monkeypatch, (Result(rows=((1, "d", "t", "a, b", None),)),))
    assert glossary.get_active_glossary()[0]["synonyms"] == ["a", "b"]

    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert glossary.get_active_glossary() == []


def test_domain_glossary_applies_filters_and_shapes_rows(monkeypatch):
    created = datetime(2026, 7, 20, 8, 0)
    fake = _install(
        monkeypatch,
        (Result(rows=((7, "mechanical", "SS", "inox, steel", None, 1, created),)),),
    )

    result = glossary.list_domain_glossary("mechanical", active_only=True)

    assert result == [
        {
            "glossary_id": 7,
            "domain": "mechanical",
            "term": "SS",
            "synonyms": ["inox", "steel"],
            "expansion": None,
            "is_active": True,
            "created_at": created,
        }
    ]
    sql, params = fake.connection.calls[0]
    assert "Domain = :d" in sql and "IsActive = 1" in sql
    assert params == {"d": "mechanical"}


def test_domain_glossary_without_filters_and_database_failure(monkeypatch):
    fake = _install(monkeypatch, (Result(rows=()),))
    assert glossary.list_domain_glossary() == []
    assert " WHERE " not in fake.connection.calls[0][0]

    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert glossary.list_domain_glossary() == []


def test_upsert_rejects_missing_required_values_without_opening_transaction(monkeypatch):
    fake = _install(monkeypatch)

    assert glossary.upsert_glossary_term("", "mechanical") == {
        "ok": False,
        "message": "thieu term hoac domain",
    }
    assert glossary.upsert_glossary_term("SS", "") == {
        "ok": False,
        "message": "thieu term hoac domain",
    }
    assert fake.connection.calls == []


def test_upsert_insert_serializes_synonyms_and_invalidates_read_caches(monkeypatch):
    fake = _install(monkeypatch, (Result(row=(17,)),))

    result = glossary.upsert_glossary_term(
        "SS", "mechanical", synonyms=["inox", "", "steel"], expansion="stainless", is_active=False
    )

    assert result == {"ok": True, "glossary_id": 17}
    params = fake.connection.calls[0][1]
    assert params == {
        "d": "mechanical",
        "t": "SS",
        "s": '["inox", "steel"]',
        "e": "stainless",
        "a": 0,
    }
    glossary._r_semantic_cache._invalidate_semantic_cache.assert_called_once_with("glossary.upsert")
    glossary._r_catalog._catalog_cache_invalidate.assert_called_once_with("gloss_active:")


def test_upsert_update_preserves_identifier_and_reports_database_error(monkeypatch):
    fake = _install(monkeypatch, (Result(),))
    assert glossary.upsert_glossary_term("PLC", "automation", glossary_id=9) == {
        "ok": True,
        "glossary_id": 9,
    }
    assert fake.connection.calls[0][1]["gid"] == 9

    _install(monkeypatch, begin_error=RuntimeError("write failed"))
    result = glossary.upsert_glossary_term("PLC", "automation")
    assert result == {"ok": False, "message": "write failed"}


@pytest.mark.parametrize(
    ("operation", "reason", "expected_params"),
    [
        (lambda: glossary.set_glossary_active(3, False), "glossary.active", {"a": 0, "gid": 3}),
        (lambda: glossary.delete_glossary_term(3), "glossary.delete", {"gid": 3}),
    ],
)
def test_mutations_update_storage_and_invalidate_both_caches(
    monkeypatch, operation, reason, expected_params
):
    fake = _install(monkeypatch, (Result(),))

    assert operation() is True
    assert fake.connection.calls[0][1] == expected_params
    glossary._r_semantic_cache._invalidate_semantic_cache.assert_called_once_with(reason)
    glossary._r_catalog._catalog_cache_invalidate.assert_called_once_with("gloss_active:")


@pytest.mark.parametrize(
    "operation",
    [
        lambda: glossary.set_glossary_active(3, True),
        lambda: glossary.delete_glossary_term(3),
    ],
)
def test_mutations_report_database_failure_without_cache_invalidation(monkeypatch, operation):
    _install(monkeypatch, begin_error=RuntimeError("offline"))

    assert operation() is False
    glossary._r_semantic_cache._invalidate_semantic_cache.assert_not_called()
    glossary._r_catalog._catalog_cache_invalidate.assert_not_called()
