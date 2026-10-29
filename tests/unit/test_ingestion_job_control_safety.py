"""Offline route authorization and real SQL update regression checks."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from mech_chatbot.api.dependencies import csrf_profile
from mech_chatbot.api.routers import documents
from mech_chatbot.db.repositories import jobs, ui_queries

pytestmark = pytest.mark.unit
CONTROLS = ("priority", "cancel", "requeue", "pending-review")
SOURCE_PATH = "data/raw/Uploads/QA/b93df8793e054b7abcd1e55734c1fa64.pdf"


@pytest.fixture
def route_client(monkeypatch):
    profile = {"user_id": 7, "username": "alice", "roles": ["reviewer"],
               "allowed_departments": ["QA"], "allowed_sites": ["A"],
               "max_security_level": "internal"}
    row = {"JobID": 8, "ThuMuc": "QA", "Site": "A", "SecurityLevel": "internal",
           "UploadedBy": "alice", "DocID": None}
    monkeypatch.setattr(documents, "list_bulk_action_jobs", lambda **_kwargs: [row])
    for name in ("set_job_priority", "cancel_job", "requeue_job", "mark_job_pending_review"):
        monkeypatch.setattr(documents, name, lambda *_args, **_kwargs: True)
    app = FastAPI()
    app.include_router(documents.router)
    app.dependency_overrides[csrf_profile] = lambda: profile
    with TestClient(app) as client:
        yield client, profile, row


def mutate(client, control):
    url = f"/api/ingestion/jobs/8/{control}"
    return client.patch(url, json={"priority": 1}) if control == "priority" else client.post(url)


@pytest.mark.parametrize("control", CONTROLS)
@pytest.mark.parametrize("change", ({"ThuMuc": "HR"}, {"Site": "B"},
                                  {"SecurityLevel": "confidential"}))
def test_control_denies_hidden_job_before_storage(route_client, monkeypatch, control, change):
    client, _profile, row = route_client
    row.update(change)
    names = {"priority": "set_job_priority", "cancel": "cancel_job",
             "requeue": "requeue_job", "pending-review": "mark_job_pending_review"}
    monkeypatch.setattr(documents, names[control], lambda *_a, **_k: pytest.fail("unauthorized write"))
    assert mutate(client, control).status_code == 403


@pytest.mark.parametrize("control", CONTROLS)
def test_control_checks_document_assignment(route_client, monkeypatch, control):
    client, _profile, row = route_client
    row["DocID"] = 19
    checked = []
    monkeypatch.setattr(documents, "validate_publish_actor", lambda *args: checked.append(args) or SimpleNamespace(valid=False))
    assert mutate(client, control).status_code == 403
    assert checked[0][0] == 19


@pytest.mark.parametrize("owned,department,expected", [(True, "QA", 200), (False, "QA", 403), (True, "HR", 403)])
def test_uploader_cancel_requires_own_job_and_department(route_client, owned, department, expected):
    client, profile, row = route_client
    profile["roles"] = ["uploader"]
    row.update(UploadedBy="alice" if owned else "bob", ThuMuc=department)
    assert mutate(client, "cancel").status_code == expected


@pytest.mark.parametrize("control", CONTROLS)
def test_missing_job_is_denied_before_mutation(route_client, monkeypatch, control):
    client, _profile, _row = route_client
    monkeypatch.setattr(documents, "list_bulk_action_jobs", lambda **_kwargs: [])
    assert mutate(client, control).status_code == 403


@pytest.mark.parametrize("role", ["reviewer", "knowledge_approver", "admin"])
@pytest.mark.parametrize("control", CONTROLS)
def test_authorized_document_control_checks_existing_assignment_guard(route_client, monkeypatch, role, control):
    client, profile, row = route_client
    profile["roles"] = [role]
    row["DocID"] = 19
    monkeypatch.setattr(documents, "validate_publish_actor", lambda *_a: SimpleNamespace(valid=True))
    assert mutate(client, control).status_code == 200


@pytest.mark.parametrize("control", ["priority", "requeue", "pending-review"])
def test_pure_uploader_has_no_review_controls(route_client, control):
    client, profile, _row = route_client
    profile["roles"] = ["uploader"]
    assert mutate(client, control).status_code == 403


class SQLiteSqlServerDialect:
    """Only translate dialect syntax; execute all predicates and updates unchanged."""
    def __init__(self, engine):
        self.engine = engine

    def connect(self):
        return self.begin()

    @contextmanager
    def begin(self):
        with self.engine.begin() as conn:
            def execute(statement, params=None):
                sql = str(statement).replace("dbo.", "").replace("GETDATE()", "CURRENT_TIMESTAMP")
                sql = sql.replace("ISNULL(", "COALESCE(")
                sql = sql.replace(" + ' [Huy boi ' + :by + ']'", " || ' [Huy boi ' || :by || ']'")
                return conn.execute(text(sql), params or {})
            yield SimpleNamespace(execute=execute)


@pytest.fixture
def sqlite_jobs(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE IngestionJobs (
            JobID INTEGER PRIMARY KEY, TenFile TEXT, ThuMuc TEXT, Status TEXT,
            ErrorMessage TEXT, FailureType TEXT, NextRetryAt TEXT, RetryCount INTEGER,
            LockedBy TEXT, LockedAt TEXT, ProgressPercent INTEGER, CanceledBy TEXT,
            CanceledAt TEXT, UpdatedAt TEXT, Priority INTEGER, QualityStatus TEXT,
            Site TEXT, SecurityLevel TEXT, UploadedBy TEXT, FilePath TEXT)"""))
        conn.execute(text("""CREATE TABLE TaiLieu (DocID INTEGER PRIMARY KEY,
            TenFile TEXT, ThuMuc TEXT, LifecycleStatus TEXT, ReviewStatus TEXT,
            PublicationState TEXT, Servable INTEGER, Site TEXT, SecurityLevel TEXT, FilePath TEXT)"""))
    adapter = SQLiteSqlServerDialect(engine)
    monkeypatch.setattr(jobs, "_ensure_engine", lambda: None)
    monkeypatch.setattr(jobs, "engine", adapter)
    monkeypatch.setattr(ui_queries, "engine", adapter)
    yield engine
    engine.dispose()


