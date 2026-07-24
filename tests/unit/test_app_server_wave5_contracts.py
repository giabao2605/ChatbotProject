from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mech_chatbot.api import dependencies
from mech_chatbot.api.routers import chat as chat_routes
from mech_chatbot.api.routers import documents as document_routes
from mech_chatbot.api.routers import operations as operation_routes
from mech_chatbot.application.document_upload import DocumentUpload, StoredUpload
from mech_chatbot.application.protected_files import AuthorizedFile, ProtectedFileError


pytestmark = pytest.mark.unit

app_server = pytest.importorskip("mech_chatbot.api.app_server")


@pytest.fixture(autouse=True)
def _bind_test_security_snapshot(monkeypatch):
    monkeypatch.setattr(
        app_server.app.state,
        "process_settings",
        replace(
            app_server.app.state.process_settings,
            session_secret="wave-five-secret",
        ),
    )


def _profile(*roles):
    return {
        "user_id": 7,
        "username": "alice",
        "display_name": "Alice",
        "department": "CoKhi",
        "roles": list(roles or ("viewer",)),
        "allowed_departments": ["CoKhi"],
        "allowed_sites": ["HN"],
        "max_security_level": "confidential",
        "preferred_language": "vi",
    }


@pytest.fixture
def client_for():
    clients = []

    def build(profile):
        app_server.app.dependency_overrides[app_server.current_profile] = lambda: profile
        app_server.app.dependency_overrides[app_server.csrf_profile] = lambda: profile
        client = TestClient(app_server.app)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()
    app_server.app.dependency_overrides.clear()


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class _Connection:
    def __init__(self, row):
        self._row = row

    def execute(self, _statement, _params=None):
        return _Result(self._row)


class _Engine:
    def __init__(self, row):
        self._row = row

    def connect(self):
        return nullcontext(_Connection(self._row))


def test_auth_session_lifecycle_exposes_only_public_profile(
    monkeypatch,
):
    profile = _profile("viewer") | {"password_hash": "must-not-leak"}
    monkeypatch.setattr(
        operation_routes,
        "authenticate_user",
        lambda username, password: profile
        if (username, password) == ("alice", "correct-password")
        else None,
    )
    monkeypatch.setattr(operation_routes, "load_user_profile", lambda **_kwargs: profile)

    with TestClient(app_server.app) as client:
        denied = client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )
        logged_in = client.post(
            "/api/auth/login",
            json={"username": " alice ", "password": "correct-password"},
        )

        assert denied.status_code == 401
        assert logged_in.status_code == 200
        first_user = logged_in.json()["user"]
        assert first_user["username"] == "alice"
        assert first_user["csrf_token"]
        assert "password_hash" not in first_user

        current = client.get("/api/auth/me")
        refreshed = client.post(
            "/api/auth/refresh",
            headers={"X-CSRF-Token": first_user["csrf_token"]},
        )
        second_csrf = refreshed.json()["user"]["csrf_token"]
        logged_out = client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": second_csrf}
        )

        assert current.status_code == 200
        assert refreshed.status_code == 200
        assert second_csrf
        assert logged_out.json() == {"ok": True}
        assert client.get("/api/auth/me").status_code == 401


def test_auth_profile_and_refresh_fail_closed_for_inactive_user(monkeypatch):
    token, payload = app_server.app_security.create_session_token(
        user_id=7,
        username="alice",
        settings=app_server.app.state.process_settings,
    )
    monkeypatch.setattr(operation_routes, "load_user_profile", lambda **_kwargs: None)

    with TestClient(app_server.app) as client:
        client.cookies.set(app_server.app_security.SESSION_COOKIE_NAME, token)
        current = client.get("/api/auth/me")
        refreshed = client.post(
            "/api/auth/refresh", headers={"X-CSRF-Token": payload.csrf}
        )

    assert current.status_code == 401
    assert refreshed.status_code == 401


