from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mech_chatbot.api import rag_server
from mech_chatbot.config.settings import Settings
from mech_chatbot.rag.execution import (
    RagCitation,
    RagCompleted,
    RagPrepared,
    RagToken,
)
from mech_chatbot.rag.execution_contracts import RagRuntimeContract
from .test_structured_evidence_repository import store
from .test_graph_v2_repository import connection


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
    application = rag_server.create_rag_app(
        Settings(
            RAG_REQUIRE_SERVICE_AUTH=True,
            RAG_SERVICE_TOKEN="test-service-token",
        )
    )
    state = application.state.rag_server
    runtime = SimpleNamespace(
        executor=object(),
        thread_pool=executor,
        semaphore=rag_server.asyncio.Semaphore(2),
        retrieval=SimpleNamespace(
            client=object(),
            collection_name="phase6-test-collection",
        ),
        runtime_contract=RagRuntimeContract.from_mapping(
            {
                "execution_context": "production",
                "evaluation_force_ambiguous": False,
                "request_deadline_seconds": 120.0,
            }
        ),
    )
    application.state.rag_server = replace(
        state,
        runtime=runtime,
        database_runtime=SimpleNamespace(engine=object()),
        ready=True,
    )
    monkeypatch.setattr(
        core,
        "load_user_profile",
        lambda user_id=None, username=None: {
            **VIEWER_PROFILE,
            "user_id": user_id or VIEWER_PROFILE["user_id"],
            "username": username or VIEWER_PROFILE["username"],
        },
    )

    client = TestClient(application)
    try:
        yield client
    finally:
        client.close()
        executor.shutdown(wait=True)


def _replace_server_state(client, **changes):
    current = client.app.state.rag_server
    client.app.state.rag_server = replace(current, **changes)


