import json
from types import SimpleNamespace
from mech_chatbot.config.settings import Settings

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event

from mech_chatbot.api import dependencies as api_dependencies
from mech_chatbot.api import file_access
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


def test_browser_comparison_is_gated_before_starting_chat_runner(monkeypatch, client):
    class NoRun:
        def stream(self, *args):
            pytest.fail('V2 must not enter runner outside its approved evaluation scope')
    monkeypatch.setattr(app_server.app.state, 'runtime', SimpleNamespace(chat_turn_runner=NoRun()))
    monkeypatch.setattr(app_server.app.state, 'settings_snapshot',
                        Settings(RAG_EXECUTION_CONTEXT='production'))
    body = {'session_id':'comparison-1', 'question':'Compare',
            'scope_comparison':{'scope_a':'a','scope_b':'b'}}
    assert client.post('/api/chat/message', json=body).status_code == 409
    monkeypatch.setattr(app_server.app.state, 'settings_snapshot',
                        Settings(RAG_EXECUTION_CONTEXT='evaluation'))
    assert client.post('/api/chat/message', json=body).status_code == 403


def test_browser_scope_total_requires_evaluation_and_forwards_one_scope(monkeypatch, client):
    captured = []
    class Runner:
        def stream(self, command, actor):
            captured.append(command)
            yield ChatDone(None, '', (), (), None, 1)
    monkeypatch.setattr(app_server.app.state, 'runtime', SimpleNamespace(chat_turn_runner=Runner()))
    body = {'session_id':'total', 'question':'Tổng phạm vi', 'scope_total':{'scope_id':'approved-a'}}
    monkeypatch.setattr(app_server.app.state, 'settings_snapshot', Settings(RAG_EXECUTION_CONTEXT='production'))
    assert client.post('/api/chat/message', json=body).status_code == 409
    assert not captured
    monkeypatch.setattr(app_server.app.state, 'settings_snapshot', Settings(RAG_EXECUTION_CONTEXT='evaluation'))
    assert client.post('/api/chat/message', json=body).status_code == 403
    app_server.app.dependency_overrides[api_dependencies.csrf_profile] = lambda: {**_profile(), 'roles':['reviewer']}
    assert client.post('/api/chat/message', json=body).status_code == 200
    assert captured[-1].scope_total == 'approved-a'
    assert client.post('/api/chat/message', json={**body,'text_query':True}).status_code == 422
    for selection in ({'scope_id':' '}, {'scope_id':'a','approved':True}):
        assert client.post('/api/chat/message', json={**body,'scope_total':selection}).status_code == 422


def test_browser_total_grant_forwards_one_action_and_blocks_comparison(monkeypatch, client):
    from mech_chatbot.governance.scope_total_activation import ScopeTotalCapability, CONTRACT
    captured = []
    cap = ScopeTotalCapability(CONTRACT, 'sum_reviewed_scope', (('a','snapshot','a'*64),),
        'candidate', 'b'*64, 'c'*64, '2099-01-01T00:00:00Z')
    class Runner:
        def stream(self, command, actor):
            captured.append(command)
            yield ChatDone(None, '', (), (), None, 1)
    monkeypatch.setattr(app_server.app.state, 'runtime',
        SimpleNamespace(chat_turn_runner=Runner(), math_v2_authorizer=lambda: cap))
    monkeypatch.setattr(app_server.app.state, 'settings_snapshot', Settings(RAG_EXECUTION_CONTEXT='production'))
    body = {'session_id':'total', 'question':'Tổng phạm vi', 'scope_total':{'scope_id':'a'}}
    assert client.post('/api/chat/message', json=body).status_code == 200
    assert captured[-1].scope_total == 'a'
    comparison = {'session_id':'total', 'question':'Compare', 'scope_comparison':{'scope_a':'a','scope_b':'b'}}
    assert client.post('/api/chat/message', json=comparison).status_code == 409
    assert len(captured) == 1


