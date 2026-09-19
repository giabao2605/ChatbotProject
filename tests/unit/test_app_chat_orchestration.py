import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mech_chatbot.api import dependencies as api_dependencies
from mech_chatbot.api.routers import chat as chat_routes
from mech_chatbot.api.routers import documents as document_routes
from mech_chatbot.api.routers import operations as operation_routes
from mech_chatbot.application.chat_turn import (
    ChatCitation,
    ChatDelta,
    ChatDone,
    ChatError,
    ChatThinking,
)

pytestmark = pytest.mark.unit

app_server = pytest.importorskip("mech_chatbot.api.app_server")


def _profile():
    return {
        "user_id": 7,
        "username": "alice",
        "display_name": "Alice",
        "department": "CoKhi",
        "roles": ["viewer"],
        "allowed_departments": ["CoKhi"],
        "max_security_level": "confidential",
        "allowed_sites": ["HN"],
        "preferred_language": "vi",
    }


def _admin_profile():
    profile = _profile()
    profile["roles"] = ["platform_admin"]
    return profile


def _events(body):
    parsed = []
    for block in body.strip().split("\n\n"):
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            if line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if event:
            parsed.append((event, data))
    return parsed


def test_ingestion_eta_returns_flat_queue_metrics(monkeypatch):
    monkeypatch.setattr(
        document_routes,
        "queue_eta_seconds",
        lambda: {"pending": 2, "avg_seconds": 4.5, "eta_seconds": 9},
    )

    assert document_routes.ingestion_eta(profile={"roles": ["admin"]}) == {
        "pending": 2,
        "avg_seconds": 4.5,
        "eta_seconds": 9,
    }


def test_dashboard_requires_admin_role():
    with pytest.raises(api_dependencies.HTTPException) as exc_info:
        api_dependencies.require_any_role("admin")({"roles": ["viewer"]})

    assert exc_info.value.status_code == 403


def test_dashboard_endpoint_is_role_aware_for_viewer(
    monkeypatch,
    isolated_app_lifespan,
):
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "get_role_dashboard",
        lambda profile: {"document_lifecycle": {"effective": 4}, "usage": {"today_questions": 1}},
    )
    app_server.app.dependency_overrides[api_dependencies.current_profile] = _profile
    try:
        with TestClient(app_server.app) as client:
            response = client.get("/api/dashboard")
    finally:
        app_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "document_lifecycle": {"effective": 4},
        "usage": {"today_questions": 1},
    }


def test_external_ai_policy_endpoint_returns_metadata_only(
    monkeypatch,
    isolated_app_lifespan,
):
    profiles = [{
        "provider": "voyage",
        "default_model": "rerank-2.5-lite",
        "secret_reference": "env:VOYAGE_API_KEY",
        "allowed_surfaces": ["reranking"],
        "policy_version": "risk-accepted-v3",
    }]
    monkeypatch.setattr(
        operation_routes.external_ai_service,
        "list_external_ai_provider_profiles",
        lambda: profiles,
    )
    app_server.app.dependency_overrides[api_dependencies.current_profile] = _admin_profile
    try:
        with TestClient(app_server.app) as client:
            response = client.get("/api/settings/external-ai-policy")
    finally:
        app_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"profiles": profiles}


@pytest.fixture
def client():
    app_server.app.dependency_overrides[api_dependencies.csrf_profile] = _profile
    try:
        yield TestClient(app_server.app)
    finally:
        app_server.app.dependency_overrides.clear()


