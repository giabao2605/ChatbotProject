from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit

document = pytest.importorskip("mech_chatbot.db.repositories.document")
feedback_repository = pytest.importorskip("mech_chatbot.db.repositories.feedback")
semantic_cache_repository = pytest.importorskip("mech_chatbot.db.repositories.semantic_cache")


class _Result:
    def __init__(self, rows=(), *, rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def mappings(self):
        return self

    def first(self):
        return self.fetchone()


class _Connection:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        return self.handler(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection


@pytest.fixture
def install_engine(monkeypatch):
    def _install(handler):
        connection = _Connection(handler)
        monkeypatch.setattr(document, "engine", _Engine(connection))
        monkeypatch.setattr(document, "_ensure_engine", lambda: None)
        return connection

    return _install


def test_department_assignment_replaces_the_shared_department_set(install_engine):
    connection = install_engine(lambda _sql, _params: _Result())

    document.set_document_departments(connection, 17, "QA, PROD, QA")

    assert len(connection.calls) == 2
    assert "DELETE FROM dbo.PhongBanChiaSe" in connection.calls[0][0]
    assert connection.calls[0][1] == {"d": 17}
    assert "INSERT INTO dbo.PhongBanChiaSe" in connection.calls[1][0]
    assert connection.calls[1][1] == [
        {"d": 17, "c": "QA"},
        {"d": 17, "c": "PROD"},
    ]


def test_create_document_inherits_classification_and_department_governance(
    install_engine, monkeypatch
):
    from mech_chatbot.db import registry_ports

    classification = {
        "base_code": "doc 001",
        "version_no": 2,
        "version_label": "B",
        "variant_code": "large",
        "domain": "quality",
        "security_level": "confidential",
        "classification_model": "rules-v2",
        "classification_rationale": "content match",
    }

    def handle(sql, _params):
        if "FROM dbo.IngestionJobs" in sql:
            return _Result(
                [
                    (
                        json.dumps(classification),
                        "C:/uploads/drawing.pdf",
                        '{"title":"Drawing"}',
                        "HCM",
                    )
                ]
            )
        if "FROM dbo.DepartmentKnowledgeGovernance" in sql:
            return _Result(
                [
                    {
                        "KnowledgeOwnerUserID": 11,
                        "KnowledgeApproverUserID": 12,
                        "TaxonomyVersion": "v4",
                        "ExternalProcessingPolicy": "no_external",
                    }
                ]
            )
        if "SELECT FamilyID FROM DocumentFamily" in sql:
            return _Result()
        if "INSERT INTO DocumentFamily" in sql:
            return _Result([(7,)])
        if "SELECT DocID, LifecycleStatus" in sql:
            return _Result()
        if "INSERT INTO TaiLieu" in sql:
            return _Result([(42,)])
        return _Result()

    connection = install_engine(handle)
    metadata_applications = []
    monkeypatch.setattr(registry_ports, "resolve_domain_by_department", lambda _dept: "fallback")
    monkeypatch.setattr(
        registry_ports,
        "resolve_security_by_department",
        lambda _dept: "internal",
    )
    monkeypatch.setattr(
        document._r_doc_metadata,
        "_apply_upload_meta_to_doc",
        lambda *args: metadata_applications.append(args),
    )

    doc_id = document._get_or_create_doc(connection, "drawing.pdf", "QA")

    assert doc_id == 42
    insert_params = next(
        params for sql, params in connection.calls if "INSERT INTO TaiLieu" in sql
    )
    assert insert_params["bc"] == "doc-001"
    assert insert_params["fid"] == 7
    assert insert_params["domain"] == "quality"
    assert insert_params["seclvl"] == "confidential"
    assert insert_params["site"] == "HCM"
    assert insert_params["knowledge_owner_user_id"] == 11
    assert insert_params["knowledge_approver_user_id"] == 12
    assert insert_params["taxonomy_version"] == "v4"
    assert insert_params["external_processing_policy"] == "no_external"
    assert insert_params["fp"] == "C:/uploads/drawing.pdf"
    assert metadata_applications == [
        (connection, 42, '{"title":"Drawing"}', "quality")
    ]


def test_published_document_cannot_be_reingested(install_engine, monkeypatch):
    from mech_chatbot.db import registry_ports

    def handle(sql, _params):
        if "FROM dbo.IngestionJobs" in sql:
            return _Result()
        if "FROM dbo.DepartmentKnowledgeGovernance" in sql:
            return _Result()
        if "SELECT DocID, LifecycleStatus" in sql:
            return _Result([(42, "published", "approved", 1)])
        return _Result()

    connection = install_engine(handle)
    monkeypatch.setattr(registry_ports, "resolve_domain_by_department", lambda _dept: "quality")
    monkeypatch.setattr(
        registry_ports,
        "resolve_security_by_department",
        lambda _dept: "internal",
    )
    monkeypatch.setattr(document._r_catalog, "_resolve_site", lambda _dept: "HCM")

    with pytest.raises(ValueError, match="published"):
        document._get_or_create_doc(connection, "drawing.pdf", "QA")

    assert not any("UPDATE TaiLieu SET" in sql for sql, _ in connection.calls)


def test_reingest_resets_a_draft_to_review_with_safe_classification_defaults(
    install_engine, monkeypatch
):
    from mech_chatbot.db import registry_ports

    def handle(sql, _params):
        if "FROM dbo.IngestionJobs" in sql:
            return _Result([("not-json", "C:/uploads/drawing.pdf", None, None)])
        if "FROM dbo.DepartmentKnowledgeGovernance" in sql:
            return _Result()
        if "SELECT DocID, LifecycleStatus" in sql:
            return _Result([(42, "draft", "rejected", 0)])
        return _Result()

    connection = install_engine(handle)
    metadata_applications = []
    monkeypatch.setattr(registry_ports, "resolve_domain_by_department", lambda _dept: "quality")
    monkeypatch.setattr(
        registry_ports,
        "resolve_security_by_department",
        lambda _dept: "internal",
    )
    monkeypatch.setattr(document._r_catalog, "_resolve_site", lambda _dept: "HCM")
    monkeypatch.setattr(
        document._r_doc_metadata,
        "_apply_upload_meta_to_doc",
        lambda *args: metadata_applications.append(args),
    )

    assert document._get_or_create_doc(connection, "drawing.pdf", "QA") == 42

    update_params = next(
        params for sql, params in connection.calls if "UPDATE TaiLieu SET" in sql
    )
    assert update_params["d"] == 42
    assert update_params["bc"] is None
    assert update_params["vc"] == "default"
    assert update_params["domain"] == "quality"
    assert update_params["seclvl"] == "internal"
    assert update_params["taxonomy_version"] == "v1"
    assert update_params["external_processing_policy"] == "internal_only"
    assert metadata_applications == [(connection, 42, None, "quality")]


def test_department_assignment_ignores_an_unknown_document(install_engine):
    connection = install_engine(lambda _sql, _params: _Result())

    document.set_document_departments(connection, None, ["QA"])

    assert connection.calls == []


def test_document_departments_return_ordered_database_values(install_engine):
    connection = install_engine(
        lambda sql, _params: _Result([("PROD",), ("QA",)])
        if "SELECT DeptCode" in sql
        else _Result()
    )

    assert document.get_document_departments(17) == ["PROD", "QA"]
    assert connection.calls[0][1] == {"d": 17}


def test_document_departments_fail_closed_when_the_database_is_unavailable(install_engine):
    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)

    assert document.get_document_departments(17) == []
    assert document.get_document_departments(None) == []


def test_classification_update_persists_fields_and_invalidates_cached_answers(
    install_engine, monkeypatch
):
    connection = install_engine(lambda _sql, _params: _Result())
    invalidations = []
    monkeypatch.setattr(
        document._r_semantic_cache,
        "_invalidate_semantic_cache",
        invalidations.append,
    )

    result = document.update_document_classification(
        21,
        domain="quality",
        security_level="confidential",
        phong_ban=["QA", "PROD"],
    )

    update_sql, update_params = connection.calls[0]
    assert "Domain = :domain" in update_sql
    assert "SecurityLevel = :seclvl" in update_sql
    assert update_params == {
        "d": 21,
        "domain": "quality",
        "seclvl": "confidential",
    }
    assert connection.calls[-1][1] == [
        {"d": 21, "c": "QA"},
        {"d": 21, "c": "PROD"},
    ]
    assert invalidations == ["doc.classification"]
    assert result is True


def test_classification_update_is_a_noop_without_a_document_or_change(
    install_engine, monkeypatch
):
    connection = install_engine(lambda _sql, _params: _Result())
    invalidations = []
    monkeypatch.setattr(
        document._r_semantic_cache,
        "_invalidate_semantic_cache",
        invalidations.append,
    )

    assert document.update_document_classification(None, domain="quality") is False
    assert document.update_document_classification(21) is True

    assert connection.calls == []
    assert invalidations == []


def test_classification_update_can_change_only_shared_departments(
    install_engine, monkeypatch,
) -> None:
    connection = install_engine(lambda _sql, _params: _Result())
    invalidations = []
    monkeypatch.setattr(
        document._r_semantic_cache,
        "_invalidate_semantic_cache",
        invalidations.append,
    )

    assert document.update_document_classification(21, phong_ban=["QA"]) is True

    assert not any("UPDATE TaiLieu SET" in sql for sql, _ in connection.calls)
    assert any("INSERT INTO dbo.PhongBanChiaSe" in sql for sql, _ in connection.calls)
    assert invalidations == ["doc.classification"]


def test_classification_update_reports_storage_failure(install_engine) -> None:
    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)

    assert (
        document.update_document_classification(
            21,
            security_level="confidential",
            phong_ban=["QA"],
        )
        is False
    )


