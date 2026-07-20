from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from mech_chatbot.api import rag_server
from mech_chatbot.rag.execution import (
    RagCitation,
    RagCompleted,
    RagPrepared,
    RagToken,
)


pytestmark = pytest.mark.unit

SERVICE_HEADERS = {"X-RAG-Service-Token": "test-service-token"}
VIEWER_PROFILE = {
    "user_id": 7,
    "username": "viewer-test",
    "department": "Technical",
    "roles": ["viewer"],
    "allowed_departments": ["Technical"],
    "max_security_level": "internal",
    "allowed_sites": ["HQ"],
}


@pytest.fixture
def rag_client(monkeypatch):
    from mech_chatbot.auth import core

    executor = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(rag_server, "RAG_REQUIRE_SERVICE_AUTH", True)
    monkeypatch.setattr(rag_server, "RAG_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setattr(rag_server, "_rag_ready", True)
    monkeypatch.setattr(rag_server, "_rag_executor", executor)
    monkeypatch.setattr(rag_server, "_rag_semaphore", rag_server.asyncio.Semaphore(2))
    monkeypatch.setattr(
        core,
        "load_user_profile",
        lambda user_id=None, username=None: {
            **VIEWER_PROFILE,
            "user_id": user_id or VIEWER_PROFILE["user_id"],
            "username": username or VIEWER_PROFILE["username"],
        },
    )

    client = TestClient(rag_server.app)
    try:
        yield client
    finally:
        client.close()
        executor.shutdown(wait=True)


def _successful_rag_events():
    citation = {
        "doc_id": 42,
        "page_no": 3,
        "file_name": "bom.pdf",
        "source_id": "D42P3",
    }
    yield RagPrepared(
        "legacy reference",
        ("page-3.png",),
        ("PART-42",),
        {"citation_docs": [citation], "route": "technical"},
    )
    yield RagToken("Answer with SourceID D42P3")
    yield RagCitation(citation)
    yield RagCompleted(
        "answered",
        "trace-public-endpoint",
        {"citation_docs": [citation], "route": "technical"},
    )


def test_service_token_is_required_before_chat_is_processed(rag_client, monkeypatch):
    opened = []
    monkeypatch.setattr(
        rag_server,
        "_open_rag_events",
        lambda *_args, **_kwargs: opened.append(True) or _successful_rag_events(),
    )

    response = rag_client.post("/chat", json={"user_question": "How?"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid RAG service token."}
    assert opened == []


def test_service_auth_fails_closed_when_the_server_token_is_missing(
    rag_client,
    monkeypatch,
):
    monkeypatch.setattr(rag_server, "RAG_SERVICE_TOKEN", "")

    response = rag_client.post("/chat", json={"user_question": "How?"})

    assert response.status_code == 503
    assert "RAG_SERVICE_TOKEN is not configured" in response.json()["detail"]


def test_chat_rejects_an_empty_question_at_the_http_boundary(rag_client):
    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "user_question": ""},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "user_question"]


def test_chat_reports_when_the_rag_runtime_is_not_ready(rag_client, monkeypatch):
    monkeypatch.setattr(rag_server, "_rag_ready", False)

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"response_language": "en", "user_question": "How?"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "RAG system is not loaded yet. Please wait and retry."
    }


def test_chat_rejects_an_inactive_user_profile(rag_client, monkeypatch):
    from mech_chatbot.auth import core

    monkeypatch.setattr(core, "load_user_profile", lambda **_identity: None)

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"username": "inactive", "user_question": "How?"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "User identity is invalid or inactive."}


def test_chat_returns_a_completed_rag_answer_and_attributed_sources(
    rag_client,
    monkeypatch,
):
    captured = {}

    def open_events(req, user_profile, **_kwargs):
        captured["request"] = req
        captured["profile"] = user_profile
        return _successful_rag_events()

    monkeypatch.setattr(
        rag_server,
        "_open_rag_events",
        open_events,
    )

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={
            "username": "viewer-test",
            "user_question": "How?",
            "user_roles": ["admin"],
            "allowed_departments": ["Finance"],
            "max_security_level": "secret",
            "allowed_sites": ["ALL"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["response"] == "Answer with SourceID D42P3"
    assert body["ref_images"] == ["page-3.png"]
    assert body["new_part_ids"] == ["PART-42"]
    assert "bom.pdf" in body["ref_text"]
    assert body["debug_info"]["route"] == "technical"
    assert body["elapsed_ms"] >= 0
    assert captured["request"].allowed_departments == ["Finance"]
    assert captured["profile"]["roles"] == ["viewer"]
    assert captured["profile"]["allowed_departments"] == ["Technical"]
    assert captured["profile"]["max_security_level"] == "internal"
    assert captured["profile"]["allowed_sites"] == ["HQ"]


def test_chat_reports_busy_without_opening_the_rag_pipeline(rag_client, monkeypatch):
    class BusySemaphore:
        async def acquire(self):
            raise rag_server.asyncio.TimeoutError

        def release(self):
            raise AssertionError("A permit was not acquired")

    opened = []
    monkeypatch.setattr(rag_server, "_rag_semaphore", BusySemaphore())
    monkeypatch.setattr(
        rag_server,
        "_open_rag_events",
        lambda *_args, **_kwargs: opened.append(True) or _successful_rag_events(),
    )

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "user_question": "How?"},
    )

    assert response.status_code == 503
    assert "Hệ thống đang bận" in response.json()["detail"]
    assert opened == []