def test_protected_dependencies_reject_inactive_session_before_storage(monkeypatch):
    token, payload = app_server.app_security.create_session_token(
        user_id=7,
        username="alice",
        settings=app_server.app.state.process_settings,
    )
    monkeypatch.setattr(dependencies, "load_user_profile", lambda **_kwargs: None)
    monkeypatch.setattr(
        chat_routes,
        "get_all_sessions",
        lambda **_kwargs: pytest.fail("inactive session must not query chat storage"),
    )
    monkeypatch.setattr(
        operation_routes,
        "update_user_preferred_language",
        lambda *_args: pytest.fail("inactive session must not update preferences"),
    )

    with TestClient(app_server.app) as client:
        client.cookies.set(app_server.app_security.SESSION_COOKIE_NAME, token)
        read_response = client.get("/api/chat/sessions")
        write_response = client.patch(
            "/api/auth/me/preferences",
            json={"language": "en"},
            headers={"X-CSRF-Token": payload.csrf},
        )

    assert read_response.status_code == 401
    assert write_response.status_code == 401


def test_preference_update_reports_storage_rejection(monkeypatch, client_for):
    client = client_for(_profile())
    monkeypatch.setattr(
        operation_routes, "update_user_preferred_language", lambda *_args: False
    )
    rejected = client.patch("/api/auth/me/preferences", json={"language": "xx"})
    monkeypatch.setattr(
        operation_routes, "update_user_preferred_language", lambda *_args: True
    )
    accepted = client.patch("/api/auth/me/preferences", json={"language": "en"})

    assert rejected.status_code == 400
    assert accepted.json() == {"ok": True}


def test_chat_image_upload_validates_content_and_returns_owner_token(
    monkeypatch, client_for, tmp_path
):
    monkeypatch.setattr(chat_routes, "data_raw_root", lambda: tmp_path)
    client = client_for(_profile())

    invalid_type = client.post(
        "/api/chat/upload-image",
        files={"file": ("notes.txt", b"not-an-image", "text/plain")},
    )
    oversized = client.post(
        "/api/chat/upload-image",
        files={"file": ("large.png", b"x" * (15 * 1024 * 1024 + 1), "image/png")},
    )
    uploaded = client.post(
        "/api/chat/upload-image",
        files={"file": ("evidence.png", b"png", "image/png")},
    )

    assert invalid_type.status_code == 400
    assert oversized.status_code == 400
    assert uploaded.status_code == 200
    body = uploaded.json()
    token_payload = app_server.app_security.verify_session_token(
        body["image_token"],
        settings=app_server.app.state.process_settings,
    )
    assert body["ok"] is True
    assert body["file_name"] == "evidence.png"
    assert token_payload.user_id == 7
    assert token_payload.username == f"image:{body['image_id']}"
    assert (tmp_path / "Chat_Images" / body["image_id"]).read_bytes() == b"png"


def test_chat_message_rejects_untrusted_image_tokens_before_rag(
    monkeypatch, client_for
):
    class RejectingRunner:
        def stream(self, *_args, **_kwargs):
            pytest.fail("rejected image must not reach RAG")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, chat_turn_runner=RejectingRunner()),
    )
    client = client_for(_profile())
    wrong_owner, _ = app_server.app_security.create_session_token(
        user_id=8,
        username="image:evidence.png",
        settings=app_server.app.state.process_settings,
    )
    wrong_kind, _ = app_server.app_security.create_session_token(
        user_id=7,
        username="alice",
        settings=app_server.app.state.process_settings,
    )
    missing, _ = app_server.app_security.create_session_token(
        user_id=7,
        username="image:missing.png",
        settings=app_server.app.state.process_settings,
    )
    monkeypatch.setattr(chat_routes, "chat_image_path", lambda _image_id: None)

    def send(token):
        return client.post(
            "/api/chat/message",
            json={"session_id": "s1", "question": "inspect", "image_token": token},
        )

    assert send(wrong_owner).status_code == 403
    assert send(wrong_kind).status_code == 400
    assert send(missing).status_code == 404