@pytest.mark.parametrize(
    ("raw_codes", "expected_codes"),
    [
        ("ASM-01", ["ASM-01"]),
        ("Khong ro", []),
        (None, []),
    ],
)
def test_metadata_params_normalize_scalar_document_codes(
    raw_codes, expected_codes,
) -> None:
    params = document._prepare_metadata_params(
        {"ma_doi_tuong": raw_codes, "loai_tai_lieu": "Khong ro"}
    )

    assert json.loads(params["ma_doi_tuong"]) == expected_codes


def test_mark_ingest_failed_removes_partial_data_and_rejects_the_document(install_engine):
    connection = install_engine(
        lambda sql, _params: _Result([(42, "draft", "pending_review")])
        if "SELECT DocID" in sql
        else _Result()
    )

    document.mark_document_ingest_failed("drawing.pdf", "QA", "invalid pages")

    sql_text = "\n".join(sql for sql, _ in connection.calls)
    assert "DELETE FROM TaiLieuKyThuat" in sql_text
    assert "DELETE FROM BangKeVatTu" in sql_text
    assert "DELETE FROM DocumentPages" in sql_text
    assert "DELETE FROM TechnicalAttributes" in sql_text
    assert "LifecycleStatus = 'rejected'" in sql_text
    assert connection.calls[-1][1] == {"d": 42, "msg": "invalid pages"}


