from __future__ import annotations

from contextlib import contextmanager

import pytest

from mech_chatbot.db.repositories import document_pages


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, row=None, rows=(), scalar_value=None):
        self._row = row
        self._rows = list(rows)
        self._scalar_value = scalar_value

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar_value

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _Engine:
    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    @contextmanager
    def connect(self):
        yield self

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, params=None):
        query = str(statement)
        bound = dict(params or {})
        self.calls.append((query, bound))
        result = self.results.pop(0) if self.results else _Result()
        if isinstance(result, BaseException):
            raise result
        return result


@pytest.fixture(autouse=True)
def _fixed_repository_boundaries(monkeypatch):
    monkeypatch.setattr(document_pages, "_ensure_engine", lambda: None)
    document_pages._reingest_snapshots.clear()
    yield
    document_pages._reingest_snapshots.clear()


def _install(monkeypatch, *results):
    engine = _Engine(results)
    monkeypatch.setattr(document_pages, "engine", engine)
    return engine


def test_reset_document_metadata_snapshots_then_deletes_all_child_tables(monkeypatch):
    old_rows = {
        "TaiLieuKyThuat": [{"ID": 1, "DocID": 7, "Value": "old"}],
        "BangKeVatTu": [{"ID": 2, "DocID": 7, "Value": "bom"}],
        "DocumentPages": [{"PageID": 3, "DocID": 7, "PageNo": 1}],
        "TechnicalAttributes": [{"AttributeID": 4, "DocID": 7, "AttributeValue": "2 mm"}],
    }
    engine = _install(monkeypatch, *[_Result(rows=old_rows[table]) for table in old_rows])
    monkeypatch.setattr(document_pages._r_document, "_get_or_create_doc", lambda *_args: 7)

    assert document_pages.reset_document_metadata("drawing.pdf", "Technical") == 7

    assert document_pages._reingest_snapshots[7] == old_rows
    deletes = [query for query, params in engine.calls if query.strip().startswith("DELETE") and params == {"d": 7}]
    assert len(deletes) == 4


def test_reset_document_metadata_can_skip_snapshot_and_handle_missing_doc(monkeypatch):
    engine = _install(monkeypatch)
    doc_ids = iter((8, None))
    monkeypatch.setattr(document_pages._r_document, "_get_or_create_doc", lambda *_args: next(doc_ids))

    assert document_pages.reset_document_metadata("drawing.pdf", "Technical", keep_snapshot=False) == 8
    assert len(engine.calls) == 4
    assert document_pages._reingest_snapshots == {}
    assert document_pages.reset_document_metadata("missing.pdf", "Technical") is None
    assert len(engine.calls) == 4


