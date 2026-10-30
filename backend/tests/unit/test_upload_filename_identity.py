"""Upload-name collisions preserve the original through the public ingest seam."""
from functools import partial
import re

import pytest
from sqlalchemy import create_engine, event, text

from mech_chatbot.db.repositories import document_pages
from mech_chatbot.ingestion.pdf import pipeline_implementation as pipeline
from tests.unit import test_markdown_ingestion as markdown
from tests.unit import test_pdf_pipeline_characterization as pdf


pytestmark = pytest.mark.unit


@pytest.fixture
def ingest_collision(tmp_path, monkeypatch):
    original = tmp_path / "original.md"
    incoming = tmp_path / "incoming.md"
    original.write_text("Original torque 113 N.m", encoding="utf-8")
    incoming.write_text("Replacement torque 127 N.m", encoding="utf-8")
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def sqlite_dialect(_conn, _cursor, statement, parameters, _context, _many):
        statement = statement.replace("dbo.", "")
        if "SELECT TOP 1 " in statement:
            statement = statement.replace("SELECT TOP 1 ", "SELECT ") + " LIMIT 1"
        returning = re.search(r"OUTPUT INSERTED\.(\w+)\s*", statement)
        if returning:
            statement = statement[:returning.start()] + statement[returning.end():]
            statement += f" RETURNING {returning.group(1)}"
        return statement, parameters

    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE TaiLieu (
            DocID INTEGER PRIMARY KEY, TenFile TEXT, ThuMuc TEXT,
            LifecycleStatus TEXT, ReviewStatus TEXT, IsCurrent INTEGER,
            FilePath TEXT, FamilyID INTEGER, BaseCode TEXT, VersionNo INTEGER,
            VersionLabel TEXT, VariantCode TEXT, Site TEXT, Domain TEXT,
            SecurityLevel TEXT, OwnerDepartment TEXT, SourceSystem TEXT,
            ExternalProcessingPolicy TEXT, ClassificationRationale TEXT,
            ClassificationModel TEXT, ClassificationJson TEXT,
            KnowledgeOwnerUserID INTEGER, KnowledgeApproverUserID INTEGER,
            TaxonomyVersion TEXT, PublicationState TEXT, PublicationError TEXT,
            Servable INTEGER, TrangThaiVector INTEGER)"""))
        conn.execute(text("""CREATE TABLE IngestionJobs (
            JobID INTEGER PRIMARY KEY, TenFile TEXT, ThuMuc TEXT,
            FilePath TEXT, ClassificationJson TEXT, UploadMetaJson TEXT,
            Site TEXT, CreatedAt INTEGER)"""))
        conn.execute(text("""CREATE TABLE DepartmentKnowledgeGovernance (
            DeptCode TEXT, IsActive INTEGER, KnowledgeOwnerUserID INTEGER,
            KnowledgeApproverUserID INTEGER, TaxonomyVersion TEXT,
            ExternalProcessingPolicy TEXT)"""))
        conn.execute(text("CREATE TABLE DocumentFamily (FamilyID INTEGER PRIMARY KEY, BaseCode TEXT, FamilyName TEXT)"))
        for table, key in document_pages._REINGEST_SNAPSHOT_TABLES.items():
            conn.execute(text(f"CREATE TABLE {table} ({key} INTEGER PRIMARY KEY, DocID INTEGER, Content TEXT)"))
            conn.execute(text(f"INSERT INTO {table} (DocID, Content) VALUES (42, 'original extraction')"))
        conn.execute(text("""INSERT INTO TaiLieu
            (DocID, TenFile, ThuMuc, LifecycleStatus, ReviewStatus, IsCurrent, FilePath,
             Site, Domain, SecurityLevel, PublicationState, Servable)
            VALUES (42, 'same-name.md', 'QA_B', 'draft', 'pending_review', 0, :path,
                    'ORIGINAL_SITE', 'quality', 'internal', 'draft', 0)"""), {"path": str(original)})
        for job_id, path, site in [(1, original, "ORIGINAL_SITE"), (2, incoming, "LATEST_JOB_SITE")]:
            conn.execute(text("""INSERT INTO IngestionJobs
                (JobID, TenFile, ThuMuc, FilePath, ClassificationJson, Site, CreatedAt)
                VALUES (:id, 'same-name.md', 'QA_B', :path, :classification, :site, :id)"""),
                {"id": job_id, "path": str(path), "site": site,
                 "classification": '{"domain":"quality","security_level":"internal"}'})

    pages, destructive, vectors, readers = [], [], {42: ["original vector"]}, []
    markdown._install_success_path_stubs(monkeypatch, pages)
    monkeypatch.setattr(pipeline, "reset_document_metadata", partial(document_pages.reset_document_metadata, db_engine=engine))
    native_reader = pipeline.extract_text_from_supported_file
    monkeypatch.setattr(pipeline, "extract_text_from_supported_file", lambda *a: readers.append(a[0]) or native_reader(*a))
    monkeypatch.setattr(pipeline, "_delete_vectors_for_file", lambda *a, **k: destructive.append("vectors") or vectors.pop(k.get("doc_id"), None))
    monkeypatch.setattr(pipeline, "mark_document_ingest_failed", lambda *a, **k: destructive.append("failed"))
    monkeypatch.setattr(pipeline, "restore_document_children", lambda *a, **k: destructive.append("restore"))

    def state():
        with engine.connect() as conn:
            return {
                "document": dict(conn.execute(text("SELECT * FROM TaiLieu WHERE DocID=42")).mappings().one()),
                "children": {table: [dict(r) for r in conn.execute(text(f"SELECT * FROM {table}")).mappings()]
                             for table in document_pages._REINGEST_SNAPSHOT_TABLES},
                "vectors": {key: list(value) for key, value in vectors.items()},
            }

    def ingest(path, *, kind="file", name="same-name.md"):
        entrypoint = pipeline.process_and_ingest_file
        if kind == "pdf":
            monkeypatch.setattr(pipeline.fitz, "open", lambda _path: pdf._PdfDocument(["PDF source"]))
            entrypoint = pipeline.process_and_ingest_pdf
        return entrypoint(
            str(path), name, "QA_B", domain_override="generic",
            security_override="internal", site_override="ORIGINAL_SITE",
            dependencies=markdown._dependencies(),
        )

    yield {"engine": engine, "original": original, "incoming": incoming,
           "ingest": ingest, "state": state, "destructive": destructive,
           "readers": readers, "pages": pages}
    engine.dispose()


@pytest.mark.parametrize("kind", ["file", "pdf"])
def test_same_name_new_upload_preserves_original_before_readers_or_vector_cleanup(ingest_collision, kind):
    env = ingest_collision
    before = env["state"]()
    report = env["ingest"](env["incoming"], kind=kind)

    assert env["state"]() == before
    assert report["status"] == "error"
    assert "trùng tên" in report["message"]
    assert env["readers"] == []
    assert env["destructive"] == []
    assert env["original"].read_text(encoding="utf-8") == "Original torque 113 N.m"
    assert env["incoming"].read_text(encoding="utf-8") == "Replacement torque 127 N.m"


@pytest.mark.parametrize("review_status", ["pending_review", "rejected"])
def test_retry_original_upload_uses_its_source_metadata_after_a_newer_same_name_job(ingest_collision, review_status):
    env = ingest_collision
    with env["engine"].begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET ReviewStatus=:status WHERE DocID=42"), {"status": review_status})
    report = env["ingest"](env["original"])

    assert report["status"] == "success"
    assert env["state"]()["document"]["FilePath"] == str(env["original"])
    assert env["state"]()["document"]["Site"] == "ORIGINAL_SITE"
    assert env["pages"][0]["text_extract"] == "Original torque 113 N.m"


def test_published_original_cannot_be_reset_even_by_the_same_source_retry(ingest_collision):
    env = ingest_collision
    with env["engine"].begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET LifecycleStatus='published', ReviewStatus='approved' WHERE DocID=42"))
    before = env["state"]()

    report = env["ingest"](env["original"])

    assert report["status"] == "error"
    assert "published" in report["message"]
    assert env["state"]() == before
    assert env["readers"] == []
    assert env["destructive"] == []


@pytest.mark.parametrize("path_kind", ["unknown", "missing"])
def test_unknown_source_identity_fails_closed_without_touching_original(ingest_collision, path_kind):
    env = ingest_collision
    if path_kind == "missing":
        with env["engine"].begin() as conn:
            conn.execute(text("UPDATE TaiLieu SET FilePath=NULL WHERE DocID=42"))
        path = env["incoming"]
    else:
        path = env["incoming"].parent / "unknown-job.md"
        path.write_text("Unknown source bytes", encoding="utf-8")
    before = env["state"]()

    report = env["ingest"](path)

    assert report["status"] == "error"
    assert env["state"]() == before
    assert env["readers"] == []
    assert env["destructive"] == []


def test_new_filename_version_upload_preserves_the_published_original(ingest_collision):
    env = ingest_collision
    with env["engine"].begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET LifecycleStatus='published', ReviewStatus='approved' WHERE DocID=42"))
        conn.execute(text("""UPDATE IngestionJobs SET TenFile='version-v2.md',
            ClassificationJson=:classification WHERE JobID=2"""),
            {"classification": '{"base_code":"ASM-001","version_no":2,"variant_code":"standard","domain":"quality","security_level":"internal"}'})
    before = env["state"]()

    report = env["ingest"](env["incoming"], name="version-v2.md")

    assert report["status"] == "success"
    assert env["state"]() == before
    with env["engine"].connect() as conn:
        replacement = conn.execute(text("SELECT * FROM TaiLieu WHERE TenFile='version-v2.md'")).mappings().one()
    assert replacement["DocID"] != 42
    assert replacement["VersionNo"] == 2
    assert replacement["VariantCode"] == "standard"
    assert replacement["FilePath"] == str(env["incoming"])