def test_chat_history_feedback_and_session_contracts(monkeypatch, client_for):
    calls = []

    class Support:
        def answer_sources_for_chat_ids(self, _chat_ids):
            return []

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, app_support_queries=Support()),
    )
    monkeypatch.setattr(
        chat_routes,
        "get_all_sessions",
        lambda **kwargs: calls.append(("sessions", kwargs)) or [{"session_id": "s1"}],
    )
    monkeypatch.setattr(
        chat_routes,
        "get_chat_history",
        lambda *_args, **kwargs: calls.append(("history", kwargs))
        or [
            {
                "chat_id": 11,
                "content": "Answer\n\n---\n**References:**\n- stale.pdf",
                "image": "C:/chat/evidence.png",
                "ref_images": ["legacy"],
            }
        ],
    )
    monkeypatch.setattr(
        chat_routes,
        "clear_chat_history",
        lambda session_id, **kwargs: calls.append(("delete", session_id, kwargs)) or 2,
    )
    monkeypatch.setattr(
        chat_routes,
        "update_chat_feedback",
        lambda *args, **kwargs: calls.append(("feedback", args, kwargs)) or True,
    )
    client = client_for(_profile("admin"))

    assert client.get("/api/chat/sessions").json() == {
        "sessions": [{"session_id": "s1"}]
    }
    assert client.post("/api/chat/history", json={}).status_code == 400
    history = client.post("/api/chat/history", json={"session_id": " s1 "})
    deleted = client.delete("/api/chat/sessions/s1")
    invalid_feedback = client.post("/api/chat/feedback", json={"chat_id": 11})
    positive = client.post(
        "/api/chat/feedback", json={"chat_id": "11", "rating": "5"}
    )
    negative = client.post(
        "/api/chat/feedback", json={"chat_id": 11, "rating": -1}
    )

    message = history.json()["messages"][0]
    assert message == {
        "chat_id": 11,
        "content": "Answer",
        "image": "C:/chat/evidence.png",
        "image_url": "/api/files/chat-images/evidence.png",
        "citations": [],
        "ref_text": "",
    }
    assert deleted.json() == {"ok": True, "deleted": 2}
    assert invalid_feedback.status_code == 400
    assert positive.json() == {"ok": True}
    assert negative.json() == {"ok": True}
    assert [call[1][1] for call in calls if call[0] == "feedback"] == [1, -1]


def test_history_exposes_only_sources_attributed_by_each_answer(
    monkeypatch, client_for
):
    rows = [
        (11, 42, "manual.pdf", 3, "7", 0.91, 1),
        (12, 43, "bom.xlsx", None, "2", 0.82, 0),
        (13, None, "invalid.pdf", None, "1", 0.5, 0),
        (13, 44, "invalid-page.pdf", None, "x", 0.5, 0),
    ]
    class Support:
        def answer_sources_for_chat_ids(self, _chat_ids):
            return rows

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, app_support_queries=Support()),
    )
    monkeypatch.setattr(
        chat_routes,
        "page_has_vision",
        lambda doc_id, page_no: (doc_id, page_no) == (42, 7),
    )
    monkeypatch.setattr(
        chat_routes,
        "get_chat_history",
        lambda *_args, **_kwargs: [
            {"chat_id": 11, "content": "Verified [SRC:D42P7]"},
            {"chat_id": 12, "content": "Legacy source: bom.xlsx"},
            {"chat_id": 13, "content": "Unattributed answer"},
            {"content": "No persisted chat id", "image": None},
        ],
    )

    response = client_for(_profile()).post(
        "/api/chat/history", json={"session_id": "s1"}
    )

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[0]["citations"] == [
        {
            "doc_id": 42,
            "page_no": 7,
            "file_name": "manual.pdf",
            "version_no": 3,
            "score": 0.91,
            "source_id": "D42P7",
            "has_vision": True,
            "page_url": "/api/files/documents/42/pages/7",
            "original_url": "/api/files/documents/42/original",
        }
    ]
    assert messages[1]["citations"][0]["source_id"] == "D43P2"
    assert messages[1]["citations"][0]["page_url"] is None
    assert messages[2]["citations"] == []
    assert "Nguồn tham chiếu" in messages[0]["ref_text"]
    assert "citations" not in messages[3]


def test_chat_image_download_fails_closed_until_owner_is_proven(
    monkeypatch, client_for, tmp_path
):
    image = tmp_path / "owned.png"
    image.write_bytes(b"owned")
    client = client_for(_profile())

    class Resolver:
        def __init__(self, outcome):
            self.outcome = outcome

        def resolve(self, _reference, _actor):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(
            app_server.app.state.runtime,
            protected_file_resolver=Resolver(
                ProtectedFileError("storage_failed", "Database is not ready")
            ),
        ),
    )
    unavailable = client.get("/api/files/chat-images/owned.png")
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(
            app_server.app.state.runtime,
            protected_file_resolver=Resolver(
                ProtectedFileError(
                    "unauthorized", "Image is not visible to this user"
                )
            ),
        ),
    )
    forbidden = client.get("/api/files/chat-images/owned.png")
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(
            app_server.app.state.runtime,
            protected_file_resolver=Resolver(AuthorizedFile(path=image)),
        ),
    )
    allowed = client.get("/api/files/chat-images/owned.png")

    assert unavailable.status_code == 503
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert allowed.content == b"owned"