def test_reset_document_metadata_preserves_published_error_and_fails_closed_otherwise(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        document_pages._r_document,
        "_get_or_create_doc",
        lambda *_args: (_ for _ in ()).throw(ValueError("published document is immutable")),
    )

    with pytest.raises(ValueError, match="published"):
        document_pages.reset_document_metadata("drawing.pdf", "Technical")

    monkeypatch.setattr(
        document_pages._r_document,
        "_get_or_create_doc",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    assert document_pages.reset_document_metadata("drawing.pdf", "Technical") is None


def test_clear_reingest_snapshot_is_idempotent():
    document_pages._reingest_snapshots[7] = {"DocumentPages": []}

    document_pages.clear_reingest_snapshot(None)
    document_pages.clear_reingest_snapshot(7)
    document_pages.clear_reingest_snapshot(7)

    assert document_pages._reingest_snapshots == {}


def test_restore_document_children_restores_only_empty_tables_without_identity_columns(monkeypatch):
    document_pages._reingest_snapshots[7] = {
        "TaiLieuKyThuat": [{"ID": 1, "DocID": 7, "Value": "old"}],
        "BangKeVatTu": [{"ID": 2, "DocID": 7, "Value": "existing"}],
        "DocumentPages": [],
        "TechnicalAttributes": [{"AttributeID": 4}],
    }
    engine = _install(
        monkeypatch,
        _Result(scalar_value=0),
        _Result(),
        _Result(scalar_value=1),
        _Result(scalar_value=0),
    )

    assert document_pages.restore_document_children(7) is True

    inserts = [(query, params) for query, params in engine.calls if query.strip().startswith("INSERT")]
    assert len(inserts) == 1
    assert "[ID]" not in inserts[0][0]
    assert inserts[0][1] == {"DocID": 7, "Value": "old"}
    assert 7 not in document_pages._reingest_snapshots


def test_restore_document_children_rejects_missing_snapshot_and_fails_closed(monkeypatch):
    assert document_pages.restore_document_children(None) is False
    assert document_pages.restore_document_children(7) is False

    document_pages._reingest_snapshots[7] = {
        "TaiLieuKyThuat": [{"ID": 1, "DocID": 7}],
    }
    _install(monkeypatch, RuntimeError("database unavailable"))
    assert document_pages.restore_document_children(7) is False


def test_save_document_page_persists_complete_page_contract(monkeypatch):
    engine = _install(monkeypatch, _Result())

    document_pages.save_document_page(7, "drawing.pdf", 3, "text", "vision", "ok", "page-3.png")

    assert engine.calls[0][1] == {
        "d": 7,
        "f": "drawing.pdf",
        "p": 3,
        "t": "text",
        "v": "vision",
        "s": "ok",
        "i": "page-3.png",
    }


def test_save_technical_attributes_ignores_empty_and_caps_values(monkeypatch):
    engine = _install(monkeypatch, _Result())

    document_pages.save_technical_attributes(7, "drawing.pdf", 1, [])
    document_pages.save_technical_attributes(
        7,
        "drawing.pdf",
        1,
        [{
            "AttributeType": "dimension",
            "AttributeName": "length",
            "AttributeValue": "x" * 600,
            "Unit": "mm",
            "SourceText": "L=2",
            "Confidence": 0.9,
            "ExtractedBy": "vision",
        }],
    )

    assert len(engine.calls) == 1
    assert len(engine.calls[0][1]["av"]) == 500
    assert engine.calls[0][1]["at"] == "dimension"


def test_save_document_attributes_ignores_empty_and_normalizes_values(monkeypatch):
    engine = _install(monkeypatch, _Result())

    document_pages.save_document_attributes(7, "mechanical", None)
    document_pages.save_document_attributes(
        7,
        "mechanical",
        [{"key": "k" * 200, "value": 12, "confidence": 0.8}],
    )

    assert len(engine.calls) == 1
    assert engine.calls[0][1] == {
        "d": 7,
        "dom": "mechanical",
        "k": "k" * 150,
        "v": "12",
        "c": 0.8,
        "eb": "regex",
    }


@pytest.mark.parametrize("correct_value", [None, "v" * 600])
def test_verify_technical_attribute_supports_confirmation_and_correction(monkeypatch, correct_value):
    engine = _install(monkeypatch, _Result())

    document_pages.verify_technical_attribute(11, "reviewer", correct_value)

    query, params = engine.calls[0]
    assert params["id"] == 11
    assert params["v"] == "reviewer"
    if correct_value is None:
        assert "AttributeValue = :val" not in query
        assert "val" not in params
    else:
        assert "AttributeValue = :val" in query
        assert len(params["val"]) == 500


def test_get_technical_attributes_for_rag_returns_best_value_per_type(monkeypatch):
    engine = _install(
        monkeypatch,
        _Result(rows=[("material", "steel", None, 1, "manual"), ("length", "20", "mm", 0, "vision")]),
    )

    assert document_pages.get_technical_attributes_for_rag("drawing.pdf") == {
        "material": {
            "value": "steel",
            "unit": None,
            "human_verified": True,
            "extracted_by": "manual",
        },
        "length": {
            "value": "20",
            "unit": "mm",
            "human_verified": False,
            "extracted_by": "vision",
        },
    }
    assert engine.calls[0][1] == {"f": "drawing.pdf"}


def test_save_page_metadata_uses_existing_doc_and_prepared_params(monkeypatch):
    engine = _install(monkeypatch, _Result())
    monkeypatch.setattr(document_pages._r_document, "_prepare_metadata_params", lambda _info: {"trang_so": 3})
    monkeypatch.setattr(
        document_pages._r_document,
        "_get_or_create_doc",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not create document")),
    )

    assert document_pages.save_page_metadata("drawing.pdf", "Technical", {"trang_so": 3}, doc_id=7) == 7
    assert engine.calls[0][1] == {"trang_so": 3, "doc_id": 7}


def test_save_page_metadata_creates_document_when_id_is_missing(monkeypatch):
    engine = _install(monkeypatch, _Result())
    monkeypatch.setattr(document_pages._r_document, "_prepare_metadata_params", lambda _info: {"trang_so": 1})
    monkeypatch.setattr(document_pages._r_document, "_get_or_create_doc", lambda *_args: 9)

    assert document_pages.save_page_metadata("drawing.pdf", "Technical", {"trang_so": 1}) == 9
    assert engine.calls[0][1]["doc_id"] == 9


@pytest.mark.parametrize(
    ("operation", "args", "expected"),
    [
        (document_pages.save_document_page, (7, "drawing.pdf", 1, "text", None, "ok", None), None),
        (document_pages.save_technical_attributes, (7, "drawing.pdf", 1, [{"AttributeValue": "x"}]), None),
        (document_pages.save_document_attributes, (7, "mechanical", [{"key": "x", "value": "y"}]), None),
        (document_pages.verify_technical_attribute, (1, "reviewer"), None),
        (document_pages.get_technical_attributes_for_rag, ("drawing.pdf",), {}),
        (document_pages.save_page_metadata, ("drawing.pdf", "Technical", {"trang_so": 1}, 7), None),
    ],
)
def test_document_page_operations_fail_closed_for_database_errors(monkeypatch, operation, args, expected):
    _install(monkeypatch, RuntimeError("database unavailable"))
    if operation is document_pages.save_page_metadata:
        monkeypatch.setattr(document_pages._r_document, "_prepare_metadata_params", lambda _info: {"trang_so": 1})

    assert operation(*args) == expected


def test_save_document_metadata_delegates_reset_doc_id_to_page_save(monkeypatch):
    calls = []
    monkeypatch.setattr(document_pages, "reset_document_metadata", lambda *_args: 7)
    monkeypatch.setattr(
        document_pages,
        "save_page_metadata",
        lambda *args, **kwargs: calls.append((args, kwargs)) or 7,
    )

    assert document_pages.save_document_metadata("drawing.pdf", "Technical", {"trang_so": 1}) == 7
    assert calls == [(("drawing.pdf", "Technical", {"trang_so": 1}), {"doc_id": 7})]
