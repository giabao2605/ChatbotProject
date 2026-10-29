"""Source-bound real SQL candidates, lookup and HTTP review regression checks."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

from mech_chatbot.adapters.document_runtime import SqlDocumentLookup
from mech_chatbot.api.dependencies import csrf_profile, current_profile
from mech_chatbot.api.routers import documents
from mech_chatbot.application.document_review import PublicationCoordinator, PublicationOutcome, ReviewDocuments
from mech_chatbot.db.repositories import ui_queries


pytestmark = pytest.mark.unit


@pytest.fixture
def source_pair(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def sqlite_dialect(_conn, _cursor, statement, parameters, _context, _many):
        # Adapt SQL Server syntax only; execute every source-identity join unchanged.
        has_top = "SELECT TOP 1 " in statement
        statement = statement.replace("dbo.", "").replace("SELECT TOP 1 ", "SELECT ")
        return statement + (" LIMIT 1" if has_top else ""), parameters

    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IngestionJobs (JobID INTEGER PRIMARY KEY, TenFile TEXT, ThuMuc TEXT, "
            "FilePath TEXT, Status TEXT, UpdatedAt TEXT, UploadedBy TEXT, SecurityLevel TEXT, Site TEXT, "
            "ExtractionReport TEXT, Domain TEXT, UploadMetaJson TEXT)"
        ))
        connection.execute(text(
            "CREATE TABLE TaiLieu (DocID INTEGER PRIMARY KEY, TenFile TEXT, ThuMuc TEXT, "
            "FilePath TEXT, LifecycleStatus TEXT, SecurityLevel TEXT, Site TEXT, ReviewStatus TEXT, "
            "PublicationState TEXT, Title TEXT, Summary TEXT, Tags TEXT, DocNumber TEXT, DocLanguage TEXT, "
            "IssuedDate TEXT, EffectiveDate TEXT, ExpiryDate TEXT, ReviewDate TEXT, OwnerSigner TEXT, "
            "EffectiveStatus TEXT, VersionNo INTEGER, IsCurrent INTEGER, VariantGroup TEXT, VariantCode TEXT)"
        ))
        connection.execute(text(
            "INSERT INTO IngestionJobs (JobID,TenFile,ThuMuc,FilePath,Status,UpdatedAt,UploadedBy,SecurityLevel,Site) VALUES "
            "(1156,'same.md','QA','data/raw/Uploads/QA/uuid-a_same.md','pending_review','2026-09-30','uploader','internal','QA'),"
            "(1157,'same.md','QA','data/raw/Uploads/QA/uuid-b_same.md','failed','2026-09-30','uploader','internal','QA')"
        ))
        connection.execute(text(
            "INSERT INTO TaiLieu (DocID,TenFile,ThuMuc,FilePath,LifecycleStatus,SecurityLevel,Site,ReviewStatus,PublicationState,Title) VALUES "
            "(1164,'same.md','QA','data/raw/Uploads/QA/uuid-a_same.md','draft','internal','QA','pending_review','draft','Source A title')"
        ))
    monkeypatch.setattr(ui_queries, "engine", engine)
    monkeypatch.setattr(documents, "validate_publish_actor", lambda *_args: SimpleNamespace(valid=True))
    mutation_calls = []

    def mutate(statement, identifier):
        mutation_calls.append((statement, identifier))
        with engine.begin() as conn:
            return conn.execute(text(statement), {"id": identifier}).rowcount > 0

    def publish(command, _actor):
        mutate("UPDATE TaiLieu SET ReviewStatus='approved',PublicationState='published' WHERE DocID=:id", command.doc_id)
        return PublicationOutcome(True, "published", {"ok": True, "state": "published"})

    lookup = SqlDocumentLookup(engine=engine)
    port = SimpleNamespace(
        resolve_latest_doc_id=lookup.latest_doc_id_for_job, publish_document=publish,
        mark_job_published=lambda identifier: mutate("UPDATE IngestionJobs SET Status='published' WHERE JobID=:id", identifier),
    )
    coordinator = PublicationCoordinator(publication=port)
    store = SimpleNamespace(
        reject_job=lambda identifier, _reason: mutate("UPDATE IngestionJobs SET Status='rejected' WHERE JobID=:id", identifier),
        mark_job_rejected=lambda identifier: mutate("UPDATE IngestionJobs SET Status='rejected' WHERE JobID=:id", identifier),
        reject_document=lambda identifier, **_kwargs: mutate("UPDATE TaiLieu SET ReviewStatus='rejected' WHERE DocID=:id", identifier),
        delete_document=lambda identifier, **_kwargs: mutate("DELETE FROM TaiLieu WHERE DocID=:id", identifier),
        delete_job=lambda identifier: mutate("DELETE FROM IngestionJobs WHERE JobID=:id", identifier),
    )
    runtime = SimpleNamespace(publication_port=port, publication_coordinator=coordinator, mutation_calls=mutation_calls,
        review_documents=ReviewDocuments(review_store=store, publication=coordinator))
    profile = {"user_id": 100, "username": "reviewer", "roles": ["reviewer"], "allowed_departments": ["QA"]}
    yield engine, lookup, runtime, profile
    engine.dispose()


def state(engine):
    with engine.connect() as conn:
        return {table: [tuple(row) for row in conn.execute(text(f"SELECT * FROM {table}"))]
                for table in ("IngestionJobs", "TaiLieu")}


def client_for(runtime, profile):
    app = FastAPI()
    app.include_router(documents.router)
    app.state.runtime = runtime
    app.dependency_overrides[csrf_profile] = lambda: profile
    app.dependency_overrides[current_profile] = lambda: profile
    return TestClient(app)


@pytest.mark.parametrize("job_id", [None, 1157])
def test_same_name_failed_upload_does_not_map_to_sibling_document(source_pair, job_id):
    rows = [dict(row._mapping) for row in ui_queries.list_bulk_action_jobs(job_id=job_id)]
    if job_id is None:
        assert next(row for row in rows if row["JobID"] == 1156)["DocID"] == 1164
    assert [row["DocID"] for row in rows if row["JobID"] == 1157] == [None]


@pytest.mark.parametrize("unknown_path", [None, "", "   ", "data/raw/Uploads/QA/uuid-b_same.md"])
def test_lookup_does_not_fallback_to_filename_for_unknown_source(source_pair, unknown_path):
    engine, lookup, _runtime, _profile = source_pair
    with engine.begin() as conn:
        conn.execute(text("UPDATE IngestionJobs SET FilePath=:path WHERE JobID=1157"), {"path": unknown_path})
    assert lookup.latest_doc_id_for_job(1156) == 1164
    assert lookup.latest_doc_id_for_job(1157) is None
    assert lookup.latest_doc_id_for_job(9999) is None


def test_pending_review_preview_does_not_borrow_sibling_title(source_pair):
    engine, _lookup, _runtime, _profile = source_pair
    with engine.begin() as conn:
        conn.execute(text("UPDATE IngestionJobs SET Status='pending_review' WHERE JobID=1157"))
    rows = {row.JobID: dict(row._mapping) for row in ui_queries.list_pending_review_docs()}
    assert rows[1156]["DocID"] == 1164 and rows[1156]["Title"] == "Source A title"
    assert rows[1157]["DocID"] is None and rows[1157]["Title"] is None


@pytest.mark.parametrize("job_path,document_path,expected_doc_id", [
    ("42cc4c4c8cc646a4933717c8290cb126.pdf", "42cc4c4c8cc646a4933717c8290cb126.pdf", 1164),
    (r"C:\raw\Uploads\QA\42cc4c4c8cc646a4933717c8290cb126.pdf",
     r"C:\raw\Uploads\QA\42cc4c4c8cc646a4933717c8290cb126.pdf", 1164),
    ("42cc4c4c8cc646a4933717c8290cb126.pdf", r"C:\raw\Uploads\QA\42cc4c4c8cc646a4933717c8290cb126.pdf", None),
    (r"C:\wrong\42cc4c4c8cc646a4933717c8290cb126.pdf", r"C:\raw\Uploads\QA\42cc4c4c8cc646a4933717c8290cb126.pdf", None),
    ("../QA/42cc4c4c8cc646a4933717c8290cb126.pdf", "42cc4c4c8cc646a4933717c8290cb126.pdf", None),
    (None, None, None),
    ("", "", None),
    ("   ", "   ", None),
])
def test_source_join_requires_same_nonempty_stored_path(source_pair, job_path, document_path, expected_doc_id):
    engine, lookup, _runtime, _profile = source_pair
    with engine.begin() as conn:
        conn.execute(text("UPDATE IngestionJobs SET FilePath=:path WHERE JobID=1156"), {"path": job_path})
        conn.execute(text("UPDATE TaiLieu SET FilePath=:path WHERE DocID=1164"), {"path": document_path})
    assert lookup.latest_doc_id_for_job(1156) == expected_doc_id
    assert ui_queries.list_bulk_action_jobs(job_id=1156)[0].DocID == expected_doc_id
    rows = {row.JobID: row for row in ui_queries.list_pending_review_docs()}
    assert rows[1156].DocID == expected_doc_id


@pytest.mark.parametrize("action", ["reject", "delete", "publish"])
def test_bulk_http_forged_pair_preserves_sql_rows(source_pair, action):
    engine, _lookup, runtime, profile = source_pair
    before = state(engine)
    with client_for(runtime, profile) as client:
        response = client.post("/api/documents/review/bulk", json={"action": action, "items": [{"job_id": 1157, "doc_id": 1164}]})
    assert state(engine) == before
    assert runtime.mutation_calls == []
    if action == "publish":
        assert response.status_code == 200 and response.json()["failed"] == 1 and response.json()["updated"] == 0
    else:
        assert response.status_code == 403


@pytest.mark.parametrize("action", ["reject", "delete"])
def test_bulk_http_preflights_all_pairs_before_first_mutation(source_pair, action):
    engine, _lookup, runtime, profile = source_pair
    before = state(engine)
    with client_for(runtime, profile) as client:
        response = client.post("/api/documents/review/bulk", json={"action": action, "items": [
            {"job_id": 1156, "doc_id": 1164}, {"job_id": 1157, "doc_id": 1164},
        ]})
    assert response.status_code == 403
    assert state(engine) == before and runtime.mutation_calls == []


@pytest.mark.parametrize("action", ["reject", "delete", "publish"])
def test_bulk_http_valid_source_a_still_works_and_preserves_b(source_pair, action):
    engine, _lookup, runtime, profile = source_pair
    before_b = state(engine)["IngestionJobs"][1]
    with client_for(runtime, profile) as client:
        response = client.post("/api/documents/review/bulk", json={"action": action, "items": [{"job_id": 1156, "doc_id": 1164}]})
    assert response.status_code == 200 and response.json()["updated"] == 1
    with engine.connect() as conn:
        assert tuple(conn.execute(text("SELECT * FROM IngestionJobs WHERE JobID=1157")).one()) == before_b
        if action == "delete":
            assert conn.execute(text("SELECT DocID FROM TaiLieu")).first() is None
        else:
            assert conn.execute(text("SELECT ReviewStatus FROM TaiLieu")).scalar_one() == {"reject": "rejected", "publish": "approved"}[action]


@pytest.mark.parametrize("job_id,expected_ok", [(1157, False), (9999, False), (1156, True), (0, True)])
def test_shared_publish_command_checks_job_source_and_keeps_document_only(source_pair, job_id, expected_ok):
    engine, _lookup, runtime, profile = source_pair
    before = state(engine)
    outcome = documents._publish_document_command(runtime=runtime, job_id=job_id, doc_id=1164, publish_mode="standalone", profile=profile)
    assert outcome.ok is expected_ok
    if not expected_ok:
        assert state(engine) == before
        assert runtime.mutation_calls == []


def test_bulk_http_get_exposes_failed_job_without_wrong_doc_id(source_pair):
    _engine, _lookup, runtime, profile = source_pair
    with client_for(runtime, profile) as client:
        response = client.get("/api/ingestion/bulk-action-jobs")
    assert response.status_code == 200
    rows = {row["JobID"]: row for row in response.json()["jobs"]}
    assert rows[1156]["DocID"] == 1164 and rows[1157]["DocID"] is None