def test_chat_busy_message_respects_the_requested_language(rag_client, monkeypatch):
    class BusySemaphore:
        async def acquire(self):
            raise rag_server.asyncio.TimeoutError

    monkeypatch.setattr(rag_server, "_rag_semaphore", BusySemaphore())

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"response_language": "en", "user_question": "How?"},
    )

    assert response.status_code == 503
    assert response.json()["detail"].startswith("System is busy")


def test_chat_translates_a_rag_failure_to_http_500(
    rag_client,
    monkeypatch,
):
    def failed_events():
        raise RuntimeError("provider unavailable")
        yield

    monkeypatch.setattr(
        rag_server,
        "_open_rag_events",
        lambda *_args, **_kwargs: failed_events(),
    )

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "user_question": "How?"},
    )

    assert response.status_code == 500


def test_stream_emits_the_public_sse_event_sequence(rag_client, monkeypatch):
    captured = {}

    def open_events(req, user_profile, **_kwargs):
        captured["request"] = req
        captured["profile"] = user_profile
        return _successful_rag_events()

    monkeypatch.setattr(
        rag_server,
        "_open_rag_events",
        open_events,
    )
    monkeypatch.setattr(rag_server, "pop_trace_stage_metrics", lambda _trace: {})

    with rag_client.stream(
        "POST",
        "/chat/stream",
        headers=SERVICE_HEADERS,
        json={
            "username": "viewer-test",
            "user_question": "How?",
            "user_roles": ["admin"],
            "allowed_departments": ["Finance"],
            "max_security_level": "secret",
            "allowed_sites": ["ALL"],
        },
    ) as response:
        transcript = "".join(response.iter_text())

    assert response.status_code == 200
    event_names = [
        line.removeprefix("event: ")
        for line in transcript.splitlines()
        if line.startswith("event: ")
    ]
    assert event_names == ["accepted", "metadata", "token", "citation", "done"]
    assert "Answer with SourceID D42P3" in transcript
    assert '"ok": true' in transcript
    assert captured["request"].allowed_departments == ["Finance"]
    assert captured["profile"]["roles"] == ["viewer"]
    assert captured["profile"]["allowed_departments"] == ["Technical"]
    assert captured["profile"]["max_security_level"] == "internal"
    assert captured["profile"]["allowed_sites"] == ["HQ"]