def test_mark_ingest_failed_does_not_touch_data_when_document_is_missing(install_engine):
    connection = install_engine(lambda _sql, _params: _Result())

    document.mark_document_ingest_failed("missing.pdf", "QA")

    assert len(connection.calls) == 1
    assert "SELECT DocID" in connection.calls[0][0]


def test_mark_ingest_failed_preserves_published_document(install_engine):
    connection = install_engine(
        lambda sql, _params: _Result([(42, "published", "approved")])
        if "SELECT DocID" in sql else _Result()
    )

    document.mark_document_ingest_failed("drawing.pdf", "QA", "duplicate upload")

    assert len(connection.calls) == 1


def test_document_info_maps_governance_and_classification_fields(install_engine):
    row = (
        7,
        "DOC-01",
        3,
        "C",
        "large",
        "size",
        "published",
        "approved",
        1,
        0,
        5,
        "published",
        1,
        9,
        "QA",
        "upload",
        "no_external",
        11,
        12,
        "v4",
        1,
        "Scope",
        8,
        19,
        1,
        '{"document_type":"procedure","classification_failed":true}',
        "Assembly procedure",
        "P-001",
    )
    install_engine(lambda _sql, _params: _Result([row]))

    info = document.get_document_info(55)

    assert info == {
        "family_id": 7,
        "base_code": "DOC-01",
        "version_no": 3,
        "version_label": "C",
        "variant_code": "large",
        "variant_group": "size",
        "lifecycle_status": "published",
        "review_status": "approved",
        "is_current": True,
        "is_archived": False,
        "supersedes_doc_id": 5,
        "publication_state": "published",
        "servable": True,
        "publication_version": 9,
        "owner_department": "QA",
        "source_system": "upload",
        "external_processing_policy": "no_external",
        "knowledge_owner_user_id": 11,
        "knowledge_approver_user_id": 12,
        "taxonomy_version": "v4",
        "parent_applicable": True,
        "parent_section": "Scope",
        "parent_page": 8,
        "serving_epoch": 19,
        "parent_context_enabled": True,
        "document_type": "procedure",
        "classification_failed": True,
        "title": "Assembly procedure",
        "doc_number": "P-001",
    }