def _replace_runtime(client, **changes):
    state = client.app.state.rag_server
    values = {
        "executor": state.runtime.executor,
        "thread_pool": state.runtime.thread_pool,
        "semaphore": state.runtime.semaphore,
        "retrieval": state.runtime.retrieval,
        "runtime_contract": state.runtime.runtime_contract,
    }
    values.update(changes)
    _replace_server_state(client, runtime=SimpleNamespace(**values))


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_scope_comparison_cannot_use_production_or_pilot_contract(path, rag_client, monkeypatch):
    monkeypatch.setattr(rag_server, '_open_rag_events',
                        lambda *a, **kw: pytest.fail('V2 must be rejected before executor/source access'))
    response = rag_client.post(path, headers=SERVICE_HEADERS, json={
        'username':'viewer-test', 'user_question':'Compare',
        'scope_comparison':{'scope_a':'a','scope_b':'b'}})
    assert response.status_code == 409


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_scope_comparison_requires_server_resolved_reviewer(path, rag_client):
    _replace_runtime(rag_client, runtime_contract=RagRuntimeContract('evaluation', False, 120))
    response = rag_client.post(path, headers=SERVICE_HEADERS, json={
        'username':'viewer-test', 'user_question':'Compare', 'user_roles':['admin'],
        'scope_comparison':{'scope_a':'a','scope_b':'b'}})
    assert response.status_code == 403


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_scope_comparison_body_rejects_client_approval_and_blank_scopes(path, rag_client):
    for selection in ({'scope_a':' ','scope_b':'b'}, {'scope_a':'a','scope_b':'b','approved':True}):
        response = rag_client.post(path, headers=SERVICE_HEADERS,
                                  json={'user_question':'Compare', 'scope_comparison':selection})
        assert response.status_code == 422


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_graph_query_v2_is_rejected_in_production_before_sql_or_executor(path,rag_client,monkeypatch):
    monkeypatch.setattr(rag_server,'_open_rag_events',lambda *a,**k:pytest.fail('must not enter pipeline'))
    response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_question':'Contains what?',
        'graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}})
    assert response.status_code==409


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_graph_v2_http_runs_the_real_executor_and_sql_policy(path,rag_client,connection,monkeypatch):
    from contextlib import contextmanager
    from sqlalchemy import create_engine,event
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution import DefaultRagExecutor
    from mech_chatbot.db.repositories import graph_v2
    from .test_graph_v2_repository import SqliteConnection
    connection.conn.commit()
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    import json
    @event.listens_for(shared,'connect')
    def functions(dbapi,record):
        dbapi.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        def json_value(value,path):
            item=json.loads(value) if value else {}
            for key in path[2:].split('.'):item=item.get(key) if isinstance(item,dict) else None
            return item
        dbapi.create_function('JSON_VALUE',2,json_value)
    with shared.connect() as target:
        connection.conn.connection.driver_connection.backup(target.connection.driver_connection)
    class Engine:
        @contextmanager
        def connect(self):
            with shared.connect() as conn:yield SqliteConnection(conn)
    engine=Engine()
    monkeypatch.setattr(core,'load_user_profile',lambda **kwargs:{**VIEWER_PROFILE,'roles':['reviewer'],
        'allowed_departments':['tech'],'allowed_sites':['factory']})
    _replace_runtime(rag_client,executor=DefaultRagExecutor(),runtime_contract=RagRuntimeContract('evaluation',False,120))
    _replace_server_state(rag_client,database_runtime=SimpleNamespace(engine=engine))
    try:
        response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_id':7,'user_question':'Contains what?',
            'graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}})
        assert response.status_code==200 and 'part:p' in response.text
        if path=='/chat':
            assert response.json()['debug_info']['graph_v2']['status']=='complete'
        else:
            assert 'event: citation' in response.text and 'event: done' in response.text
    finally:shared.dispose()


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_reviewed_scope_comparison_runs_real_executor_through_chat_http(path, rag_client, store, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution import DefaultRagExecutor
    from .test_rag_scope_comparison import approved_pair
    from .test_structured_evidence_repository import PROFILE

    approved_pair(store, values=('0','8'))
    shared = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    with store.connect() as original, shared.connect() as target:
        original.connection.driver_connection.backup(target.connection.driver_connection)
    _replace_server_state(rag_client, database_runtime=SimpleNamespace(engine=shared))
    _replace_runtime(rag_client, executor=DefaultRagExecutor(),
                     runtime_contract=RagRuntimeContract('evaluation', False, 120))
    monkeypatch.setattr(core, 'load_user_profile', lambda **kw: PROFILE)
    try:
        response = rag_client.post(path, headers=SERVICE_HEADERS, json={
            'user_id':7, 'user_question':'So sánh hai kỳ', 'scope_comparison':{'scope_a':'a','scope_b':'b'}})
        assert response.status_code == 200
        if path == '/chat':
            math = response.json()['debug_info']['math_v2']
            assert 'Sheet CSV' in response.json()['ref_text'] and 'Trang None' not in response.json()['ref_text']
        else:
            import json
            events = [(block.splitlines()[0], json.loads(block.splitlines()[1][6:]))
                      for block in response.text.strip().split('\n\n')]
            assert events[-1][0] == 'event: done'
            math = next(body['debug_info']['math_v2'] for name, body in events if name == 'event: metadata')
            citations = [body for name, body in events if name == 'event: citation']
            assert len(citations) == 2 and all(card['page_no'] is None for card in citations)
        assert math['status'] == 'partial' and math['domain_step_count'] == 4
        assert [s['display_value'] for s in math['steps'][:3]] == ['0.00','8.00','8.00']
        assert math['steps'][3]['value'] is None and not math['accepted'] and math['offline_only']
    finally:
        shared.dispose()


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_comparison_storage_failure_never_exposes_database_or_source_details(path, rag_client, monkeypatch):
    from mech_chatbot.auth import core
    from .test_structured_evidence_repository import PROFILE
    monkeypatch.setattr(core, 'load_user_profile', lambda **kw: PROFILE)
    _replace_runtime(rag_client, runtime_contract=RagRuntimeContract('evaluation', False, 120))
    def unavailable(*args, **kwargs):
        raise RuntimeError('private database SELECT payroll.xlsx')
    monkeypatch.setattr(rag_server, '_open_rag_events', unavailable)
    response = rag_client.post(path, headers=SERVICE_HEADERS, json={
        'user_id':7, 'user_question':'Compare', 'scope_comparison':{'scope_a':'a','scope_b':'b'}})
    assert 'payroll' not in response.text and 'SELECT' not in response.text
    assert (response.status_code == 500 if path == '/chat' else 'event: error' in response.text)


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


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_rag_chat_rate_limit_runs_before_the_pipeline(
    path,
    rag_client,
    monkeypatch,
):
    monkeypatch.setattr(rag_server, "RAG_REQUESTS_PER_WINDOW", 1)
    opened = []
    monkeypatch.setattr(
        rag_server,
        "_open_rag_events",
        lambda *_args, **_kwargs: opened.append(True) or _successful_rag_events(),
    )

    first = rag_client.post(
        path,
        headers=SERVICE_HEADERS,
        json={"username": "rate-limit-user", "user_question": "First"},
    )
    limited = rag_client.post(
        path,
        headers=SERVICE_HEADERS,
        json={"username": "rate-limit-user", "user_question": "Second"},
    )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert opened == [True]


def test_service_auth_fails_closed_when_the_server_token_is_missing(
    rag_client,
    monkeypatch,
):
    state = rag_client.app.state.rag_server
    _replace_server_state(
        rag_client,
        process_settings=replace(state.process_settings, service_token=""),
    )

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
    _replace_server_state(rag_client, ready=False)

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


def test_history_returns_503_when_database_runtime_is_unavailable(rag_client):
    _replace_server_state(rag_client, database_runtime=None)

    response = rag_client.post(
        "/chat/sessions",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "RAG database runtime is not ready."
    }


def test_chat_executor_binds_and_resets_repository_context(
    rag_client,
    monkeypatch,
):
    from mech_chatbot.config.repository_runtime import (
        current_qdrant_runtime,
        current_repository_engine,
    )

    database_engine = object()
    qdrant_client = object()
    collection_name = "phase6-chat-context"
    _replace_server_state(
        rag_client,
        database_runtime=SimpleNamespace(engine=database_engine),
    )
    _replace_runtime(
        rag_client,
        retrieval=SimpleNamespace(
            client=qdrant_client,
            collection_name=collection_name,
        ),
    )
    observed = []

    def run_sync(*_args):
        observed.append(
            (
                current_repository_engine(),
                current_qdrant_runtime(),
            )
        )
        return rag_server.ChatResponse(response="bound")

    monkeypatch.setattr(rag_server, "_run_rag_sync", run_sync)

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "user_question": "How?"},
    )

    assert response.status_code == 200
    assert observed == [
        (database_engine, (qdrant_client, collection_name))
    ]
    reset = rag_client.app.state.rag_server.runtime.thread_pool.submit(
        lambda: (
            current_repository_engine(),
            current_qdrant_runtime(),
        )
    ).result()
    assert reset == (None, (None, None))


