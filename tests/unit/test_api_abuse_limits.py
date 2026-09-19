import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient

from mech_chatbot.api import request_limits
from mech_chatbot.api.dependencies import csrf_profile
from mech_chatbot.api.routers import chat as chat_routes
from mech_chatbot.api.routers import documents as document_routes
from mech_chatbot.application.chat_turn import ChatDone
from mech_chatbot.config.settings import Settings


pytestmark = pytest.mark.unit


class _CappedReadProbe(BytesIO):
    def __init__(self, value: bytes, *, max_read: int):
        super().__init__(value)
        self.max_read = max_read

    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= self.max_read
        return super().read(size)


def test_app_rejects_oversized_upload_before_multipart_parsing() -> None:
    from mech_chatbot.api import app_server

    application = app_server.create_app(Settings.from_env({}))
    client = TestClient(application)
    response = client.post(
        "/api/chat/upload-image",
        content=b"x",
        headers={
            "Content-Length": str(18 * 1024 * 1024),
            "Content-Type": "multipart/form-data; boundary=boundary",
        },
    )

    assert response.status_code == 413


def test_request_body_limit_rejects_chunked_body_before_inner_app_retains_it():
    received_by_inner = bytearray()
    sent = []
    messages = iter(
        (
            {"type": "http.request", "body": b"12", "more_body": True},
            {"type": "http.request", "body": b"34", "more_body": False},
        )
    )

    async def inner(_scope, receive, send):
        while True:
            message = await receive()
            received_by_inner.extend(message.get("body") or b"")
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    middleware = request_limits.RequestBodyLimitMiddleware(
        inner,
        limits={"/upload": 3},
    )
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/upload",
                "headers": [],
            },
            receive,
            send,
        )
    )

    assert sent[0]["status"] == 413
    assert received_by_inner == b"12"


def test_chat_image_rejects_over_limit_without_an_unbounded_read(
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_routes, "CHAT_IMAGE_MAX_BYTES", 2)
    upload = UploadFile(
        filename="large.png",
        file=_CappedReadProbe(b"body-is-larger", max_read=3),
    )

    with pytest.raises(HTTPException) as exc:
        chat_routes.upload_chat_image(
            SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace()),
                client=SimpleNamespace(host="127.0.0.1"),
            ),
            upload,
            {"user_id": 7, "username": "alice"},
        )

    assert exc.value.status_code == 413
    assert exc.value.detail == "File is too large"


def test_document_upload_rejects_over_limit_before_enqueue(
    monkeypatch,
) -> None:
    monkeypatch.setattr(document_routes, "DOCUMENT_UPLOAD_MAX_BYTES", 2)

    class UploadRuntime:
        def preflight(self, **_kwargs):
            return None

        def enqueue(self, *_args):
            pytest.fail("oversized upload must not be enqueued")

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                runtime=SimpleNamespace(document_upload=UploadRuntime())
            )
        )
    )
    upload = UploadFile(
        filename="large.pdf",
        file=_CappedReadProbe(b"body-is-larger", max_read=3),
    )

    with pytest.raises(HTTPException) as exc:
        document_routes.documents_upload(
            request=request,
            file=upload,
            thu_muc="CoKhi",
            domain=None,
            security_level=None,
            cong_doan=None,
            site=None,
            meta_json=None,
            extra_departments_json=None,
            profile={
                "user_id": 7,
                "username": "alice",
                "roles": ["uploader"],
                "allowed_departments": ["CoKhi"],
            },
        )

    assert exc.value.status_code == 413
    assert exc.value.detail == "Tệp quá lớn (giới hạn 100MB): large.pdf"


