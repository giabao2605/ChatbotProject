"""Contract tests for Phase 1 browser-chat system adapters."""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mech_chatbot.adapters.chat_runtime import (
    HttpRagStreamAdapter,
    PilotExperimentAdapter,
    RepositoryAuditSink,
    RepositoryChatStore,
)
from mech_chatbot.application.chat_turn import (
    ChatActor,
    ChatAuditRecord,
    ChatPersistence,
    ChatTurnCommand,
    PilotReplayInput,
    RagCitationCandidate,
    RagDelta,
    RagMetadata,
    RagStreamDone,
    RagStreamError,
    RagStreamRequest,
)


pytestmark = pytest.mark.unit


class Response:
    def __init__(self, *, status=200, events=(), text=""):
        self.status_code = status
        self.ok = 200 <= status < 400
        self.events = tuple(events)
        self.text = text
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True
        return False

    def iter_lines(self, decode_unicode=True):
        for event, payload in self.events:
            yield f"event: {event}"
            yield "data: " + json.dumps(payload)
            yield ""


def actor() -> ChatActor:
    return ChatActor(
        user_id=7,
        username="alice",
        roles=frozenset({"viewer"}),
        department="Technical",
        allowed_departments=frozenset({"Technical"}),
        allowed_sites=frozenset({"HQ"}),
        max_security_level="internal",
        response_language="vi",
    )


def command() -> ChatTurnCommand:
    return ChatTurnCommand(
        request_id="session-1|request-1",
        session_id="session-1",
        question="Approved procedure?",
        image_path=Path("image.png"),
        history=({"role": "user", "content": "previous"},),
        current_part_ids=("P1",),
        conversation_context={"topic": "procedure"},
    )


def test_http_adapter_maps_payload_and_sse_to_typed_events():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response(
            events=(
                ("accepted", {"ok": True}),
                (
                    "metadata",
                    {
                        "ref_text": "upstream",
                        "ref_images": ["page.png"],
                        "new_part_ids": ["P2"],
                        "debug_info": {"route": "technical"},
                    },
                ),
                ("token", {"text": "answer"}),
                ("citation", {"source_id": "D1P1"}),
                ("done", {"trace_id": "trace-1", "elapsed_ms": 12}),
            )
        )

    adapter = HttpRagStreamAdapter(
        post=post,
        base_url="http://rag",
        headers={"X-RAG-Service-Token": "service-token"},
        timeout=(10, 300),
    )
    events = list(adapter.stream(RagStreamRequest(command(), actor()), None))

    assert calls[0][0] == "http://rag/chat/stream"
    assert calls[0][1]["json"]["user_id"] == 7
    assert calls[0][1]["json"]["username"] == "alice"
    assert calls[0][1]["json"]["image_path"] == "image.png"
    assert [type(event) for event in events] == [
        RagMetadata,
        RagDelta,
        RagCitationCandidate,
        RagStreamDone,
    ]
    assert events[-1].trace_id == "trace-1"


def test_comparison_selection_crosses_http_adapter_without_client_approval_or_roles():
    bodies = []
    adapter = HttpRagStreamAdapter(post=lambda url, **kw: bodies.append(kw['json']) or Response(
        events=(('done', {'trace_id':'comparison'}),)), base_url='http://rag')
    selected = replace(command(), scope_comparison=('a','b'))
    list(adapter.stream(RagStreamRequest(selected, actor()), None))
    assert bodies[0]['scope_comparison'] == {'scope_a':'a','scope_b':'b'}
    assert 'user_roles' not in bodies[0] and 'approved' not in bodies[0]['scope_comparison']


def test_scope_total_selection_crosses_http_adapter_without_approval():
    bodies = []
    adapter = HttpRagStreamAdapter(post=lambda url, **kw: bodies.append(kw['json']) or Response(
        events=(('done', {'trace_id':'total'}),)), base_url='http://rag')
    list(adapter.stream(RagStreamRequest(replace(command(), scope_total='a'), actor()), None))
    assert bodies[0]['scope_total'] == {'scope_id':'a'}
    assert 'scope_comparison' not in bodies[0] and 'query_plan' not in bodies[0]


def test_query_scope_total_branch_crosses_adapter_with_dependency():
    from mech_chatbot.api.evidence_contracts import QueryPlanRequest
    plan=QueryPlanRequest.model_validate({'branches':[
        {'branch_id':'a','question':'Tổng A','scope_total':{'scope_id':'scope-a'}},
        {'branch_id':'b','question':'Tổng B','scope_total':{'scope_id':'scope-b'},'depends_on':['a']}]}).to_plan()
    bodies=[]
    adapter=HttpRagStreamAdapter(post=lambda url,**kw:bodies.append(kw['json']) or Response(
        events=(('done',{'trace_id':'query-total'}),)),base_url='http://rag')
    list(adapter.stream(RagStreamRequest(replace(command(),query_plan=plan),actor()),None))
    assert bodies[0]['query_plan']['branches'][1]['scope_total']=={'scope_id':'scope-b'}
    assert bodies[0]['query_plan']['branches'][1]['depends_on']==('a',)