def test_stream_executor_binds_and_resets_repository_context(
    rag_client,
    monkeypatch,
):
    from mech_chatbot.config.repository_runtime import (
        current_qdrant_runtime,
        current_repository_engine,
    )

    database_engine = object()
    qdrant_client = object()
    collection_name = "phase6-stream-context"
    _replace_server_state(
        rag_client,
        database_runtime=SimpleNamespace(engine=database_engine),
    )
    _replace_runtime(
        rag_client,
        retrieval=SimpleNamespace(
            client=qdrant_client,
            collection_name=collection_name,
        ),
    )
    observed = []

    def open_events(*_args, **_kwargs):
        observed.append(
            (
                current_repository_engine(),
                current_qdrant_runtime(),
            )
        )
        return iter(
            [
                RagCompleted(
                    "answered",
                    "phase6-stream",
                    {"citation_docs": []},
                )
            ]
        )

    monkeypatch.setattr(rag_server, "_open_rag_events", open_events)

    response = rag_client.post(
        "/chat/stream",
        headers=SERVICE_HEADERS,
        json={"username": "viewer-test", "user_question": "How?"},
    )

    assert response.status_code == 200
    assert "event: done" in response.text
    assert observed == [
        (database_engine, (qdrant_client, collection_name))
    ]
    reset = rag_client.app.state.rag_server.runtime.thread_pool.submit(
        lambda: (
            current_repository_engine(),
            current_qdrant_runtime(),
        )
    ).result()
    assert reset == (None, (None, None))