def seed(engine, *, state="failed", locked=False, quality="ready_for_review", doc="draft"):
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO IngestionJobs
            (JobID, TenFile, ThuMuc, Status, RetryCount, LockedBy, LockedAt, QualityStatus,
             Site, SecurityLevel, UploadedBy, FilePath)
            VALUES (8, 'qa.pdf', 'QA', :state, 2, :lock, :lock, :quality, 'A', 'internal', 'alice', :source_path)"""),
                     {"state": state, "lock": "worker" if locked else None, "quality": quality, "source_path": SOURCE_PATH})
        conn.execute(text("""INSERT INTO TaiLieu VALUES
            (19, 'qa.pdf', 'QA', :life, :review, :publication, :servable, 'A', 'internal', :source_path)"""),
                     {"life": "draft" if doc == "draft" else doc,
                      "review": "pending_review" if doc == "draft" else "approved",
                      "publication": doc, "servable": int(doc == "published"), "source_path": SOURCE_PATH})


def repo_mutate(control):
    return {"priority": lambda: jobs.set_job_priority(8, 1), "cancel": lambda: jobs.cancel_job(8),
            "requeue": lambda: jobs.requeue_job(8), "pending-review": lambda: ui_queries.mark_job_pending_review(8)}[control]()


@pytest.mark.parametrize("control", CONTROLS)
@pytest.mark.parametrize("change", [{"state": "published"}, {"state": "publishing"},
                                   {"state": "embedding"}, {"locked": True},
                                   {"doc": "published"}, {"doc": "publishing"},
                                   {"doc": "deleting"}, {"doc": "archived"}])
def test_atomic_update_preserves_protected_job_and_document(sqlite_jobs, control, change):
    seed(sqlite_jobs, **change)
    with sqlite_jobs.connect() as conn:
        before = tuple(conn.execute(text("SELECT * FROM IngestionJobs")).one())
    assert repo_mutate(control) is False
    with sqlite_jobs.connect() as conn:
        assert tuple(conn.execute(text("SELECT * FROM IngestionJobs")).one()) == before


@pytest.mark.parametrize("control", CONTROLS)
def test_allowed_update_changes_actual_row_and_missing_job_is_false(sqlite_jobs, control):
    assert repo_mutate(control) is False
    seed(sqlite_jobs)
    assert repo_mutate(control) is True
    with sqlite_jobs.connect() as conn:
        row = conn.execute(text("SELECT Status, RetryCount, Priority FROM IngestionJobs")).one()
    expected = {"priority": ("failed", 2, 1), "cancel": ("rejected", 2, None),
                "requeue": ("pending", 0, None), "pending-review": ("pending_review", 2, None)}
    assert tuple(row) == expected[control]


@pytest.mark.parametrize("quality", ["blocked", "unknown", None])
def test_manual_review_cannot_bypass_quality_gate(sqlite_jobs, quality):
    seed(sqlite_jobs, quality=quality)
    assert ui_queries.mark_job_pending_review(8) is False


def test_manual_review_needs_review_quality_is_allowed(sqlite_jobs):
    seed(sqlite_jobs, quality="needs_review")
    assert ui_queries.mark_job_pending_review(8) is True


@pytest.mark.parametrize("control", CONTROLS)
@pytest.mark.parametrize("lock_column", ["LockedBy", "LockedAt"])
def test_either_worker_lock_blocks_atomic_update(sqlite_jobs, control, lock_column):
    seed(sqlite_jobs)
    with sqlite_jobs.begin() as conn:
        conn.execute(text(f"UPDATE IngestionJobs SET {lock_column} = 'worker'"))
    assert repo_mutate(control) is False


def test_manual_review_requires_existing_draft_document(sqlite_jobs):
    seed(sqlite_jobs)
    with sqlite_jobs.begin() as conn:
        conn.execute(text("DELETE FROM TaiLieu"))
    assert ui_queries.mark_job_pending_review(8) is False


def test_requeue_cannot_bypass_waiting_quota(sqlite_jobs):
    seed(sqlite_jobs, state="waiting_quota")
    assert jobs.requeue_job(8) is False


def test_cancel_then_requeue_cannot_bypass_retained_quota_schedule(sqlite_jobs):
    seed(sqlite_jobs, state="waiting_quota")
    with sqlite_jobs.begin() as conn:
        conn.execute(text("UPDATE IngestionJobs SET FailureType = 'vision_quota', NextRetryAt = '2099-01-01'"))
    assert jobs.cancel_job(8, "alice") is True
    assert jobs.requeue_job(8) is False
    with sqlite_jobs.connect() as conn:
        row = conn.execute(text("SELECT Status, FailureType, NextRetryAt, RetryCount FROM IngestionJobs")).one()
    assert tuple(row) == ("rejected", "vision_quota", "2099-01-01", 2)


@pytest.mark.parametrize("control", CONTROLS)
@pytest.mark.parametrize("hidden", [False, True])
def test_http_control_reaches_real_targeted_lookup_and_atomic_update(sqlite_jobs, monkeypatch, control, hidden):
    seed(sqlite_jobs)
    profile = {"user_id": 7, "username": "alice", "roles": ["reviewer"],
               "allowed_departments": ["QA"], "allowed_sites": ["A"],
               "max_security_level": "internal"}
    with sqlite_jobs.begin() as conn:
        if hidden:
            conn.execute(text("UPDATE TaiLieu SET Site = 'B'"))
        before = tuple(conn.execute(text("SELECT * FROM IngestionJobs")).one())
    monkeypatch.setattr(documents, "validate_publish_actor", lambda *_a: SimpleNamespace(valid=True))
    app = FastAPI()
    app.include_router(documents.router)
    app.dependency_overrides[csrf_profile] = lambda: profile
    with TestClient(app) as client:
        response = mutate(client, control)
    with sqlite_jobs.connect() as conn:
        after = tuple(conn.execute(text("SELECT * FROM IngestionJobs")).one())
    assert response.status_code == (403 if hidden else 200)
    assert (after == before) is hidden
    if not hidden:
        assert response.json() == {"ok": True}