def test_http_adapter_maps_busy_without_starting_persistence_contract():
    response = Response(status=503, text="busy")
    adapter = HttpRagStreamAdapter(
        post=lambda *_args, **_kwargs: response,
        base_url="http://rag",
    )

    events = list(adapter.stream(RagStreamRequest(command(), actor()), None))

    assert events == [
        RagStreamError(
            message="RAG server busy",
            detail="busy",
            http_status=503,
        )
    ]
    assert response.closed is True


def test_http_adapter_reports_unresponsive_rag_service_without_transport_details():
    adapter = HttpRagStreamAdapter(
        post=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("internal proxy host")
        ),
        base_url="http://rag",
    )

    events = list(adapter.stream(RagStreamRequest(command(), actor()), None))

    assert events == [
        RagStreamError(
            message="Dịch vụ RAG/AI không phản hồi kịp. Vui lòng thử lại."
        )
    ]


def test_repository_store_persists_turn_evidence_and_final_sources():
    calls = []
    store = RepositoryChatStore(
        save_chat_history=lambda **kwargs: calls.append(("chat", kwargs)) or 91,
        save_answer_evidence=lambda *args: calls.append(("evidence", args)),
        save_answer_sources=lambda *args: calls.append(("sources", args)),
    )
    citation = {
        "doc_id": 1,
        "file_name": "manual.pdf",
        "version_no": 2,
        "page_no": 3,
        "score": 0.9,
        "source_id": "D1P3",
    }
    record = ChatPersistence(
        command=command(),
        actor=actor(),
        answer="answer",
        ref_text=" refs",
        ref_images=("page.png",),
        evidence=({"doc_id": 1},),
        citations=(citation,),
    )

    assert store.persist(record) == 91
    assert calls[0][1]["bot_msg"] == "answer refs"
    assert calls[1] == ("evidence", (91, [{"doc_id": 1}]))
    assert calls[2][1][1][0]["source_id"] == "D1P3"


def test_repository_store_keeps_structured_pins_and_reports_failed_evidence_write():
    captured = []
    citation = {'doc_id':17,'version_no':3,'page_no':None,'file_name':'book.xlsx',
                'locator':{'doc_id':'17','version':'3','sheet':'Book','cell':'B2','row_key':'r2'},
                'scope_id':'a','scope_hash':'b'*64,'snapshot_id':'s1','source_hash':'a'*64,
                'citation_schema':'source-locator-v2','source_id':'D17L1234567890ABCDEF'}
    record = ChatPersistence(command=replace(command(),scope_comparison=('a','b')),actor=actor(),answer='4',
                             ref_text='',ref_images=(),evidence=(citation,),citations=(citation,))
    store = RepositoryChatStore(save_chat_history=lambda **kw:11,save_answer_evidence=lambda *a:True,
                                save_answer_sources=lambda cid,docs:captured.extend(docs) or True)
    assert store.persist(record)==11
    assert captured[0]['scope_hash']=='b'*64 and captured[0]['locator']['cell']=='B2'
    failed = RepositoryChatStore(save_chat_history=lambda **kw:11,save_answer_evidence=lambda *a:False,
                                 save_answer_sources=lambda *a:pytest.fail('must not save unverifiable sources'))
    with pytest.raises(RuntimeError):
        failed.persist(record)


def test_repository_store_passes_branch_result_with_final_citations_and_reports_missing_migration():
    captured = []
    metadata = {'text_query':{'schema':'query-text-v2','accepted':False,'offline_only':True,'branches':[]}}
    card = {'doc_id':1,'version_no':2,'source_id':'D1P1'}
    record = ChatPersistence(command=replace(command(),text_query=True),actor=actor(),answer='Answer',
        ref_text='',ref_images=(),evidence=(card,),citations=(card,),advanced_rag=metadata)
    store = RepositoryChatStore(save_chat_history=lambda **kw:11,
        save_answer_evidence=lambda *a,**kw:captured.append(kw) or True,save_answer_sources=lambda *a:True)
    assert store.persist(record)==11
    assert captured == [{'advanced_rag':metadata,'citation_cards':[card]}]
    failed = RepositoryChatStore(save_chat_history=lambda **kw:11,save_answer_evidence=lambda *a,**kw:False,
        save_answer_sources=lambda *a:pytest.fail('must not pretend the advanced history was saved'))
    with pytest.raises(RuntimeError):failed.persist(record)