def test_document_batch_rejects_aggregate_limit_before_enqueue(
    monkeypatch,
) -> None:
    monkeypatch.setattr(document_routes, "DOCUMENT_UPLOAD_MAX_BYTES", 4)
    monkeypatch.setattr(document_routes, "DOCUMENT_BATCH_MAX_BYTES", 5)

    class UploadRuntime:
        def preflight(self, **_kwargs):
            return None

        def enqueue_batch(self, *_args):
            pytest.fail("oversized batch must not be enqueued")

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                runtime=SimpleNamespace(document_upload=UploadRuntime())
            )
        )
    )
    files = [
        UploadFile(
            filename="first.pdf",
            file=_CappedReadProbe(b"123", max_read=5),
        ),
        UploadFile(
            filename="second.pdf",
            file=_CappedReadProbe(b"456", max_read=3),
        ),
    ]

    with pytest.raises(HTTPException) as exc:
        document_routes.documents_upload_batch(
            request=request,
            files=files,
            thu_muc="CoKhi",
            domain=None,
            security_level=None,
            cong_doan=None,
            site=None,
            meta_json=None,
            extra_departments_json=None,
            assignments_json=None,
            profile={
                "user_id": 7,
                "username": "alice",
                "roles": ["uploader"],
                "allowed_departments": ["CoKhi"],
            },
        )

    assert exc.value.status_code == 413
    assert exc.value.detail == "Tổng dung lượng upload vượt quá giới hạn 200MB"


def test_document_batch_reports_a_per_file_limit_without_enqueuing_it(
    monkeypatch,
) -> None:
    monkeypatch.setattr(document_routes, "DOCUMENT_UPLOAD_MAX_BYTES", 2)
    monkeypatch.setattr(document_routes, "DOCUMENT_BATCH_MAX_BYTES", 10)
    captured = []

    class UploadRuntime:
        def preflight(self, **_kwargs):
            return None

        def enqueue_batch(self, commands, _actor):
            captured.extend(commands)
            return SimpleNamespace(jobs=(), errors=())

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                runtime=SimpleNamespace(document_upload=UploadRuntime())
            )
        ),
        client=SimpleNamespace(host="127.0.0.1"),
    )
    response = document_routes.documents_upload_batch(
        request=request,
        files=[
            UploadFile(
                filename="large.pdf",
                file=_CappedReadProbe(b"larger", max_read=3),
            )
        ],
        thu_muc="CoKhi",
        domain=None,
        security_level=None,
        cong_doan=None,
        site=None,
        meta_json=None,
        extra_departments_json=None,
        assignments_json=None,
        profile={
            "user_id": 8,
            "username": "batch-user",
            "roles": ["uploader"],
            "allowed_departments": ["CoKhi"],
        },
    )

    assert response["created"] == 0
    assert response["errors"] == [
        {
            "file_name": "large.pdf",
            "error": "Tệp quá lớn (giới hạn 100MB): large.pdf",
        }
    ]
    assert captured == []


def test_browser_chat_rate_limit_runs_before_the_chat_runner(
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_routes, "CHAT_REQUESTS_PER_WINDOW", 1)
    times = iter((0.0, 0.0, 61.0))
    monkeypatch.setattr(request_limits, "monotonic", lambda: next(times))
    calls = []

    class Runner:
        def stream(self, *_args):
            calls.append(True)
            yield ChatDone(
                chat_id=None,
                ref_text="",
                citations=(),
                new_part_ids=(),
                conversation_context=None,
                elapsed_ms=0,
            )

    application = FastAPI()
    application.state.runtime = SimpleNamespace(chat_turn_runner=Runner())
    application.include_router(chat_routes.router)
    application.dependency_overrides[csrf_profile] = lambda: {
        "user_id": 7,
        "username": "alice",
        "roles": ["viewer"],
    }

    with TestClient(application) as client:
        first = client.post(
            "/api/chat/message",
            json={"session_id": "session-1", "question": "First"},
        )
        limited = client.post(
            "/api/chat/message",
            json={"session_id": "session-1", "question": "Second"},
        )
        after_window = client.post(
            "/api/chat/message",
            json={"session_id": "session-1", "question": "Third"},
        )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert after_window.status_code == 200
    assert calls == [True, True]