def test_chat_image_download_rejects_disappeared_file(monkeypatch, client_for, tmp_path):
    missing = tmp_path / "disappeared.png"

    class Resolver:
        def resolve(self, _reference, _actor):
            return AuthorizedFile(path=missing)

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, protected_file_resolver=Resolver()),
    )

    response = client_for(_profile()).get(
        "/api/files/chat-images/disappeared.png"
    )

    assert response.status_code == 404


def test_access_administration_binds_identity_and_preserves_failure(
    monkeypatch, client_for
):
    calls = []
    monkeypatch.setattr(
        operation_routes.access_service,
        "resolve_access_request",
        lambda **kwargs: calls.append(("resolve", kwargs)) or None,
    )
    monkeypatch.setattr(
        operation_routes.access_service,
        "revoke_user_clearance",
        lambda **kwargs: calls.append(("clearance", kwargs)) or {"changed": True},
    )
    monkeypatch.setattr(
        operation_routes.access_service,
        "revoke_user_department",
        lambda **kwargs: calls.append(("department", kwargs)) or False,
    )
    denied = client_for(_profile()).post(
        "/api/access/requests/4/resolve", json={"decision": "approved"}
    )
    client = client_for(_profile("security_admin"))
    resolved = client.post(
        "/api/access/requests/4/resolve",
        json={"decision": "approved", "review_note": "verified"},
    )
    clearance = client.post(
        "/api/access/users/8/revoke-clearance",
        json={"new_level": "internal", "reason": "rotation"},
    )
    department = client.post(
        "/api/access/users/8/revoke-department",
        json={"department": "QA", "reason": "rotation"},
    )

    assert denied.status_code == 403
    assert resolved.json() == {"ok": False, "result": None}
    assert clearance.json() == {"ok": True, "result": {"changed": True}}
    assert department.json() == {"ok": False, "result": False}
    assert calls[0][1]["reviewer_username"] == "alice"
    assert calls[1][1]["actor_id"] == 7
    assert calls[2][1]["dept"] == "QA"


def test_security_admin_user_mutations_report_repository_outcomes(
    monkeypatch, client_for
):
    calls = []

    def record(name, result):
        def fake(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        return fake

    monkeypatch.setattr(
        operation_routes.ui_query_service, "set_user_active_status", record("active", True)
    )
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "update_user_active_and_roles",
        record("roles", False),
    )
    monkeypatch.setattr(
        operation_routes.org_service, "set_user_departments", record("departments", True)
    )
    monkeypatch.setattr(operation_routes.org_service, "set_user_sites", record("sites", False))
    monkeypatch.setattr(
        operation_routes.access_service, "set_user_clearance", record("clearance", True)
    )
    monkeypatch.setattr(
        operation_routes.ui_query_service, "delete_user_account", record("delete", False)
    )
    client = client_for(_profile("security_admin"))

    responses = [
        client.patch("/api/users/8/active", json={"is_active": True}),
        client.patch(
            "/api/users/8/roles",
            json={"is_active": False, "add_roles": ["viewer"], "del_roles": []},
        ),
        client.patch("/api/users/8/departments", json={"departments": ["QA"]}),
        client.patch("/api/users/8/sites", json={"sites": ["HN"]}),
        client.patch("/api/users/8/clearance", json={"max_level": "internal"}),
        client.delete("/api/users/8"),
    ]

    assert [response.json()["ok"] for response in responses] == [
        True,
        False,
        True,
        False,
        True,
        False,
    ]
    assert calls[0][2] == {"actor_username": "alice", "actor_id": 7}
    assert calls[-1][2] == {"actor_username": "alice", "actor_id": 7}


def test_bulk_metadata_selector_normalizes_status_suffixes(
    monkeypatch, client_for
):
    departments = []
    monkeypatch.setattr(
        document_routes,
        "list_docs_for_bulk_meta",
        lambda **kwargs: departments.append(kwargs["dept"]) or [],
    )
    monkeypatch.setattr(
        document_routes, "list_bulk_meta_departments", lambda: ["CoKhi"]
    )
    client = client_for(_profile("reviewer"))

    disabled = client.get("/api/documents/bulk-meta?dept=CoKhi%20(disabled)")
    archived = client.get("/api/documents/bulk-meta?dept=QA%20(archived)")
    unmarked = client.get("/api/documents/bulk-meta?dept=Shared")

    assert disabled.status_code == archived.status_code == unmarked.status_code == 200
    assert departments == ["CoKhi", "QA", "Shared"]