def test_stream_rejects_incomplete_pilot_replay_headers(rag_client):
    response = rag_client.post(
        "/chat/stream",
        headers={**SERVICE_HEADERS, "X-RAG-Pilot-Replay": "true"},
        json={"username": "viewer-test", "user_question": "How?"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid CRAG pilot replay headers"}


def test_stream_rejects_an_invalid_pilot_replay_signature(rag_client):
    replay_headers = {
        **SERVICE_HEADERS,
        "X-RAG-Pilot-Replay": "true",
        "X-RAG-Pilot-Experiment-ID": "experiment-1",
        "X-RAG-Matched-Pair-ID": "pair_1",
        "X-RAG-Original-Trace-ID": "trace-original",
        "X-RAG-Assigned-Arm": "candidate",
        "X-RAG-Pilot-Replay-Signature": "invalid",
        "X-RAG-Pilot-Payload-SHA256": "invalid",
        "X-RAG-Pilot-Replay-Nonce": "nonce-1",
        "X-RAG-Pilot-Replay-Expires": "1",
    }

    response = rag_client.post(
        "/chat/stream",
        headers=replay_headers,
        json={"username": "viewer-test", "user_question": "How?"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid CRAG pilot replay signature"}


def test_stream_reports_busy_before_starting_an_sse_response(rag_client, monkeypatch):
    class BusySemaphore:
        async def acquire(self):
            raise rag_server.asyncio.TimeoutError

    monkeypatch.setattr(rag_server, "_rag_semaphore", BusySemaphore())

    response = rag_client.post(
        "/chat/stream",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "user_question": "How?"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "RAG server busy"}


def test_stream_emits_an_error_event_when_the_pipeline_fails(rag_client, monkeypatch):
    def failed_events():
        raise RuntimeError("provider unavailable")
        yield

    monkeypatch.setattr(
        rag_server,
        "_open_rag_events",
        lambda *_args, **_kwargs: failed_events(),
    )

    with rag_client.stream(
        "POST",
        "/chat/stream",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "user_question": "How?"},
    ) as response:
        transcript = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: accepted" in transcript
    assert "event: error" in transcript


def test_session_list_is_scoped_to_the_authenticated_username(rag_client, monkeypatch):
    from mech_chatbot import services

    observed = []
    monkeypatch.setattr(
        services,
        "get_all_sessions",
        lambda **scope: observed.append(scope) or [{"session_id": "session-42"}],
    )

    response = rag_client.post(
        "/chat/sessions",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test"},
    )

    assert response.json() == {"sessions": [{"session_id": "session-42"}]}
    assert observed == [{"username": "viewer-test", "is_admin": False}]


def test_history_uses_the_server_side_profile_scope(rag_client, monkeypatch):
    from mech_chatbot import services

    observed = {}

    def fake_history(session_id, **scope):
        observed.update({"session_id": session_id, **scope})
        return [{"role": "assistant", "content": "saved answer"}]

    monkeypatch.setattr(services, "get_chat_history", fake_history)

    response = rag_client.post(
        "/chat/history",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "session_id": "session-42"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "messages": [{"role": "assistant", "content": "saved answer"}]
    }
    assert observed == {
        "session_id": "session-42",
        "username": "viewer-test",
        "is_admin": False,
        "user_clearance": "internal",
        "allowed_departments": ["Technical"],
        "allowed_sites": ["HQ"],
    }


def test_history_delete_is_scoped_to_the_authenticated_username(rag_client, monkeypatch):
    from mech_chatbot import services

    observed = []
    monkeypatch.setattr(
        services,
        "clear_chat_history",
        lambda session_id, **scope: observed.append((session_id, scope)) or 2,
    )

    response = rag_client.post(
        "/chat/history/delete",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "session_id": "session-42"},
    )

    assert response.json() == {"ok": True, "deleted": 2}
    assert observed == [
        ("session-42", {"username": "viewer-test", "is_admin": False})
    ]


def test_save_history_persists_only_sources_attributed_by_the_answer(
    rag_client,
    monkeypatch,
):
    from mech_chatbot import services

    saved = {"evidence": [], "sources": [], "audits": []}
    monkeypatch.setattr(services, "save_chat_history", lambda **_kwargs: 91)
    monkeypatch.setattr(
        services,
        "save_answer_evidence",
        lambda chat_id, docs: saved["evidence"].append((chat_id, docs)),
    )
    monkeypatch.setattr(
        services,
        "save_answer_sources",
        lambda chat_id, docs: saved["sources"].append((chat_id, docs)),
    )
    monkeypatch.setattr(
        services,
        "write_audit_log",
        lambda **kwargs: saved["audits"].append(kwargs),
    )
    retrieved_docs = [
        {
            "doc_id": 42,
            "trang": 3,
            "file_goc": "bom.pdf",
            "version_no": 5,
            "security_level": "confidential",
        },
        {
            "doc_id": 99,
            "trang": 1,
            "file_goc": "unused.pdf",
            "security_level": "internal",
        },
    ]

    response = rag_client.post(
        "/chat/history/save",
        headers=SERVICE_HEADERS,
        json={
            "username": "viewer-test",
            "session_id": "session-42",
            "user_msg": "How?",
            "bot_msg": "Answer with SourceID D42P3",
            "retrieved_docs": retrieved_docs,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "chat_id": 91}
    assert saved["evidence"] == [(91, retrieved_docs)]
    assert saved["sources"][0][0] == 91
    assert [item["doc_id"] for item in saved["sources"][0][1]] == [42]
    assert [item["action"] for item in saved["audits"]] == [
        "chat_query",
        "read_confidential",
    ]


@pytest.mark.parametrize(
    ("rating", "stored_rating"),
    [(1, 1), (0, -1), (-1, -1)],
)
def test_feedback_normalizes_the_public_rating_contract(
    rag_client,
    monkeypatch,
    rating,
    stored_rating,
):
    from mech_chatbot import services

    saved = []
    monkeypatch.setattr(
        services,
        "update_chat_feedback",
        lambda chat_id, value, voter_username=None: saved.append(
            (chat_id, value, voter_username)
        ) or True,
    )

    response = rag_client.post(
        "/chat/feedback",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "chat_id": 91, "rating": rating},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert saved == [(91, stored_rating, "viewer-test")]