@pytest.mark.parametrize("row", [None, (None,) * 25 + ("not-json", None, None)])
def test_document_info_returns_safe_defaults_for_missing_or_invalid_rows(
    install_engine, row
):
    install_engine(lambda _sql, _params: _Result([] if row is None else [row]))

    info = document.get_document_info(999)

    if row is None:
        assert info == {}
    else:
        assert info["document_type"] == ""
        assert info["classification_failed"] is False
        assert info["base_code"] == ""
        assert info["lifecycle_status"] == "draft"
        assert info["external_processing_policy"] == "internal_only"


def test_document_info_fails_closed_on_database_error(install_engine):
    def fail(_sql, _params):
        raise RuntimeError("database unavailable")

    install_engine(fail)

    assert document.get_document_info(55) == {}


def test_document_lookup_maps_row_and_current_query_scope(install_engine):
    def handle(sql, params):
        if "SELECT DocID FROM TaiLieu" in sql:
            assert "VariantCode = :v" in sql
            assert params == {"b": "DOC-01", "v": "large"}
            return _Result([(3,), (4,)])
        if "WHERE DocID = :d" in sql:
            doc_id = params["d"]
            return _Result(
                [(doc_id, 7, "DOC-01", 2, "large", "published", doc_id == 3)]
            )
        return _Result()

    install_engine(handle)

    docs = document.find_current_docs("DOC-01", "large")

    assert [item.DocID for item in docs] == [3, 4]
    assert docs[0].BaseCode == "DOC-01"
    assert docs[0].IsCurrent is True
    assert docs[1].IsCurrent is False


def test_document_lookup_without_variant_and_missing_document(install_engine):
    def handle(sql, params):
        if "SELECT DocID FROM TaiLieu" in sql:
            assert "VariantCode = :v" not in sql
            assert params == {"b": "DOC-01"}
        return _Result()

    install_engine(handle)

    assert document.find_current_docs("DOC-01") == []
    assert document.get_doc(404) is None


def test_missing_document_cannot_be_deleted(install_engine):
    install_engine(lambda _sql, _params: _Result())

    assert document.delete_document_completely(404) is False