def test_chat_reports_busy_without_opening_the_rag_pipeline(rag_client, monkeypatch):
    class BusySemaphore:
        async def acquire(self):
            raise rag_server.asyncio.TimeoutError

        def release(self):
            raise AssertionError("A permit was not acquired")

    opened = []
    _replace_runtime(rag_client, semaphore=BusySemaphore())
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

    _replace_runtime(rag_client, semaphore=BusySemaphore())

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

    _replace_runtime(rag_client, semaphore=BusySemaphore())

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
    from mech_chatbot.services import chat_service

    observed = []
    monkeypatch.setattr(
        chat_service,
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
    from mech_chatbot.services import chat_service

    observed = {}

    def fake_history(session_id, **scope):
        observed.update({"session_id": session_id, **scope})
        return [{"role": "assistant", "content": "saved answer"}]

    monkeypatch.setattr(chat_service, "get_chat_history", fake_history)

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
    from mech_chatbot.services import chat_service

    observed = []
    monkeypatch.setattr(
        chat_service,
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
    from mech_chatbot.services import audit_service, chat_service

    saved = {"evidence": [], "sources": [], "audits": []}
    monkeypatch.setattr(chat_service, "save_chat_history", lambda **_kwargs: 91)
    monkeypatch.setattr(
        chat_service,
        "save_answer_evidence",
        lambda chat_id, docs: saved["evidence"].append((chat_id, docs)),
    )
    monkeypatch.setattr(
        chat_service,
        "save_answer_sources",
        lambda chat_id, docs: saved["sources"].append((chat_id, docs)),
    )
    monkeypatch.setattr(
        audit_service,
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
    from mech_chatbot.services import chat_service

    saved = []
    monkeypatch.setattr(
        chat_service,
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


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_bom_v2_is_gated_in_production_before_entering_executor(path,rag_client,monkeypatch):
    monkeypatch.setattr(rag_server,'_open_rag_events',lambda *a,**k:pytest.fail('Must not execute production BOM v2'))
    response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_id':7,'user_question':'BOM',
        'bom_rollup':{'root_key':'document:1','product_scope':'product','site':'factory','order_quantity':'3','rounding_scope':'policy'}})
    assert response.status_code==409


@pytest.mark.parametrize('selection',[
    {'order_quantity':'NaN'}, {'order_quantity':3}, {'direction':'where_used'}, {'rounding_scope':' '}, {'approved':True}])
def test_bom_request_rejects_client_approval_noncanonical_quantity_and_reverse_direction(selection,rag_client):
    body={'root_key':'document:1','product_scope':'product','site':'factory','order_quantity':'3','rounding_scope':'policy'}
    response=rag_client.post('/chat',headers=SERVICE_HEADERS,json={'user_question':'BOM','bom_rollup':{**body,**selection}})
    assert response.status_code==422


def test_bom_request_rejects_multiple_templates_before_execution(rag_client):
    selection={'root_key':'document:1','product_scope':'product','site':'factory','order_quantity':'3','rounding_scope':'policy'}
    response=rag_client.post('/chat',headers=SERVICE_HEADERS,json={'user_question':'BOM','bom_rollup':selection,
        'graph_query':{key:selection[key] for key in ('root_key','product_scope','site')}})
    assert response.status_code==422


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_query_plan_is_rejected_in_production_before_executor(path,rag_client,monkeypatch):
    monkeypatch.setattr(rag_server,'_open_rag_events',lambda *a,**k:pytest.fail('Must not enter production Query v2'))
    body={'branches':[{'branch_id':'graph','question':'Contains what?',
        'graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}}]}
    response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_question':'Contains what?','query_plan':body})
    assert response.status_code==409


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_text_query_requires_evaluation_context_and_reviewer_before_executor(path,rag_client,monkeypatch):
    class NoRun:
        def run(self,*a,**k): pytest.fail('must not execute')
    _replace_runtime(rag_client,executor=NoRun())
    body={'user_id':7,'user_question':'Tìm quy trình; tìm chính sách','text_query':True}
    assert rag_client.post(path,headers=SERVICE_HEADERS,json=body).status_code==409
    _replace_runtime(rag_client,runtime_contract=RagRuntimeContract('evaluation',False,120))
    assert rag_client.post(path,headers=SERVICE_HEADERS,json=body).status_code==403
    assert rag_client.post(path,headers=SERVICE_HEADERS,json={**body,'text_query':1}).status_code==422
    assert rag_client.post(path,headers=SERVICE_HEADERS,json={**body,'scope_comparison':{'scope_a':'a','scope_b':'b'}}).status_code==422


@pytest.mark.parametrize('change',['four','duplicate','dependency','two_actions','approval','bounds'])
def test_query_plan_schema_refuses_unsafe_or_oversized_client_plan(rag_client,change):
    branch={'branch_id':'graph','question':'Contains what?','graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}}
    plan={'branches':[branch]}
    if change=='four':plan['branches']=[{**branch,'branch_id':str(i)} for i in range(4)]
    if change=='duplicate':plan['branches']=[branch,branch]
    if change=='dependency':plan['branches']=[{**branch,'depends_on':['missing']}]
    if change=='two_actions':plan['branches']=[{**branch,'scope_comparison':{'scope_a':'a','scope_b':'b'}}]
    if change=='approval':plan['accepted']=True
    if change=='bounds':plan['branches']=[{**branch,'graph_query':{**branch['graph_query'],'max_relations':501}}]
    response=rag_client.post('/chat',headers=SERVICE_HEADERS,json={'user_question':'Contains what?','query_plan':plan})
    assert response.status_code==422