def test_browser_text_query_requires_evaluation_and_reviewer_before_runner(monkeypatch,client):
    class NoRun:
        def stream(self,*a):pytest.fail('must not enter runner')
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=NoRun()))
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='production'))
    body={'session_id':'text','question':'Tìm quy trình; tìm chính sách','text_query':True}
    assert client.post('/api/chat/message',json=body).status_code==409
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='evaluation'))
    assert client.post('/api/chat/message',json=body).status_code==403
    assert client.post('/api/chat/message',json={**body,'text_query':'true'}).status_code==422


def test_browser_graph_query_is_gated_before_entering_runner(monkeypatch,client):
    class NoRun:
        def stream(self,*a):pytest.fail('must not enter runner')
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=NoRun()))
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='production'))
    body={'session_id':'graph','question':'Contains what?',
          'graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}}
    assert client.post('/api/chat/message',json=body).status_code==409


def test_browser_graph_grant_keeps_standalone_query_and_actor_for_runner(monkeypatch,client):
    from datetime import datetime,timedelta,timezone
    from mech_chatbot.governance.graph_v2_activation import CONTRACT,GraphV2Capability
    captured=[]
    class Runner:
        def stream(self,command,actor):
            captured.append((command,actor))
            yield ChatDone(None,'',(),(),None,1,advanced_rag={'graph':{'offline_only':False}})
    context={'root_key':'document:1','product_scope':'product','site':'HN','direction':'contains'}
    cap=GraphV2Capability(CONTRACT,((json.dumps(context,sort_keys=True,separators=(',',':')),'a'*64),),
        'b'*40,'c'*64,'d'*64,(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat())
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='production'))
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=Runner(),graph_v2_authorizer=lambda:cap))
    response=client.post('/api/chat/message',json={'session_id':'granted-graph','question':'Cụm này chứa gì?','graph_query':context})
    assert response.status_code==200
    assert captured[0][0].graph_query.root_key=='document:1' and captured[0][1].user_id==7
    assert captured[0][0].query_plan is None and captured[0][0].scope_comparison is None
    assert _events(response.text)[-1][1]['advanced_rag']=={'graph':{'offline_only':False}}