def test_chat_message_serializes_typed_runner_events_without_transport_or_repository_patching(
    monkeypatch, client
):
    citation = {
        "doc_id": 42,
        "page_no": 3,
        "file_name": "bom.pdf",
        "version_no": 1,
        "score": 0.91,
        "source_id": "D42P3",
    }
    calls = []

    class ScriptedRunner:
        def stream(self, command, actor):
            calls.append((command, actor))
            yield ChatThinking("Đang suy nghĩ")
            yield ChatDelta("Tra loi dung ")
            yield ChatDelta("[Nguồn: bom.pdf, Trang 3, Version 1, SourceID D42P3]")
            yield ChatCitation(citation)
            yield ChatDone(
                chat_id=123,
                ref_text="\nNguon: PDF",
                citations=(citation,),
                new_part_ids=("P123",),
                conversation_context={"topic": "bom"},
                elapsed_ms=25,
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        SimpleNamespace(chat_turn_runner=ScriptedRunner()),
    )

    response = client.post(
        "/api/chat/message",
        json={
            "session_id": "s1",
            "question": "  cau hoi ve BOM  ",
            "chat_history": [{"role": "user", "content": "prev"}],
            "current_part_ids": ["OLD"],
            "conversation_context": {"prev": True},
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0].question == "cau hoi ve BOM"
    assert calls[0][0].current_part_ids == ("OLD",)
    assert calls[0][1].user_id == 7
    assert calls[0][1].username == "alice"

    events = _events(response.text)
    assert [name for name, _data in events] == ["thinking", "delta", "delta", "citation", "done"]
    done = events[-1][1]
    assert done["chat_id"] == 123
    assert done["new_part_ids"] == ["P123"]
    assert done["conversation_context"] == {"topic": "bom"}
    assert done["citations"] == [citation]


def test_text_citation_has_download_without_preview(monkeypatch):
    monkeypatch.setattr(chat_routes, "page_has_vision", lambda _doc_id, _page_no: False)
    citations = chat_routes._citation_list([
        {
            "doc_id": 9,
            "trang": 1,
            "file_goc": "policy.docx",
            "score": 0.88,
            "source_id": "D9P1",
        }
    ])
    assert citations == [{
        "doc_id": 9,
        "page_no": 1,
        "file_name": "policy.docx",
        "score": 0.88,
        "source_id": "D9P1",
        "has_vision": False,
        "page_url": None,
        "original_url": "/api/files/documents/9/original",
    }]


def test_live_citation_filter_requires_exact_source_id():
    citations = [
        {"doc_id": 42, "page_no": 3, "file_name": "bom.pdf", "source_id": "D42P3"},
        {"doc_id": 42, "page_no": 4, "file_name": "bom.pdf", "source_id": "D42P4"},
    ]

    assert chat_routes._filter_citations_by_answer(citations, "Nguồn: bom.pdf, Trang 3") == []
    assert chat_routes._filter_citations_by_answer(
        citations,
        "Nguồn: bom.pdf, Trang 3, SourceID D42P3",
    ) == [citations[0]]


def test_chat_message_serializes_runner_error_without_persistence_seam_patching(
    monkeypatch, client
):
    class ScriptedRunner:
        def stream(self, _command, _actor):
            yield ChatThinking("Đang suy nghĩ")
            yield ChatError(
                code="rag_stream_error",
                message="RAG server busy",
                http_status=503,
                retryable=True,
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        SimpleNamespace(chat_turn_runner=ScriptedRunner()),
    )

    response = client.post("/api/chat/message", json={"session_id": "s1", "question": "hello"})

    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _data in events] == ["thinking", "error"]
    assert events[-1][1]["status"] == 503
    assert events[-1][1]["message"] == "RAG server busy"

def test_crag_pilot_replay_queue_is_bounded_and_drops_without_submitting(monkeypatch):
    from mech_chatbot.adapters import pilot_replay
    from mech_chatbot.evaluation.crag_pilot import PilotConfig, assign_pilot_route

    class FullCapacity:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

    class NoSubmitExecutor:
        def submit(self, *_args, **_kwargs):
            raise AssertionError("full queue must not submit")

    events = []
    route = assign_pilot_route(
        PilotConfig(
            experiment_id="exp-1",
            assignment_salt="test-salt",
            eligible_department="Technical",
            cohort_sha256="cohort-v1",
            control_url="http://control",
            candidate_url="http://candidate",
            control_deployment_id="control-1",
            candidate_deployment_id="candidate-1",
            snapshot_fingerprint="snapshot-v1",
        ),
        user_id="7",
        department="Technical",
        request_id="request-1",
    )
    pilot_replays = app_server.app.state.pilot_replays
    monkeypatch.setattr(pilot_replays, "capacity", FullCapacity())
    monkeypatch.setattr(pilot_replays, "executor", NoSubmitExecutor())
    monkeypatch.setattr(
        pilot_replay,
        "log_trace",
        lambda event, trace_id, **data: events.append((event, trace_id, data)),
    )

    submitted = pilot_replays.schedule(
        route,
        {"user_question": "sensitive", "user_id": 7},
        {"refusal": True, "query_type": "technical"},
        "trace-1",
        {"department": "Technical", "roles": ["viewer"], "allowed_sites": ["HQ"]},
    )

    assert submitted is False
    assert events[0][0] == "pilot_assignment"
    assert events[0][2]["assigned_arm"] == route.arm
    assert events[0][2]["cohort_sha256"] == "cohort-v1"
    assert events[-1][2]["status"] == "dropped"
    assert events[-1][2]["fallback_reason"] == "replay_queue_full_or_stopped"
