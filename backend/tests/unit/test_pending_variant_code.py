from contextlib import contextmanager

import pytest

from mech_chatbot.db.repositories import doc_metadata

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, row=None):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class _Engine:
    def __init__(self, *, status="pending_review", collision=False, fail_update=False):
        self.status = status
        self.collision = collision
        self.fail_update = fail_update
        self.calls = []

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if "UPDATE dbo.TaiLieu SET VariantCode" in sql and self.fail_update:
            raise RuntimeError("SQL unavailable")
        if "SELECT BaseCode, VariantCode, ReviewStatus" in sql:
            return _Result({
                "BaseCode": "qa-family", "VariantCode": "default",
                "ReviewStatus": self.status, "IsCurrent": False, "Servable": False,
            })
        if "SELECT TOP 1 DocID" in sql:
            return _Result({"DocID": 168} if self.collision else None)
        return _Result()


@pytest.fixture(autouse=True)
def _boundaries(monkeypatch):
    monkeypatch.setattr(doc_metadata, "_ensure_engine", lambda: None)
    monkeypatch.setattr(doc_metadata._r_audit, "write_audit_log", lambda *_args: None)


def test_pending_variant_code_updates_one_doc_and_its_vectors(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(doc_metadata, "engine", engine)
    vector_calls = []
    monkeypatch.setattr(
        doc_metadata._r_qdrant, "update_qdrant_metadata",
        lambda *args, **kwargs: vector_calls.append((args, kwargs)) or True,
    )

    result = doc_metadata.update_pending_variant_code(170, " beta ", reviewer="qa-reviewer")

    assert result == {"ok": True, "variant_code": "beta"}
    assert any("UPDATE dbo.TaiLieu SET VariantCode" in sql and params == {"doc_id": 170, "variant_code": "beta"}
               for sql, params in engine.calls)
    assert vector_calls == [((170, {"variant_code": "beta"}), {"require_points": True})]


@pytest.mark.parametrize("status,collision", [("approved", False), ("pending_review", True)])
def test_pending_variant_code_rejects_ineligible_or_duplicate_without_writes(monkeypatch, status, collision):
    engine = _Engine(status=status, collision=collision)
    monkeypatch.setattr(doc_metadata, "engine", engine)
    vector_calls = []
    monkeypatch.setattr(doc_metadata._r_qdrant, "update_qdrant_metadata", lambda *args, **kwargs: vector_calls.append(args))

    result = doc_metadata.update_pending_variant_code(170, "beta")

    assert result["ok"] is False
    assert not any("UPDATE dbo.TaiLieu SET VariantCode" in sql for sql, _ in engine.calls)
    assert vector_calls == []


def test_pending_variant_code_does_not_write_sql_when_vector_update_fails(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(doc_metadata, "engine", engine)
    monkeypatch.setattr(doc_metadata._r_qdrant, "update_qdrant_metadata", lambda *_args, **_kwargs: False)

    result = doc_metadata.update_pending_variant_code(170, "beta")

    assert result["ok"] is False
    assert not any("UPDATE dbo.TaiLieu SET VariantCode" in sql for sql, _ in engine.calls)


def test_pending_variant_code_rejects_default_even_when_unchanged(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(doc_metadata, "engine", engine)
    monkeypatch.setattr(doc_metadata._r_qdrant, "update_qdrant_metadata", lambda *_args, **_kwargs: pytest.fail("no vector write"))

    result = doc_metadata.update_pending_variant_code(170, "default")

    assert result["ok"] is False
    assert not any("UPDATE dbo.TaiLieu SET VariantCode" in sql for sql, _ in engine.calls)


def test_pending_variant_code_restores_vectors_after_sql_write_fails(monkeypatch):
    engine = _Engine(fail_update=True)
    monkeypatch.setattr(doc_metadata, "engine", engine)
    vector_codes = []
    monkeypatch.setattr(
        doc_metadata._r_qdrant, "update_qdrant_metadata",
        lambda _doc_id, updates, **_kwargs: vector_codes.append(updates["variant_code"]) or True,
    )

    result = doc_metadata.update_pending_variant_code(170, "beta")

    assert result["ok"] is False
    assert vector_codes == ["beta", "default"]
