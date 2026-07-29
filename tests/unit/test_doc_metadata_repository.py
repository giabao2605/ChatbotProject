from contextlib import contextmanager

import pytest

from mech_chatbot.db.repositories import doc_metadata


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, row=None, rows=()):
        self._row = row
        self._rows = list(rows)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _Engine:
    def __init__(self, handler):
        self._handler = handler
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
        return self._handler(query, bound)


@pytest.fixture(autouse=True)
def _fixed_repository_boundaries(monkeypatch):
    monkeypatch.setenv("RAG_EXECUTION_CONTEXT", "test")
    monkeypatch.setattr(doc_metadata, "_ensure_engine", lambda: None)


def test_clean_meta_value_normalizes_only_strings():
    assert doc_metadata._clean_meta_value(None) is None
    assert doc_metadata._clean_meta_value("  ") is None
    assert doc_metadata._clean_meta_value("  value  ") == "value"
    assert doc_metadata._clean_meta_value(0) == 0


@pytest.mark.parametrize("payload", [None, "not-json", "[]"])
def test_apply_upload_metadata_ignores_missing_or_invalid_payload(payload):
    engine = _Engine(lambda _query, _params: _Result())

    doc_metadata._apply_upload_meta_to_doc(engine, 7, payload, "quality")

    assert engine.calls == []


def test_apply_upload_metadata_updates_common_governance_and_manual_attributes():
    engine = _Engine(lambda _query, _params: _Result())
    payload = """{
        "title": "  Drawing A  ",
        "summary": "  ",
        "knowledge_owner_user_id": "17",
        "knowledge_approver_user_id": "invalid",
        "taxonomy_version": "  v2  ",
        "parent_section": "  Procedure 1  ",
        "parent_page": "3",
        "attributes": {
            "material": "  steel  ",
            "empty": "  ",
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA": 12
        }
    }"""

    doc_metadata._apply_upload_meta_to_doc(engine, 7, payload, "")

    assert len(engine.calls) == 5
    common_sql, common_params = engine.calls[0]
    assert "UPDATE TaiLieu SET Title = :title" in common_sql
    assert common_params == {"d": 7, "title": "Drawing A"}
    governance_sql, governance_params = engine.calls[1]
    assert "KnowledgeOwnerUserID = :gov_knowledge_owner_user_id" in governance_sql
    assert "KnowledgeApproverUserID" not in governance_sql
    assert "ParentApplicable = 1" in governance_sql
    assert governance_params == {
        "d": 7,
        "gov_knowledge_owner_user_id": 17,
        "gov_taxonomy_version": "v2",
        "gov_parent_section": "Procedure 1",
        "gov_parent_page": 3,
    }
    assert "DELETE FROM DocumentAttributes" in engine.calls[2][0]
    assert engine.calls[3][1] == {
        "d": 7,
        "dom": "generic",
        "k": "material",
        "v": "steel",
    }
    assert len(engine.calls[4][1]["k"]) == 150
    assert engine.calls[4][1]["v"] == "12"


def test_apply_upload_metadata_normalizes_optional_governance_values():
    engine = _Engine(lambda _query, _params: _Result())
    payload = """{
        "knowledge_owner_user_id": "",
        "knowledge_approver_user_id": null,
        "parent_page": 0,
        "parent_applicable": false,
        "parent_section": "",
        "attributes": "not-a-dict"
    }"""

    doc_metadata._apply_upload_meta_to_doc(engine, 9, payload, "engineering")

    assert len(engine.calls) == 1
    assert engine.calls[0][1] == {
        "d": 9,
        "gov_knowledge_owner_user_id": None,
        "gov_knowledge_approver_user_id": None,
        "gov_parent_applicable": 0,
        "gov_parent_section": None,
    }


def test_apply_upload_metadata_fails_closed_when_database_write_raises():
    def raise_database_error(_query, _params):
        raise RuntimeError("database unavailable")

    engine = _Engine(raise_database_error)

    doc_metadata._apply_upload_meta_to_doc(engine, 3, '{"title": "A"}', None)

    assert len(engine.calls) == 1


def test_apply_upload_metadata_can_update_common_fields_without_governance():
    engine = _Engine(lambda _query, _params: _Result())

    doc_metadata._apply_upload_meta_to_doc(engine, 3, '{"title": "A"}', None)

    assert len(engine.calls) == 1
    assert engine.calls[0][1] == {"d": 3, "title": "A"}


def test_get_document_attributes_returns_manual_first_and_supports_domain(monkeypatch):
    engine = _Engine(
        lambda _query, _params: _Result(
            rows=[
                ("material", "manual steel", "manual"),
                ("material", "ai steel", "llm"),
                ("thickness", "2 mm", "regex"),
            ]
        )
    )
    monkeypatch.setattr(doc_metadata, "engine", engine)

    assert doc_metadata.get_document_attributes(7, "mechanical") == {
        "material": "manual steel",
        "thickness": "2 mm",
    }
    query, params = engine.calls[0]
    assert "AND Domain = :dom" in query
    assert params == {"d": 7, "dom": "mechanical"}


