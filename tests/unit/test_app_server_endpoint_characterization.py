import inspect
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mech_chatbot.api import dependencies as api_dependencies
from mech_chatbot.api.routers import documents as document_routes
from mech_chatbot.application.chat_turn import (
    ChatDelta,
    ChatDone,
    ChatError,
    ChatThinking,
    ChatWarning,
)
from mech_chatbot.application.document_review import (
    PublicationCoordinator,
    PublicationOutcome,
    ReviewDocuments,
)
from mech_chatbot.application.document_upload import (
    UploadBatchResult,
    UploadFailure,
    UploadReceipt,
)
from mech_chatbot.application.protected_files import AuthorizedFile, ProtectedFileError


pytestmark = pytest.mark.unit

app_server = pytest.importorskip("mech_chatbot.api.app_server")


@pytest.mark.parametrize(
    ("name", "parameters"),
    [
        ("_sources_for_chat_ids", ["chat_ids"]),
        ("_decorate_history_messages", ["messages"]),
        ("history", ["body", "profile"]),
        ("upload_chat_image", ["request", "file", "profile"]),
        ("citation_page", ["doc_id", "page_no", "profile"]),
        ("original_document", ["doc_id", "profile"]),
        ("chat_image", ["image_id", "profile"]),
        (
            "documents_upload_batch",
            [
                "files",
                "thu_muc",
                "domain",
                "security_level",
                "cong_doan",
                "site",
                "meta_json",
                "extra_departments_json",
                "assignments_json",
                "profile",
            ],
        ),
        ("documents_review_bulk", ["body", "profile"]),
        ("document_publish_new_version", ["doc_id", "response", "profile"]),
        ("document_publish_new_variant", ["doc_id", "response", "profile"]),
        ("document_publish_standalone", ["doc_id", "response", "profile"]),
        ("ingestion_publish", ["job_id", "response", "profile"]),
        ("feedback_classify", ["feedback_id", "body", "profile"]),
    ],
)
def test_app_server_compatibility_wrappers_keep_legacy_call_shape(name, parameters):
    assert list(inspect.signature(getattr(app_server, name)).parameters) == parameters


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
        app_server.app.dependency_overrides[api_dependencies.current_profile] = lambda: profile
        app_server.app.dependency_overrides[api_dependencies.csrf_profile] = lambda: profile
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
    captured = []

    class Upload:
        def preflight(self, **_kwargs):
            return None

        def enqueue_batch(self, commands, actor):
            captured.append((commands, actor))
            return UploadBatchResult(
                jobs=(UploadReceipt(91, "bom.pdf", "CoKhi"),),
                errors=(
                    UploadFailure(
                        "unauthorized",
                        "secret.pdf",
                        "Không có quyền upload vào phòng ban Finance",
                    ),
                ),
                created=1,
                failed=1,
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, document_upload=Upload()),
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
    commands, actor = captured[0]
    assert [command.owner_department for command in commands] == ["CoKhi", "Finance"]
    assert commands[0].shared_departments == ("Shared",)
    assert commands[0].upload_metadata == {"owner": "qa"}
    assert commands[0].site == "HN"
    assert actor.username == "alice"


def test_batch_upload_rejects_viewer_before_storing_files(monkeypatch, client_for):
    client = client_for(_profile("viewer"))

    class Upload:
        def enqueue_batch(self, *_args):
            pytest.fail("unauthorized upload must not reach the use case")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, document_upload=Upload()),
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

    class Upload:
        def enqueue_batch(self, *_args):
            pytest.fail("oversized batch must not reach the use case")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, document_upload=Upload()),
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
    class Upload:
        def preflight(self, **_kwargs):
            return None

        def enqueue_batch(self, _commands, _actor):
            return UploadBatchResult(
                jobs=(),
                errors=(
                    UploadFailure("enqueue_failed", "bom.pdf", "Không tạo được job"),
                ),
                created=0,
                failed=1,
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, document_upload=Upload()),
    )

    response = client_for(_profile("uploader")).post(
        "/api/documents/upload-batch",
        files=[("files", ("bom.pdf", b"pdf", "application/pdf"))],
        data={"thu_muc": "CoKhi"},
    )

    assert response.status_code == 200
    assert response.json()["created"] == 0
    assert response.json()["failed"] == 1


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

    monkeypatch.setattr(document_routes, "validate_document_metadata_actor", validate_actor)
    monkeypatch.setattr(document_routes, "update_document_common_metadata", update)

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
    class Review:
        def execute(self, *_args):
            pytest.fail("viewer must not reach the review use case")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, review_documents=Review()),
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
    published = []

    class Publication:
        def resolve_latest_doc_id(self, _job_id):
            return 42

        def publish_document(self, command, actor):
            published.append((command, actor))
            if command.doc_id == 42:
                return PublicationOutcome(True, "published", {"ok": True})
            return PublicationOutcome(
                False,
                "failed",
                {"ok": False, "error": "private publication detail"},
                "private publication detail",
            )

        def mark_job_published(self, _job_id):
            return None

    class ReviewStore:
        pass

    review = ReviewDocuments(
        review_store=ReviewStore(),
        publication=PublicationCoordinator(publication=Publication()),
    )
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, review_documents=review),
    )

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
    assert [(call.doc_id, actor.username) for call, actor in published] == [
        (42, "alice"),
        (43, "alice"),
    ]