def test_qdrant_failure_rolls_back_the_soft_delete(install_engine, monkeypatch):
    connection = install_engine(
        lambda sql, _params: _Result([("drawing.pdf", "QA", "published", "qa/drawing.pdf", 0)])
        if "SELECT TenFile" in sql
        else _Result([(10,)])
        if "SELECT TOP 2 DocID" in sql
        else _Result([(42, "published", None)])
        if "SELECT TOP 2 JobID" in sql
        else _Result()
    )

    class FailingClient:
        def delete(self, **_kwargs):
            raise RuntimeError("qdrant unavailable")

    assert document.delete_document_completely(
        10,
        qdrant_client=FailingClient(),
        collection_name="test-knowledge",
    ) is False
    updates = [(sql, params) for sql, params in connection.calls if "UPDATE TaiLieu" in sql]
    assert "LifecycleStatus = 'deleting'" in updates[0][0]
    assert updates[-1][1] == {"s": "published", "current": 0, "id": 10}


def test_successful_delete_cleans_all_stores_and_audits(install_engine, monkeypatch):
    connection = install_engine(
        lambda sql, _params: _Result(
            [("drawing.pdf", "QA", "published", "qa/drawing.pdf", 1)]
            if "SELECT TenFile" in sql
            else [(10,)]
            if "SELECT TOP 2 DocID" in sql
            else [(42, "published", "worker-1")]
            if "SELECT TOP 2 JobID" in sql
            else [(0,)]
            if "KnowledgeGraphNode" in sql
            else [("missing-page.png",)]
            if "SELECT ImagePath" in sql
            else []
        )
    )
    qdrant_deletes = []
    audit_calls = []
    removed_images = []
    qdrant_client = SimpleNamespace(
        delete=lambda **kwargs: qdrant_deletes.append(kwargs)
    )
    monkeypatch.setattr(feedback_repository, "cleanup_dangling_records", lambda: pytest.fail("global feedback cleanup"))
    monkeypatch.setattr(semantic_cache_repository, "sc_clear_all", lambda: pytest.fail("global cache clear"))
    monkeypatch.setattr(
        document._r_audit,
        "write_audit_log",
        lambda *args: audit_calls.append(args),
    )
    monkeypatch.setattr(document.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(document.os, "remove", removed_images.append)

    assert document.delete_document_completely(
        10,
        reviewer="alice",
        qdrant_client=qdrant_client,
        collection_name="test-knowledge",
    ) is True

    sql_text = "\n".join(sql for sql, _ in connection.calls)
    assert "LifecycleStatus = 'deleting'" in sql_text
    assert "DELETE FROM DocumentPages" in sql_text
    assert "DELETE FROM TechnicalAttributes" in sql_text
    assert "DELETE FROM DocumentAttributes" in sql_text
    assert "DELETE FROM TaiLieu" in sql_text
    assert "DELETE FROM dbo.IngestionJobs WHERE JobID = :job_id" in sql_text
    assert "DELETE FROM dbo.IngestionJobs WHERE TenFile" not in sql_text
    assert "UPDATE GoldenAnswer SET SourceDocID = NULL WHERE SourceDocID = :id" in sql_text
    assert "UPDATE FeedbackReview SET SourceDocID = NULL WHERE SourceDocID = :id" in sql_text
    assert "UPDATE AnswerSource SET DocID = NULL WHERE DocID = :id" in sql_text
    assert "UPDATE dbo.ChatEvidenceManifest SET IsComplete = 0" in sql_text
    assert "DELETE FROM dbo.AnswerEvidence WHERE DocID = :id" in sql_text
    assert "UPDATE RegressionQuestion SET ExpectedDocID = NULL WHERE ExpectedDocID = :id" in sql_text
    assert all(
        params.get("id") == 10 or params.get("job_id") == 42
        for sql, params in connection.calls
        if sql.startswith(("UPDATE GoldenAnswer", "UPDATE FeedbackReview", "UPDATE AnswerSource", "UPDATE RegressionQuestion", "DELETE FROM dbo.IngestionJobs", "DELETE FROM dbo.AnswerEvidence"))
    )
    assert len(qdrant_deletes) == 1
    assert removed_images == ["missing-page.png"]
    assert audit_calls == [
        (
            "alice",
            "delete_document",
            "TaiLieu",
            10,
            {"ten_file": "drawing.pdf", "thu_muc": "QA"},
        )
    ]


@pytest.mark.parametrize("job_rows,doc_rows", [([], [(10,)]), ([(42, "published", None), (43, "published", None)], [(10,)]), ([(42, "published", None)], [(10,), (11,)])])
def test_delete_fails_closed_when_document_job_link_is_not_unique(install_engine, job_rows, doc_rows):
    connection = install_engine(
        lambda sql, _params: _Result(
            [("drawing.pdf", "QA", "published", "qa/drawing.pdf", 1)]
            if "SELECT TenFile" in sql
            else doc_rows
            if "SELECT TOP 2 DocID" in sql
            else job_rows
            if "SELECT TOP 2 JobID" in sql
            else []
        )
    )

    assert document.delete_document_completely(10, qdrant_client=SimpleNamespace(delete=lambda **_kw: None), collection_name="test-knowledge") is False
    assert not any(sql.startswith(("UPDATE", "DELETE")) for sql, _ in connection.calls)


def test_delete_removes_only_the_linked_upload_file(install_engine, monkeypatch, tmp_path):
    upload_dir = tmp_path / "Uploads" / "QA"
    upload_dir.mkdir(parents=True)
    target = upload_dir / "target.pdf"
    other = upload_dir / "other.pdf"
    target.write_bytes(b"target")
    other.write_bytes(b"other")
    connection = install_engine(
        lambda sql, _params: _Result(
            [("target.pdf", "QA", "published", str(target), 1)]
            if "SELECT TenFile" in sql
            else [(10,)]
            if "SELECT TOP 2 DocID" in sql
            else [(42, "published", None)]
            if "SELECT TOP 2 JobID" in sql
            else []
        )
    )
    monkeypatch.setattr(document._r_audit, "write_audit_log", lambda *_args, **_kwargs: None)

    assert document.delete_document_completely(
        10,
        qdrant_client=SimpleNamespace(delete=lambda **_kw: None),
        collection_name="test-knowledge",
        raw_root=lambda: tmp_path,
    ) is True
    assert not target.exists()
    assert other.read_bytes() == b"other"
    assert any("DELETE FROM dbo.IngestionJobs WHERE JobID" in sql for sql, _ in connection.calls)


def test_delete_rejects_source_outside_upload_root(install_engine, tmp_path):
    outside = tmp_path / "unrelated.pdf"
    outside.write_bytes(b"preserve")
    connection = install_engine(
        lambda sql, _params: _Result([("unrelated.pdf", "QA", "published", str(outside), 1)])
        if "SELECT TenFile" in sql else _Result()
    )

    assert document.delete_document_completely(10, raw_root=lambda: tmp_path) is False
    assert outside.read_bytes() == b"preserve"
    assert not any(sql.startswith(("UPDATE", "DELETE")) for sql, _ in connection.calls)


def test_delete_with_protected_references_stops_before_qdrant_or_sql_mutation(install_engine):
    connection = install_engine(
        lambda sql, _params: _Result(
            [("drawing.pdf", "QA", "published", "qa/drawing.pdf", 1)]
            if "SELECT TenFile" in sql
            else [(10,)]
            if "SELECT TOP 2 DocID" in sql
            else [(42, "published", None)]
            if "SELECT TOP 2 JobID" in sql
            else [(1,)]
            if "KnowledgeGraphNode" in sql
            else []
        )
    )
    qdrant_deletes = []

    assert document.delete_document_completely(
        10,
        qdrant_client=SimpleNamespace(delete=lambda **kwargs: qdrant_deletes.append(kwargs)),
        collection_name="test-knowledge",
    ) is False
    assert qdrant_deletes == []
    assert not any(sql.startswith(("UPDATE", "DELETE")) for sql, _ in connection.calls)