def test_rate_limit_falls_back_to_direct_ip_without_a_user() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    first_ip = SimpleNamespace(
        app=app,
        client=SimpleNamespace(host="127.0.0.1"),
    )
    second_ip = SimpleNamespace(
        app=app,
        client=SimpleNamespace(host="10.0.0.8"),
    )

    request_limits.enforce_request_rate_limit(
        first_ip,
        {},
        scope="anonymous-test",
        limit=1,
    )
    with pytest.raises(HTTPException) as exc:
        request_limits.enforce_request_rate_limit(
            first_ip,
            {},
            scope="anonymous-test",
            limit=1,
        )
    request_limits.enforce_request_rate_limit(
        second_ip,
        {},
        scope="anonymous-test",
        limit=1,
    )

    assert exc.value.status_code == 429


def test_rate_limit_capacity_fails_closed_without_evicting_active_keys() -> None:
    limiter = request_limits._WindowLimiter(
        limit=1,
        window_seconds=60,
        max_keys=1,
    )

    assert limiter.allow("first") is True
    assert limiter.allow("second") is False
    assert limiter.allow("first") is False


def test_chat_image_rate_limit_runs_before_reading_or_storing(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(chat_routes, "CHAT_IMAGE_UPLOADS_PER_WINDOW", 1)
    monkeypatch.setattr(chat_routes, "data_raw_root", lambda: tmp_path)
    monkeypatch.setattr(chat_routes, "_sign_image_upload", lambda *_args: "token")
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace()),
        client=SimpleNamespace(host="127.0.0.1"),
    )
    rotated_ip_request = SimpleNamespace(
        app=request.app,
        client=SimpleNamespace(host="10.0.0.8"),
    )
    profile = {"user_id": 7, "username": "alice"}

    first = chat_routes.upload_chat_image(
        request,
        UploadFile(filename="first.png", file=BytesIO(b"first")),
        profile,
    )
    with pytest.raises(HTTPException) as exc:
        chat_routes.upload_chat_image(
            rotated_ip_request,
            UploadFile(
                filename="second.png",
                file=_CappedReadProbe(b"second", max_read=0),
            ),
            profile,
        )

    assert first["ok"] is True
    assert exc.value.status_code == 429
    assert len(list((tmp_path / "Chat_Images").iterdir())) == 1


def test_app_server_image_wrapper_forwards_the_request(monkeypatch) -> None:
    from mech_chatbot.api import app_server

    captured = []
    monkeypatch.setattr(
        chat_routes,
        "upload_chat_image",
        lambda *args: captured.append(args) or {"ok": True},
    )
    request, file, profile = object(), object(), {"user_id": 7}

    response = app_server.upload_chat_image(request, file, profile)

    assert response == {"ok": True}
    assert captured == [(request, file, profile)]


def test_document_upload_routes_share_a_limit_before_reading(
    monkeypatch,
) -> None:
    monkeypatch.setattr(document_routes, "DOCUMENT_UPLOADS_PER_WINDOW", 1)

    class UploadRuntime:
        def preflight(self, **_kwargs):
            return None

        def enqueue(self, command, _actor):
            return SimpleNamespace(job_id=1, file_name=command.file_name)

        def enqueue_batch(self, *_args):
            pytest.fail("limited batch must not be enqueued")

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                runtime=SimpleNamespace(document_upload=UploadRuntime())
            )
        ),
        client=SimpleNamespace(host="127.0.0.1"),
    )
    profile = {
        "user_id": 7,
        "username": "alice",
        "roles": ["uploader"],
        "allowed_departments": ["CoKhi"],
    }
    first = document_routes.documents_upload(
        request=request,
        file=UploadFile(filename="first.pdf", file=BytesIO(b"first")),
        thu_muc="CoKhi",
        domain=None,
        security_level=None,
        cong_doan=None,
        site=None,
        meta_json=None,
        extra_departments_json=None,
        profile=profile,
    )

    with pytest.raises(HTTPException) as exc:
        document_routes.documents_upload_batch(
            request=request,
            files=[
                UploadFile(
                    filename="second.pdf",
                    file=_CappedReadProbe(b"second", max_read=0),
                )
            ],
            thu_muc="CoKhi",
            domain=None,
            security_level=None,
            cong_doan=None,
            site=None,
            meta_json=None,
            extra_departments_json=None,
            assignments_json=None,
            profile=profile,
        )

    assert first["ok"] is True
    assert exc.value.status_code == 429