@pytest.mark.parametrize("action", ["reject", "delete"])
def test_bulk_review_executes_reject_and_delete_boundaries(
    action, monkeypatch, client_for
):
    calls = []

    class ReviewStore:
        def reject_job(self, job_id, reason):
            calls.append(("reject_job", job_id, reason))
            return False

        def mark_job_rejected(self, job_id):
            calls.append(("mark_rejected", job_id))

        def reject_document(self, doc_id, reviewer):
            calls.append(("reject_doc", doc_id, reviewer))

        def delete_document(self, doc_id, reviewer):
            calls.append(("delete_doc", doc_id, reviewer))

        def delete_job(self, job_id):
            calls.append(("delete_job", job_id))

    class Publication:
        def resolve_latest_doc_id(self, _job_id):
            return 42

        def publish_document(self, *_args):
            pytest.fail("reject/delete must not reach publication")

        def mark_job_published(self, _job_id):
            pytest.fail("reject/delete must not mark publication")

    review = ReviewDocuments(
        review_store=ReviewStore(),
        publication=PublicationCoordinator(publication=Publication()),
    )
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, review_documents=review),
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
    class Resolver:
        def resolve(self, _reference, _actor):
            raise ProtectedFileError("unauthorized", "department_denied")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, protected_file_resolver=Resolver()),
    )

    response = client_for(_profile()).get("/api/files/documents/42/pages/3")

    assert response.status_code == 403
    assert response.json() == {"detail": "department_denied"}


def test_citation_page_returns_safe_placeholder_and_audits_confidential_read(
    monkeypatch, client_for
):
    class Resolver:
        def resolve(self, _reference, _actor):
            return AuthorizedFile(path=None, placeholder=True)

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, protected_file_resolver=Resolver()),
    )

    response = client_for(_profile()).get("/api/files/documents/42/pages/3")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "Doc 42" in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_citation_page_serves_authorized_rendered_image(
    monkeypatch, client_for, tmp_path
):
    image = tmp_path / "page.png"
    image.write_bytes(b"trusted-image")
    class Resolver:
        def resolve(self, _reference, _actor):
            return AuthorizedFile(path=image)

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, protected_file_resolver=Resolver()),
    )

    response = client_for(_profile()).get("/api/files/documents/42/pages/3")

    assert response.status_code == 200
    assert response.content == b"trusted-image"
    assert response.headers["content-type"] == "image/png"


def test_original_document_serves_only_authorized_resolved_file(
    monkeypatch, client_for, tmp_path
):
    source = tmp_path / "bom.pdf"
    source.write_bytes(b"trusted-pdf")
    class Resolver:
        def resolve(self, _reference, _actor):
            return AuthorizedFile(path=source, filename="download-name.pdf")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, protected_file_resolver=Resolver()),
    )

    response = client_for(_profile()).get("/api/files/documents/42/original")

    assert response.status_code == 200
    assert response.content == b"trusted-pdf"
    assert "download-name.pdf" in response.headers["content-disposition"]


def test_original_document_fails_closed_when_resolved_file_is_missing(
    monkeypatch, client_for
):
    class Resolver:
        def resolve(self, _reference, _actor):
            raise ProtectedFileError("not_found", "Original file not found")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, protected_file_resolver=Resolver()),
    )

    response = client_for(_profile()).get("/api/files/documents/42/original")

    assert response.status_code == 404
    assert response.json() == {"detail": "Original file not found"}


def test_original_document_denies_before_resolving_file(monkeypatch, client_for):
    class Resolver:
        def resolve(self, _reference, _actor):
            raise ProtectedFileError("unauthorized", "security_denied")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, protected_file_resolver=Resolver()),
    )

    response = client_for(_profile()).get("/api/files/documents/42/original")

    assert response.status_code == 403
    assert response.json() == {"detail": "security_denied"}


def test_chat_stream_serializes_incomplete_runner_error_after_visible_delta(
    monkeypatch, client_for
):
    class Runner:
        def stream(self, _command, _actor):
            yield ChatThinking("Đang suy nghĩ")
            yield ChatDelta("partial")
            yield ChatError(
                code="rag_stream_incomplete",
                message="RAG stream ended without a done event",
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, chat_turn_runner=Runner()),
    )

    response = client_for(_profile()).post(
        "/api/chat/message", json={"session_id": "s1", "question": "hello"}
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert [event for event, _payload in events] == ["thinking", "delta", "error"]
    assert events[-1][1] == {"message": "RAG stream ended without a done event"}


def test_chat_stream_serializes_upstream_runner_error(monkeypatch, client_for):
    class Runner:
        def stream(self, _command, _actor):
            yield ChatThinking("Đang suy nghĩ")
            yield ChatError(code="rag_stream_error", message="unavailable")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, chat_turn_runner=Runner()),
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
    class Runner:
        def stream(self, _command, _actor):
            yield ChatThinking("Đang suy nghĩ")
            yield ChatDelta("safe answer")
            yield ChatWarning(
                code="chat_persistence_failed",
                message="Không lưu được lịch sử chat",
            )
            yield ChatDone(
                chat_id=None,
                ref_text="",
                citations=(),
                new_part_ids=(),
                conversation_context=None,
                elapsed_ms=12,
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, chat_turn_runner=Runner()),
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