def test_browser_comparison_keeps_selected_scopes_and_versioned_steps_in_done_event(monkeypatch, client):
    captured = []
    metadata = {'schema':'grounded-calculation-v2', 'accepted':False, 'offline_only':True,
                'status':'partial', 'domain_step_count':4}
    class Runner:
        def stream(self, command, actor):
            captured.append((command, actor))
            yield ChatDone(None, '', (), (), None, 10, advanced_rag={'math':metadata})
    app_server.app.dependency_overrides[api_dependencies.csrf_profile] = lambda: {**_profile(), 'roles':['reviewer']}
    monkeypatch.setattr(app_server.app.state, 'runtime', SimpleNamespace(chat_turn_runner=Runner()))
    monkeypatch.setattr(app_server.app.state, 'settings_snapshot', Settings(RAG_EXECUTION_CONTEXT='evaluation'))
    response = client.post('/api/chat/message', json={'session_id':'compare', 'question':'Compare',
                           'scope_comparison':{'scope_a':'a','scope_b':'b'}})
    assert response.status_code == 200
    assert _events(response.text)[-1][1]['advanced_rag'] == {'math':metadata}
    assert captured[0][0].scope_comparison == ('a','b') and captured[0][1].user_id == 7


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
    monkeypatch.setattr(chat_routes, "page_has_preview", lambda _doc_id, _page_no: False)
    citations = chat_routes._citation_list([
        {
            "doc_id": 9,
            "trang": 1,
            "file_goc": "policy.docx",
            "score": 0.88,
            "source_id": "D9P1",
            "version_no": 3,
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
        "original_url": "/api/files/documents/9/original?citation_version=3",
        "version_no": 3,
    }]


@pytest.mark.parametrize('version', [None, '', 0, -1, 'bad', 1.5, True])
def test_ordinary_citation_without_a_valid_version_never_links_to_latest(monkeypatch, version):
    monkeypatch.setattr(chat_routes, 'page_has_vision', lambda *_: True)
    assert chat_routes._citation_list([
        {'doc_id': 9, 'trang': 1, 'file_goc': 'manual.pdf', 'version_no': version}
    ]) == []


def test_ordinary_pdf_citation_keeps_revision_on_both_urls(monkeypatch):
    monkeypatch.setattr(chat_routes, 'page_has_vision', lambda *_: True)
    monkeypatch.setattr(chat_routes, 'page_has_preview', lambda *_: True)
    card = chat_routes._citation_list([
        {'doc_id': 9, 'trang': 1, 'file_goc': 'manual.pdf', 'version_no': 3}
    ])[0]
    assert card['page_url'] == '/api/files/documents/9/pages/1?citation_version=3'
    assert card['original_url'] == '/api/files/documents/9/original?citation_version=3'


@pytest.mark.parametrize('version', [3, None])
def test_streamed_citation_fallback_rebuilds_revision_links(monkeypatch, version):
    from mech_chatbot.application.chat_citations import resolve_chat_citations
    candidates = [{'doc_id': 9, 'page_no': 1, 'file_name': 'manual.pdf', 'version_no': version,
                   'source_id': 'D9P1', 'original_url': '/api/files/documents/9/original'}]
    cards, _ = resolve_chat_citations([], candidates, 'Answer [SRC:D9P1]', page_has_vision=lambda *_: True)
    if version is None:
        assert cards == ()
    else:
        assert cards[0]['original_url'] == '/api/files/documents/9/original?citation_version=3'
        assert cards[0]['page_url'] == '/api/files/documents/9/pages/1?citation_version=3'


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


def test_live_ocr_citation_keeps_vision_false_and_exposes_available_preview(monkeypatch):
    monkeypatch.setattr(chat_routes, "page_has_vision", lambda *_args: False)
    monkeypatch.setattr(chat_routes, "page_has_preview", lambda *_args: True, raising=False)
    docs = [{"doc_id": 9, "trang": 1, "file_goc": "scan.pdf", "version_no": 1}]

    citations, _reference = chat_routes._resolve_chat_citations(docs, [], "[SRC:D9P1]")

    assert citations[0]["has_vision"] is False
    assert citations[0]["page_url"] == "/api/files/documents/9/pages/1?citation_version=1"
    assert chat_routes._citation_list(docs) == list(citations)


@pytest.mark.parametrize("summary", ["", "vision dimensions"])
@pytest.mark.parametrize("image_state", ["approved", "missing", "outside"])
@pytest.mark.parametrize("surface", ["live", "history"])
def test_composed_runtime_citations_use_its_owned_engine(
    monkeypatch, tmp_path, summary, image_state, surface
):
    from mech_chatbot.config.settings import AppProcessSettings, Settings

    image = tmp_path / ("src/data/processed/page.png" if image_state != "outside" else "outside.png")
    image.parent.mkdir(parents=True, exist_ok=True)
    if image_state != "missing":
        Image.new("RGB", (2, 2)).save(image)
    calls = []
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def sqlite_syntax(_conn, _cursor, sql, params, _context, _many):
        calls.append((sql, params))
        converted = sql.replace("dbo.", "").replace("SELECT TOP 1", "SELECT")
        converted = converted.replace("TRY_CONVERT(INT, a.ChunkRef)", "CAST(a.ChunkRef AS INTEGER)")
        return (converted + " LIMIT 1" if "SELECT TOP 1" in sql else converted), params

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE DocumentPages (DocID INT, PageNo INT, VisionSummary TEXT, ImagePath TEXT)")
        connection.exec_driver_sql("CREATE TABLE AnswerSource (ChatID INT, DocID INT, FileName TEXT, VersionNo INT, ChunkRef TEXT, Score FLOAT, RankNo INT)")
        connection.exec_driver_sql("INSERT INTO DocumentPages VALUES (9,1,?,?)", (summary, str(image)))
        connection.exec_driver_sql("INSERT INTO AnswerSource VALUES (11,9,'scan.pdf',1,'1',0.91,1)")

    monkeypatch.setattr(file_access, "engine", file_access._ENGINE_UNSET)
    monkeypatch.setattr(file_access, "project_root", lambda: tmp_path)
    monkeypatch.setattr(app_server, "data_raw_root", lambda: tmp_path / "data/raw")
    runtime = app_server._build_default_app_runtime(
        AppProcessSettings.from_settings(Settings.from_env({})),
        SimpleNamespace(schedule=lambda *_args: False),
        post=lambda *_args, **_kwargs: None,
        engine=engine,
    )
    docs = [{"doc_id": 9, "trang": 1, "file_goc": "scan.pdf", "version_no": 1}]

    live, _ = runtime.citation_resolver(docs, [], "[SRC:D9P1]")
    history = chat_routes._sources_for_chat_ids([11], runtime)[11]

    expected_url = "/api/files/documents/9/pages/1?citation_version=1" if image_state == "approved" else None
    citation = live[0] if surface == "live" else history[0]
    assert citation["page_url"] == expected_url
    assert citation["has_vision"] is bool(summary and image_state == "approved")
    assert any("SELECT TOP 1 ImagePath" in sql for sql, _ in calls)
    assert runtime.db_engine is engine
    other_engine = create_engine("sqlite://")
    event.listen(other_engine, "before_cursor_execute", sqlite_syntax, retval=True)
    with other_engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE DocumentPages (DocID INT, PageNo INT, VisionSummary TEXT, ImagePath TEXT)")
        connection.exec_driver_sql("CREATE TABLE AnswerSource (ChatID INT, DocID INT, FileName TEXT, VersionNo INT, ChunkRef TEXT, Score FLOAT, RankNo INT)")
        connection.exec_driver_sql("INSERT INTO DocumentPages VALUES (9,1,'',?)", (str(tmp_path / "src/data/processed/other-missing.png"),))
        connection.exec_driver_sql("INSERT INTO AnswerSource VALUES (11,9,'scan.pdf',1,'1',0.91,1)")
    other = app_server._build_default_app_runtime(
        runtime.settings,
        SimpleNamespace(schedule=lambda *_args: False),
        post=lambda *_args, **_kwargs: None,
        engine=other_engine,
    )
    assert other.db_engine is other_engine
    # Missing rows in the second app cannot reuse the first app's approved image.
    assert other.citation_resolver(docs, [], "[SRC:D9P1]")[0][0]["page_url"] is None
    assert chat_routes._sources_for_chat_ids([11], other)[11][0]["page_url"] is None
    assert runtime.citation_resolver(docs, [], "[SRC:D9P1]")[0][0]["page_url"] == expected_url
    assert chat_routes._sources_for_chat_ids([11], runtime)[11][0]["page_url"] == expected_url
    assert file_access.engine is file_access._ENGINE_UNSET
    other_engine.dispose()
    engine.dispose()


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


def test_structured_citations_keep_sheet_cell_row_and_never_invent_a_pdf_page():
    from mech_chatbot.application.chat_citations import build_citation_list, filter_citations_by_answer, citation_ref_text
    from mech_chatbot.rag.execution import attributed_citations
    records = [
        {'doc_id':17,'file_goc':'ledger.xlsx','version_no':3,'snapshot_id':'s1','source_hash':'a'*64,
         'locator':{'doc_id':'17','version':'3','sheet':'Ledger','cell':'B2','row_key':'Ledger:row-2'}},
        {'doc_id':17,'file_goc':'ledger.xlsx','version_no':3,'snapshot_id':'s1','source_hash':'a'*64,
         'locator':{'doc_id':'17','version':'3','sheet':'Ledger','cell':'B3','row_key':'Ledger:row-3'}},
    ]
    cards = build_citation_list(records,page_has_vision=lambda *a: pytest.fail('not a PDF page'))
    assert len(cards) == 2 and len({c['source_id'] for c in cards}) == 2
    assert all(c['page_no'] is None and c['page_url'] is None and not c['has_vision'] for c in cards)
    assert cards[0]['locator']['cell'] == 'B2' and cards[1]['locator']['row_key'] == 'Ledger:row-3'
    assert cards[0]['original_url'] == '/api/files/documents/17/original?citation_version=3'
    answer = f"Tổng A = 25 [SRC:{cards[0]['source_id']}]"
    assert filter_citations_by_answer(cards, answer) == [cards[0]]
    assert attributed_citations({'citation_docs':records}, answer) == (cards[0],)
    assert 'Sheet Ledger' in citation_ref_text([cards[0]]) and 'B2' in citation_ref_text([cards[0]])
    assert 'Trang ?' not in citation_ref_text([cards[0]])

def test_browser_graph_context_and_completeness_survive_done_event(monkeypatch,client):
    from mech_chatbot.domain.graph_query import GraphQuery
    captured=[]
    metadata={'contract_version':'graph-v2','status':'partial','accepted':False,'offline_only':True}
    class Runner:
        def stream(self,command,actor):
            captured.append(command)
            yield ChatDone(None,'',(),(),None,10,advanced_rag={'graph':metadata})
    app_server.app.dependency_overrides[api_dependencies.csrf_profile]=lambda:{**_profile(),'roles':['reviewer']}
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=Runner()))
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='evaluation'))
    response=client.post('/api/chat/message',json={'session_id':'graph','question':'Contains what?',
        'graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}})
    assert response.status_code==200 and _events(response.text)[-1][1]['advanced_rag']=={'graph':metadata}
    assert captured[0].graph_query==GraphQuery('document:1','product','factory')


def test_browser_bom_selection_is_gated_and_preserves_math_metadata(monkeypatch,client):
    class NoRun:
        def stream(self,*args):pytest.fail('Must not execute production or unprivileged BOM')
    body={'session_id':'bom','question':'BOM','bom_rollup':{'root_key':'document:1','product_scope':'product',
        'site':'factory','order_quantity':'3','rounding_scope':'policy'}}
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=NoRun()))
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='production'))
    assert client.post('/api/chat/message',json=body).status_code==409
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='evaluation'))
    assert client.post('/api/chat/message',json=body).status_code==403
    captured=[]
    math={'schema':'grounded-calculation-v2','template':'bom_rollup','status':'complete','steps':[],'accepted':False}
    class Runner:
        def stream(self,command,actor):
            captured.append(command)
            yield ChatDone(None,'',(),(),None,10,advanced_rag={'math':math})
    app_server.app.dependency_overrides[api_dependencies.csrf_profile]=lambda:{**_profile(),'roles':['reviewer']}
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=Runner()))
    response=client.post('/api/chat/message',json=body)
    assert response.status_code==200 and _events(response.text)[-1][1]['advanced_rag']=={'math':math}
    assert captured[0].bom_rollup.order_quantity=='3' and captured[0].bom_rollup.rounding_scope=='policy'