def test_document_lifecycle_counts_hide_reviewer_only_buckets(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        document_routes,
        "get_document_lifecycle_counts",
        lambda **_kwargs: {"effective": 4, "expired": 2, "needs_review": 1},
    )

    viewer = client_for(_profile()).get(
        "/api/documents/lifecycle-counts?soon_days=-5"
    )
    reviewer = client_for(_profile("reviewer")).get(
        "/api/documents/lifecycle-counts?soon_days=500"
    )

    assert viewer.json() == {"counts": {"effective": 4}, "soon_days": 0}
    assert reviewer.json() == {
        "counts": {"effective": 4, "expired": 2, "needs_review": 1},
        "soon_days": 365,
    }


def test_health_reports_database_probe_outcome(monkeypatch, client_for):
    client = client_for(_profile())

    class Support:
        def __init__(self, outcome):
            self.outcome = outcome

        def database_ready(self):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, app_support_queries=Support(False)),
    )
    unavailable = client.get("/api/health")
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, app_support_queries=Support(True)),
    )
    available = client.get("/api/health")
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(
            app_server.app.state.runtime,
            app_support_queries=Support(RuntimeError("private database detail")),
        ),
    )
    failed = client.get("/api/health")

    assert unavailable.json() == {
        "status": "degraded",
        "app": "mech-chatbot-app-api",
        "db": "unavailable",
    }
    assert available.json()["status"] == "ok"
    assert failed.json()["status"] == "degraded"


def test_single_upload_validates_file_before_creating_job(
    monkeypatch, client_for, tmp_path
):
    created = []

    class UploadStorage:
        def store(self, *, file_name, content, owner_department):
            directory = tmp_path / "Uploads" / owner_department
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / file_name
            path.write_bytes(content)
            return StoredUpload(original_name=file_name, stored_path=str(path))

        def delete(self, stored_path):
            path = Path(stored_path)
            path.unlink(missing_ok=True)
            return True

    class UploadJobs:
        def create_job(self, **kwargs):
            created.append(kwargs)
            return 41

    upload_runtime = DocumentUpload(
        storage=UploadStorage(),
        job_store=UploadJobs(),
    )
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, document_upload=upload_runtime),
    )
    client = client_for(_profile("uploader"))

    denied_department = client.post(
        "/api/documents/upload",
        files={"file": ("manual.pdf", b"pdf", "application/pdf")},
        data={"thu_muc": "QA"},
    )
    unsupported = client.post(
        "/api/documents/upload",
        files={"file": ("malware.exe", b"bad", "application/octet-stream")},
        data={"thu_muc": "CoKhi"},
    )
    empty = client.post(
        "/api/documents/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        data={"thu_muc": "CoKhi"},
    )
    accepted = client.post(
        "/api/documents/upload",
        files={"file": ("manual.pdf", b"pdf", "application/pdf")},
        data={
            "thu_muc": "CoKhi",
            "meta_json": '{"owner":"qa","empty":""}',
            "extra_departments_json": '["Shared","CoKhi"]',
        },
    )

    assert denied_department.status_code == 403
    assert unsupported.status_code == 400
    assert empty.status_code == 400
    assert accepted.json() == {"ok": True, "job_id": 41, "file_name": "manual.pdf"}
    assert created[0]["upload_metadata"] == {"owner": "qa"}
    assert created[0]["shared_departments"] == ("CoKhi", "Shared")
    assert (tmp_path / "Uploads" / "CoKhi").exists()


@pytest.mark.parametrize(
    ("reject_result", "mark_result", "expected"),
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_ingestion_rejection_never_reports_false_success(
    reject_result, mark_result, expected, monkeypatch, client_for
):
    marked = []
    monkeypatch.setattr(
        document_routes, "reject_ingestion_job", lambda *_args: reject_result
    )
    monkeypatch.setattr(
        document_routes,
        "mark_job_rejected",
        lambda job_id: marked.append(job_id) or mark_result,
    )

    response = client_for(_profile("reviewer")).post(
        "/api/ingestion/jobs/9/reject", json={"reason": "invalid metadata"}
    )

    assert response.json() == {"ok": expected}
    assert marked == ([] if reject_result else [9])
