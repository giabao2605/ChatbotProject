import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


pytestmark = pytest.mark.unit

app_server = pytest.importorskip("mech_chatbot.api.app_server")


class _StreamResponse:
    def __init__(self, events):
        self.status_code = 200
        self.ok = True
        self.text = ""
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self, decode_unicode=True):
        del decode_unicode
        for event, payload in self._events:
            yield f"event: {event}"
            yield f"data: {json.dumps(payload, ensure_ascii=False)}"
            yield ""


def _profile(*roles):
    return {
        "user_id": 7,
        "username": "alice",
        "department": "CoKhi",
        "roles": list(roles or ("viewer",)),
        "allowed_departments": ["CoKhi"],
        "allowed_sites": ["HN"],
        "max_security_level": "confidential",
        "preferred_language": "vi",
    }


def _sse_events(body):
    events = []
    for block in body.strip().split("\n\n"):
        name = None
        payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        if name:
            events.append((name, payload))
    return events


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


def test_batch_upload_returns_created_jobs_and_per_file_authorization_errors(
    monkeypatch, client_for
):
    profile = _profile("uploader")
    client = client_for(profile)
    stored = []
    created = []

    def fake_store(upload, dept):
        path = f"C:/staged/{upload.filename}"
        stored.append((upload.filename, dept, path))
        return upload.filename, path

    def fake_create(**kwargs):
        created.append(kwargs)
        return 91

    monkeypatch.setattr(app_server, "_store_upload_file", fake_store)
    monkeypatch.setattr(
        "mech_chatbot.db.repositories.jobs.create_ingestion_job", fake_create
    )

    response = client.post(
        "/api/documents/upload-batch",
        files=[
            ("files", ("bom.pdf", b"bom", "application/pdf")),
            ("files", ("secret.pdf", b"secret", "application/pdf")),
        ],
        data={
            "thu_muc": "CoKhi",
            "domain": "Mechanical",
            "extra_departments_json": "Shared,CoKhi",
            "meta_json": '{"owner":"qa","empty":""}',
            "assignments_json": json.dumps(
                [
                    {"site": "HN", "extra_departments": ["Shared"]},
                    {"thu_muc": "Finance"},
                ]
            ),
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "jobs": [{"job_id": 91, "file_name": "bom.pdf", "thu_muc": "CoKhi"}],
        "errors": [
            {
                "file_name": "secret.pdf",
                "error": "Không có quyền upload vào phòng ban Finance",
            }
        ],
        "created": 1,
        "failed": 1,
    }
    assert stored == [("bom.pdf", "CoKhi", "C:/staged/bom.pdf")]
    assert created[0]["phong_ban"] == ["CoKhi", "Shared"]
    assert created[0]["upload_meta"] == {"owner": "qa"}
    assert created[0]["site"] == "HN"


def test_batch_upload_rejects_viewer_before_storing_files(monkeypatch, client_for):
    client = client_for(_profile("viewer"))
    monkeypatch.setattr(
        app_server,
        "_store_upload_file",
        lambda *_args: pytest.fail("unauthorized upload must not be stored"),
    )

    response = client.post(
        "/api/documents/upload-batch",
        files=[("files", ("bom.pdf", b"bom", "application/pdf"))],
        data={"thu_muc": "CoKhi"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Forbidden"}


def test_batch_upload_rejects_more_than_fifty_files_before_storing(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        app_server,
        "_store_upload_file",
        lambda *_args: pytest.fail("oversized batch must not store files"),
    )
    files = [
        ("files", (f"doc-{index}.pdf", b"pdf", "application/pdf"))
        for index in range(51)
    ]

    response = client_for(_profile("uploader")).post(
        "/api/documents/upload-batch", files=files, data={"thu_muc": "CoKhi"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Một lần upload tối đa 50 tệp"}


def test_batch_upload_removes_staged_file_when_job_creation_fails(
    monkeypatch, client_for, tmp_path
):
    staged = tmp_path / "staged.pdf"
    staged.write_bytes(b"pdf")
    monkeypatch.setattr(
        app_server,
        "_store_upload_file",
        lambda upload, _dept: (upload.filename, str(staged)),
    )
    monkeypatch.setattr(
        "mech_chatbot.db.repositories.jobs.create_ingestion_job",
        lambda **_kwargs: None,
    )

    response = client_for(_profile("uploader")).post(
        "/api/documents/upload-batch",
        files=[("files", ("bom.pdf", b"pdf", "application/pdf"))],
        data={"thu_muc": "CoKhi"},
    )

    assert response.status_code == 200
    assert response.json()["created"] == 0
    assert response.json()["failed"] == 1
    assert not staged.exists()


@pytest.mark.parametrize(
    ("form_field", "value", "expected_detail"),
    [
        ("meta_json", "not-json", "meta_json không hợp lệ (JSON)"),
        ("assignments_json", "{}", "assignments_json phải là array JSON"),
    ],
)
def test_batch_upload_rejects_invalid_json_fields(
    form_field, value, expected_detail, client_for
):
    client = client_for(_profile("uploader"))
    response = client.post(
        "/api/documents/upload-batch",
        files=[("files", ("bom.pdf", b"bom", "application/pdf"))],
        data={"thu_muc": "CoKhi", form_field: value},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": expected_detail}


@pytest.mark.parametrize(
    "payload",
    [
        {"doc_ids": [], "metadata": {"site": "HN"}},
        {"doc_ids": [1], "metadata": {}},
    ],
)
def test_bulk_metadata_rejects_empty_contract_fields(payload, client_for):
    response = client_for(_profile("reviewer")).patch(
        "/api/documents/bulk-metadata", json=payload
    )

    assert response.status_code == 400


def test_bulk_metadata_reports_mixed_results_without_leaking_internal_errors(
    monkeypatch, client_for
):
    updates = []

    def validate_actor(doc_id, _user_id, _roles):
        if doc_id == 3:
            return False, "metadata_actor_denied"
        return True, "allowed"

    def update(doc_id, **kwargs):
        updates.append((doc_id, kwargs))
        if doc_id == 2:
            raise RuntimeError("private database coordinates")
        if doc_id == 4:
            return False
        return True

    monkeypatch.setattr(app_server, "validate_document_metadata_actor", validate_actor)
    monkeypatch.setattr(app_server, "update_document_common_metadata", update)

    response = client_for(_profile("reviewer")).patch(
        "/api/documents/bulk-metadata",
        json={
            "doc_ids": [1, "bad", 2, 3, 4],
            "metadata": {
                "site": "HN",
                "security_level": "",
                "domain": "Mechanical",
                "attributes": {"material": "S45C"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": False, "updated": 1, "failed": 4}
    assert "private database coordinates" not in response.text
    assert updates[0] == (
        1,
        {
            "reviewer": "alice",
            "attributes": {"material": "S45C"},
            "domain": "Mechanical",
            "site": "HN",
        },
    )


def test_bulk_review_rejects_unauthorized_actor_before_work(monkeypatch, client_for):
    monkeypatch.setattr(
        app_server,
        "publish_document",
        lambda *_args, **_kwargs: pytest.fail("viewer must not publish"),
    )

    response = client_for(_profile("viewer")).post(
        "/api/documents/review/bulk",
        json={"action": "publish", "items": [{"job_id": 9, "doc_id": 42}]},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "publish", "items": []},
        {"action": "archive", "items": [{"job_id": 9, "doc_id": 42}]},
    ],
)
def test_bulk_review_rejects_invalid_request_contract(payload, client_for):
    response = client_for(_profile("reviewer")).post(
        "/api/documents/review/bulk", json=payload
    )

    assert response.status_code == 400


def test_bulk_review_reports_valid_and_failed_items(
    monkeypatch, client_for
):
    marked = []

    class Result:
        def __init__(self, ok, state, error=None):
            self.ok = ok
            self.state = state
            self.error = error

        def __bool__(self):
            return self.ok

        def to_dict(self):
            return {"ok": self.ok, "state": self.state, "error": self.error}

    def publish(doc_id, **kwargs):
        assert kwargs["reviewer"] == "alice"
        return (
            Result(True, "published")
            if doc_id == 42
            else Result(False, "failed", "private publication detail")
        )

    monkeypatch.setattr(app_server, "publish_document", publish)
    monkeypatch.setattr(app_server, "mark_job_published", marked.append)

    response = client_for(_profile("reviewer")).post(
        "/api/documents/review/bulk",
        json={
            "action": "publish",
            "publish_mode": "new_version",
            "items": [
                {"job_id": 9, "doc_id": 42},
                {"job_id": 10, "doc_id": 43},
                "invalid-item",
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["updated"] == 1
    assert body["failed"] == 2
    assert marked == [9]


@pytest.mark.parametrize("action", ["reject", "delete"])
def test_bulk_review_executes_reject_and_delete_boundaries(
    action, monkeypatch, client_for
):
    calls = []
    monkeypatch.setattr(
        app_server,
        "reject_ingestion_job",
        lambda job_id, reason: calls.append(("reject_job", job_id, reason)) or False,
    )
    monkeypatch.setattr(
        app_server,
        "mark_job_rejected",
        lambda job_id: calls.append(("mark_rejected", job_id)),
    )
    monkeypatch.setattr(
        app_server,
        "reject_document",
        lambda doc_id, reviewer: calls.append(("reject_doc", doc_id, reviewer)),
    )
    monkeypatch.setattr(
        app_server,
        "delete_document_completely",
        lambda doc_id, reviewer: calls.append(("delete_doc", doc_id, reviewer)),
    )
    monkeypatch.setattr(
        app_server,
        "delete_ingestion_job",
        lambda job_id: calls.append(("delete_job", job_id)),
    )

    response = client_for(_profile("reviewer")).post(
        "/api/documents/review/bulk",
        json={
            "action": action,
            "reason": "duplicate",
            "items": [{"job_id": 9, "doc_id": 42}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "updated": 1,
        "pending": 0,
        "failed": 0,
        "failures": [],
    }
    if action == "reject":
        assert calls == [
            ("reject_job", 9, "duplicate"),
            ("mark_rejected", 9),
            ("reject_doc", 42, "alice"),
        ]
    else:
        assert calls == [("delete_doc", 42, "alice"), ("delete_job", 9)]


def test_citation_page_denies_before_resolving_any_file(monkeypatch, client_for):
    monkeypatch.setattr(
        app_server,
        "can_access_document",
        lambda _profile, _doc_id: (
            SimpleNamespace(
                allowed=False,
                reason="department_denied",
                security_level="confidential",
            ),
            None,
        ),
    )
    monkeypatch.setattr(
        app_server,
        "page_image_path",
        lambda *_args: pytest.fail("denied document must not resolve a file"),
    )

    response = client_for(_profile()).get("/api/files/documents/42/pages/3")

    assert response.status_code == 403
    assert response.json() == {"detail": "department_denied"}


def test_citation_page_returns_safe_placeholder_and_audits_confidential_read(
    monkeypatch, client_for
):
    audits = []
    monkeypatch.setattr(
        app_server,
        "can_access_document",
        lambda _profile, _doc_id: (
            SimpleNamespace(
                allowed=True, reason="allowed", security_level="confidential"
            ),
            SimpleNamespace(ten_file="bom.docx"),
        ),
    )
    monkeypatch.setattr(app_server, "page_image_path", lambda *_args: None)
    monkeypatch.setattr(app_server, "write_audit_log", lambda *args: audits.append(args))

    response = client_for(_profile()).get("/api/files/documents/42/pages/3")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "Doc 42" in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert audits[0][1] == "view_citation_page"


def test_citation_page_serves_authorized_rendered_image(
    monkeypatch, client_for, tmp_path
):
    image = tmp_path / "page.png"
    image.write_bytes(b"trusted-image")
    monkeypatch.setattr(
        app_server,
        "can_access_document",
        lambda _profile, _doc_id: (
            SimpleNamespace(allowed=True, reason="allowed", security_level="internal"),
            SimpleNamespace(ten_file="bom.pdf"),
        ),
    )
    monkeypatch.setattr(app_server, "page_image_path", lambda *_args: image)

    response = client_for(_profile()).get("/api/files/documents/42/pages/3")

    assert response.status_code == 200
    assert response.content == b"trusted-image"
    assert response.headers["content-type"] == "image/png"


def test_original_document_serves_only_authorized_resolved_file(
    monkeypatch, client_for, tmp_path
):
    source = tmp_path / "bom.pdf"
    source.write_bytes(b"trusted-pdf")
    record = SimpleNamespace(ten_file="download-name.pdf")
    audits = []
    monkeypatch.setattr(
        app_server,
        "can_access_document",
        lambda _profile, _doc_id: (
            SimpleNamespace(allowed=True, reason="allowed", security_level="internal"),
            record,
        ),
    )
    monkeypatch.setattr(app_server, "original_file_path", lambda _record: source)
    monkeypatch.setattr(app_server, "write_audit_log", lambda *args: audits.append(args))

    response = client_for(_profile()).get("/api/files/documents/42/original")

    assert response.status_code == 200
    assert response.content == b"trusted-pdf"
    assert "download-name.pdf" in response.headers["content-disposition"]
    assert audits[0][1] == "download_original"


def test_original_document_fails_closed_when_resolved_file_is_missing(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        app_server,
        "can_access_document",
        lambda _profile, _doc_id: (
            SimpleNamespace(allowed=True, reason="allowed", security_level="internal"),
            SimpleNamespace(ten_file="missing.pdf"),
        ),
    )
    monkeypatch.setattr(app_server, "original_file_path", lambda _record: None)

    response = client_for(_profile()).get("/api/files/documents/42/original")

    assert response.status_code == 404
    assert response.json() == {"detail": "Original file not found"}


def test_original_document_denies_before_resolving_file(monkeypatch, client_for):
    monkeypatch.setattr(
        app_server,
        "can_access_document",
        lambda _profile, _doc_id: (
            SimpleNamespace(
                allowed=False,
                reason="security_denied",
                security_level="confidential",
            ),
            SimpleNamespace(ten_file="secret.pdf"),
        ),
    )
    monkeypatch.setattr(
        app_server,
        "original_file_path",
        lambda *_args: pytest.fail("denied document must not resolve a file"),
    )

    response = client_for(_profile()).get("/api/files/documents/42/original")

    assert response.status_code == 403
    assert response.json() == {"detail": "security_denied"}


def test_chat_stream_without_done_event_emits_error_and_does_not_persist(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        app_server.requests,
        "post",
        lambda *_args, **_kwargs: _StreamResponse(
            [("metadata", {"debug_info": {}}), ("token", {"text": "partial"})]
        ),
    )
    monkeypatch.setattr(
        app_server,
        "save_chat_history",
        lambda **_kwargs: pytest.fail("incomplete answers must not be persisted"),
    )

    response = client_for(_profile()).post(
        "/api/chat/message", json={"session_id": "s1", "question": "hello"}
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event for event, _payload in events] == ["thinking", "delta", "error"]
    assert events[-1][1] == {"message": "RAG stream ended without a done event"}


def test_chat_upstream_error_event_stops_without_persisting(monkeypatch, client_for):
    monkeypatch.setattr(
        app_server.requests,
        "post",
        lambda *_args, **_kwargs: _StreamResponse(
            [("metadata", {"debug_info": {}}), ("error", {"message": "unavailable"})]
        ),
    )
    monkeypatch.setattr(
        app_server,
        "save_chat_history",
        lambda **_kwargs: pytest.fail("failed answers must not be persisted"),
    )

    response = client_for(_profile()).post(
        "/api/chat/message", json={"session_id": "s1", "question": "hello"}
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event for event, _payload in events] == ["thinking", "error"]
    assert events[-1][0] == "error"


def test_chat_persistence_failure_warns_but_completes_without_exception_detail_assertion(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        app_server.requests,
        "post",
        lambda *_args, **_kwargs: _StreamResponse(
            [("token", {"text": "safe answer"}), ("done", {"elapsed_ms": 12})]
        ),
    )
    monkeypatch.setattr(
        app_server,
        "save_chat_history",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("internal coordinates")),
    )

    response = client_for(_profile()).post(
        "/api/chat/message", json={"session_id": "s1", "question": "hello"}
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event for event, _payload in events] == [
        "thinking",
        "delta",
        "warning",
        "done",
    ]
    assert events[-1][1]["chat_id"] is None
    assert events[-1][1]["elapsed_ms"] == 12