def test_browser_query_plan_preserves_branch_metadata_and_context_guard(monkeypatch,client):
    branch={'branch_id':'graph','question':'Cụm chứa gì','graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}}
    body={'session_id':'plan','question':'Cụm chứa gì','query_plan':{'branches':[branch]}}
    class NoRun:
        def stream(self,*args):pytest.fail('Must not enter unapproved Query v2')
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=NoRun()))
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='production'))
    assert client.post('/api/chat/message',json=body).status_code==409
    monkeypatch.setattr(app_server.app.state,'settings_snapshot',Settings(RAG_EXECUTION_CONTEXT='evaluation'))
    assert client.post('/api/chat/message',json=body).status_code==403
    captured=[]
    metadata={'schema':'query-plan-v2','status':'partial','branches':[],'accepted':False}
    class Runner:
        def stream(self,command,actor):
            captured.append(command)
            yield ChatDone(None,'',(),(),None,10,advanced_rag={'query':metadata})
    app_server.app.dependency_overrides[api_dependencies.csrf_profile]=lambda:{**_profile(),'roles':['reviewer']}
    monkeypatch.setattr(app_server.app.state,'runtime',SimpleNamespace(chat_turn_runner=Runner()))
    response=client.post('/api/chat/message',json=body)
    assert response.status_code==200 and _events(response.text)[-1][1]['advanced_rag']=={'query':metadata}
    assert captured[0].query_plan.branches[0].branch_id=='graph'