def test_audit_adapter_preserves_typed_record_fields():
    calls = []
    sink = RepositoryAuditSink(
        write_audit_log=lambda **kwargs: calls.append(kwargs)
    )

    sink.record(
        ChatAuditRecord(
            action="chat_query",
            username="alice",
            entity_type="LichSuChat",
            entity_id=91,
            details={"session_id": "session-1"},
        )
    )

    assert calls == [
        {
            "username": "alice",
            "action": "chat_query",
            "entity_type": "LichSuChat",
            "entity_id": 91,
            "details": {"session_id": "session-1"},
        }
    ]


def test_pilot_adapter_assigns_and_schedules_from_server_actor():
    route = SimpleNamespace(arm="candidate")
    calls = []
    adapter = PilotExperimentAdapter(
        load_config=lambda: "config",
        assign_route=lambda config, **kwargs: calls.append(
            ("assign", config, kwargs)
        )
        or route,
        outcome=lambda answer, debug, **kwargs: {
            "answer": answer,
            "provider_error": kwargs["provider_error"],
        },
        schedule_replay=lambda *args: calls.append(("replay", args)) or True,
    )

    assigned = adapter.assign(actor(), command())
    scheduled = adapter.schedule_replay(
        route,
        PilotReplayInput(
            command=command(),
            actor=actor(),
            answer="answer",
            debug_info={"route": "technical"},
            trace_id="trace-1",
        ),
    )

    assert assigned is route
    assert calls[0][2]["user_id"] == "7"
    assert calls[0][2]["sites"] == ("HQ",)
    assert scheduled is True
    replay_payload = calls[1][1][1]
    assert replay_payload["username"] == "alice"
    assert replay_payload["response_language"] == "vi"

def test_graph_selection_crosses_http_adapter_with_only_explicit_context():
    from mech_chatbot.domain.graph_query import GraphQuery
    bodies=[]
    adapter=HttpRagStreamAdapter(post=lambda url,**kw:bodies.append(kw['json']) or Response(
        events=(('done',{'trace_id':'graph'}),)),base_url='http://rag')
    selected=replace(command(),graph_query=GraphQuery('document:1','product','factory'))
    list(adapter.stream(RagStreamRequest(selected,actor()),None))
    assert bodies[0]['graph_query']=={'root_key':'document:1','product_scope':'product','site':'factory','direction':'contains'}
    assert 'user_roles' not in bodies[0] and 'approved' not in bodies[0]['graph_query']


def test_bom_selection_crosses_http_adapter_without_float_or_approval():
    from mech_chatbot.domain.graph_query import GraphQuery,BomRollupQuery
    bodies=[]
    adapter=HttpRagStreamAdapter(post=lambda url,**kw:bodies.append(kw['json']) or Response(
        events=(('done',{'trace_id':'bom'}),)),base_url='http://rag')
    selected=replace(command(),bom_rollup=BomRollupQuery(GraphQuery('document:1','product','factory'),'3.00','policy'))
    list(adapter.stream(RagStreamRequest(selected,actor()),None))
    assert bodies[0]['bom_rollup']=={'root_key':'document:1','product_scope':'product','site':'factory','direction':'contains',
                                   'order_quantity':'3.00','rounding_scope':'policy'}
    assert 'approved' not in bodies[0]['bom_rollup'] and 'user_roles' not in bodies[0]


def test_query_plan_http_adapter_preserves_order_dependencies_and_decimal_inputs():
    from mech_chatbot.domain.graph_query import GraphQuery,BomRollupQuery
    from mech_chatbot.domain.query_plan import QueryBranch,QueryPlan
    bodies=[]
    graph=GraphQuery('document:1','product','factory')
    plan=QueryPlan((QueryBranch('graph','Contains what?',graph_query=graph),
                    QueryBranch('bom','Calculate BOM',bom_rollup=BomRollupQuery(graph,'3.00','policy'),depends_on=('graph',))))
    adapter=HttpRagStreamAdapter(post=lambda url,**kw:bodies.append(kw['json']) or Response(events=(('done',{}),)),base_url='http://rag')
    list(adapter.stream(RagStreamRequest(replace(command(),query_plan=plan),actor()),None))
    branches=bodies[0]['query_plan']['branches']
    assert [b['branch_id'] for b in branches]==['graph','bom']
    assert branches[1]['depends_on']==('graph',) and branches[1]['bom_rollup']['order_quantity']=='3.00'
    assert 'max_relations' not in branches[0]['graph_query'] and 'approved' not in bodies[0]['query_plan']


def test_http_adapter_forwards_only_the_selected_text_query_flag():
    bodies=[]
    adapter=HttpRagStreamAdapter(post=lambda url,**kw:bodies.append(kw['json']) or Response(events=(('done',{}),)),base_url='http://rag')
    list(adapter.stream(RagStreamRequest(replace(command(),text_query=True),actor()),None))
    assert bodies[0]['text_query'] is True
    assert 'approved' not in bodies[0] and 'query_plan' not in bodies[0]