def test_get_document_attributes_handles_empty_id_and_database_error(monkeypatch):
    assert doc_metadata.get_document_attributes(None) == {}
    engine = _Engine(lambda _query, _params: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(doc_metadata, "engine", engine)

    assert doc_metadata.get_document_attributes(7) == {}
    assert "AND Domain = :dom" not in engine.calls[0][0]


def test_get_document_metadata_maps_row_and_attaches_attributes(monkeypatch):
    row = tuple(range(1, 20))
    engine = _Engine(lambda _query, _params: _Result(row=row))
    monkeypatch.setattr(doc_metadata, "engine", engine)
    monkeypatch.setattr(
        doc_metadata,
        "get_document_attributes",
        lambda doc_id: {"doc": str(doc_id)},
    )

    result = doc_metadata.get_document_metadata(42)

    assert result == {
        "title": 1,
        "summary": 2,
        "tags": 3,
        "doc_number": 4,
        "issued_date": 5,
        "effective_date": 6,
        "expiry_date": 7,
        "review_date": 8,
        "owner_signer": 9,
        "language": 10,
        "effective_status": 11,
        "site": 12,
        "domain": 13,
        "knowledge_owner_user_id": 14,
        "knowledge_approver_user_id": 15,
        "taxonomy_version": 16,
        "parent_applicable": True,
        "parent_section": 18,
        "parent_page": 19,
        "attributes": {"doc": "42"},
    }


def test_get_document_metadata_returns_attributes_when_row_missing_or_query_fails(monkeypatch):
    monkeypatch.setattr(doc_metadata, "get_document_attributes", lambda _doc_id: {"safe": "fallback"})
    engine = _Engine(lambda _query, _params: _Result(row=None))
    monkeypatch.setattr(doc_metadata, "engine", engine)
    assert doc_metadata.get_document_metadata(8) == {"attributes": {"safe": "fallback"}}

    failing_engine = _Engine(
        lambda _query, _params: (_ for _ in ()).throw(RuntimeError("db"))
    )
    monkeypatch.setattr(doc_metadata, "engine", failing_engine)
    assert doc_metadata.get_document_metadata(8) == {"attributes": {"safe": "fallback"}}
    assert doc_metadata.get_document_metadata(None) == {}


def test_set_document_attributes_replaces_selected_extractor_and_filters_values(monkeypatch):
    engine = _Engine(lambda _query, _params: _Result())
    monkeypatch.setattr(doc_metadata, "engine", engine)

    assert doc_metadata.set_document_attributes(
        7,
        None,
        {"material": " steel ", "empty": " ", "number": 4},
        extracted_by="reviewer",
    ) is True

    assert len(engine.calls) == 3
    assert engine.calls[0][1] == {"d": 7, "by": "reviewer"}
    assert engine.calls[1][1] == {
        "d": 7,
        "dom": "generic",
        "k": "material",
        "v": "steel",
        "by": "reviewer",
    }
    assert engine.calls[2][1]["v"] == "4"


def test_set_document_attributes_rejects_empty_id_and_fails_closed(monkeypatch):
    assert doc_metadata.set_document_attributes(None, "x", {}) is False
    engine = _Engine(lambda _query, _params: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(doc_metadata, "engine", engine)

    assert doc_metadata.set_document_attributes(7, "x", {"a": "b"}) is False


def test_update_common_metadata_updates_all_boundaries_and_survives_qdrant_failure(monkeypatch):
    engine = _Engine(lambda _query, _params: _Result())
    monkeypatch.setattr(doc_metadata, "engine", engine)
    attribute_calls = []
    monkeypatch.setattr(
        doc_metadata,
        "set_document_attributes",
        lambda *args, **kwargs: attribute_calls.append((args, kwargs)) or True,
    )
    qdrant_calls = []

    def qdrant_failure(*args):
        qdrant_calls.append(args)
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(doc_metadata._r_qdrant, "update_qdrant_metadata", qdrant_failure)
    audit_calls = []
    monkeypatch.setattr(
        doc_metadata._r_audit,
        "write_audit_log",
        lambda *args: audit_calls.append(args),
    )
    invalidations = []
    monkeypatch.setattr(
        doc_metadata._r_semantic_cache,
        "_invalidate_semantic_cache",
        invalidations.append,
    )

    assert doc_metadata.update_document_common_metadata(
        7,
        reviewer="alice",
        attributes={"material": "steel"},
        domain="mechanical",
        title="  Drawing  ",
        summary=" ",
        tags=None,
        taxonomy_version=" v2 ",
        parent_section=" Procedure ",
        parent_page="3",
    ) is True

    query, params = engine.calls[0]
    assert "Title = :title" in query
    assert "Summary = :summary" in query
    assert "Tags" not in query
    assert "ParentApplicable = 1" in query
    assert params == {
        "d": 7,
        "title": "Drawing",
        "summary": None,
        "taxonomy_version": "v2",
        "parent_section": "Procedure",
        "parent_page": 3,
    }
    assert attribute_calls == [((7, "mechanical", {"material": "steel"}), {"extracted_by": "manual"})]
    assert qdrant_calls == [
        (7, {"title": "Drawing", "taxonomy_version": "v2", "parent_section": "Procedure", "parent_page": 3})
    ]
    assert audit_calls[0][:4] == ("alice", "update_common_metadata", "TaiLieu", 7)
    assert invalidations == ["doc.metadata"]


def test_update_common_metadata_supports_clear_values_and_attribute_only_update(monkeypatch):
    engine = _Engine(lambda _query, _params: _Result())
    monkeypatch.setattr(doc_metadata, "engine", engine)
    monkeypatch.setattr(doc_metadata, "set_document_attributes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(doc_metadata._r_qdrant, "update_qdrant_metadata", lambda *_args: True)
    monkeypatch.setattr(doc_metadata._r_audit, "write_audit_log", lambda *_args: None)
    monkeypatch.setattr(doc_metadata._r_semantic_cache, "_invalidate_semantic_cache", lambda *_args: None)

    assert doc_metadata.update_document_common_metadata(
        3,
        attributes={},
        knowledge_owner_user_id="",
        knowledge_approver_user_id=8,
        parent_applicable=False,
    ) is True
    assert engine.calls[0][1] == {
        "d": 3,
        "knowledge_owner_user_id": None,
        "knowledge_approver_user_id": 8,
        "parent_applicable": 0,
    }

    engine.calls.clear()
    assert doc_metadata.update_document_common_metadata(3, attributes={}) is True
    assert engine.calls == []


def test_update_common_metadata_accepts_non_string_common_value_and_skips_none_governance(monkeypatch):
    engine = _Engine(lambda _query, _params: _Result())
    monkeypatch.setattr(doc_metadata, "engine", engine)
    monkeypatch.setattr(doc_metadata._r_qdrant, "update_qdrant_metadata", lambda *_args: True)
    monkeypatch.setattr(doc_metadata._r_audit, "write_audit_log", lambda *_args: None)
    monkeypatch.setattr(doc_metadata._r_semantic_cache, "_invalidate_semantic_cache", lambda *_args: None)

    assert doc_metadata.update_document_common_metadata(
        3,
        issued_date=20260720,
        knowledge_owner_user_id=None,
    ) is True

    assert engine.calls[0][1] == {"d": 3, "issued_date": 20260720}


@pytest.mark.parametrize(
    "fields",
    [
        {"knowledge_owner_user_id": "not-int"},
        {"parent_page": 0},
        {"taxonomy_version": " "},
    ],
)
def test_update_common_metadata_rejects_invalid_governance_values(
    monkeypatch, fields
):
    engine = _Engine(lambda _query, _params: _Result())
    monkeypatch.setattr(doc_metadata, "engine", engine)

    assert doc_metadata.update_document_common_metadata(5, **fields) is False
    assert engine.calls == []


def test_update_common_metadata_rejects_empty_id_and_database_failure(monkeypatch):
    assert doc_metadata.update_document_common_metadata(None, title="A") is False
    engine = _Engine(lambda _query, _params: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(doc_metadata, "engine", engine)

    assert doc_metadata.update_document_common_metadata(8, title="A") is False


def test_get_common_metadata_for_rag_normalizes_ids_and_maps_rows(monkeypatch):
    rows = [
        (7, "Title", "Summary", "tag", "DOC-7", "i", "e", "x", "owner", "active", "vi"),
        (8, None, None, None, None, None, None, None, None, None, None),
    ]
    engine = _Engine(lambda _query, _params: _Result(rows=rows))
    monkeypatch.setattr(doc_metadata, "engine", engine)

    result = doc_metadata.get_common_metadata_for_rag(["7", 8, "7", "bad", None])

    assert result[7] == {
        "title": "Title",
        "summary": "Summary",
        "tags": "tag",
        "doc_number": "DOC-7",
        "issued_date": "i",
        "effective_date": "e",
        "expiry_date": "x",
        "owner_signer": "owner",
        "effective_status": "active",
        "language": "vi",
    }
    assert result[8]["title"] is None
    query, params = engine.calls[0]
    assert "IN (:id0, :id1)" in query
    assert params == {"id0": 7, "id1": 8}


def test_get_common_metadata_for_rag_handles_empty_engine_and_query_failure(monkeypatch):
    assert doc_metadata.get_common_metadata_for_rag([]) == {}
    monkeypatch.setattr(doc_metadata, "engine", None)
    assert doc_metadata.get_common_metadata_for_rag([1]) == {}

    engine = _Engine(lambda _query, _params: (_ for _ in ()).throw(RuntimeError("db")))
    monkeypatch.setattr(doc_metadata, "engine", engine)
    assert doc_metadata.get_common_metadata_for_rag([1]) == {}
