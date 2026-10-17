"""Explicit mixed draft reuses one text batch and the original request ledger."""
from dataclasses import replace
import pytest
from .test_rag_bom_chat import bom_store, request, store, connection, rag_client
from .test_rag_pipeline_public_characterization import offline_pipeline


def test_mixed_claim_repair_keeps_nonzero_shared_accounting(bom_store, offline_pipeline, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    from mech_chatbot.rag import pipeline_steps
    from .test_rag_pipeline_public_characterization import _document
    _, engine = bom_store
    _, _, _, runtime = offline_pipeline
    runtime.claim_repair_enabled = True
    document = _document('Chi phí là 10 USD. Chi phí đã được ghi nhận trong chính sách nội bộ.')
    class Store:
        def __init__(self, **kwargs):
            pass
        def similarity_search(self, query, **kwargs):
            return [document]
    monkeypatch.setattr('langchain_qdrant.QdrantVectorStore', Store)
    monkeypatch.setattr(pipeline_steps, 'audited_external_call', lambda **kwargs: nullcontext())
    repairs = []
    def repair(*args, **kwargs):
        repairs.append(True)
        return SimpleNamespace(content='Chi phí là 10 USD. [SRC:D17P2]')
    provider = SimpleNamespace(client=RunnableLambda(lambda _: AIMessage(content='Chi phí là 999 USD. [SRC:D17P2]')),
        invoke=repair, settings=SimpleNamespace(model_name='fixture', base_url='http://127.0.0.1/fixture'))
    original = request()
    selected = replace(original, bom_rollup=None, question='Tính BOM; chi phí là bao nhiêu?',
        access=replace(original.access, allowed_departments=frozenset({'HR','ENG','tech'}),
                       allowed_sites=frozenset({'factory','HCM'})),
        query_plan=QueryPlan((QueryBranch('bom','Tính BOM',bom_rollup=original.bom_rollup),
                             QueryBranch('cost','Chi phí là bao nhiêu?',text_query=True))))
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor(retrieval_adapter=runtime,provider_adapter=provider).run(
            selected, RagInvocation('mixed-repair',mode='test')))
    assert '999' not in result.answer and '6.00' in result.answer
    assert result.diagnostics['generation_metrics']['repair_count'] == 1
    assert result.diagnostics.budget.repairs == 1
    assert result.diagnostics.budget.calculations == 1
    assert result.diagnostics.budget.final_generations == 1


def test_mixed_text_reserves_remaining_budget_before_retrieval(bom_store, offline_pipeline):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, RequestBudgetLimits, RagFailed
    _, engine = bom_store
    _, calls, _, runtime = offline_pipeline
    original = request()
    selected = replace(original, bom_rollup=None, question='BOM; điều kiện; hướng dẫn',
        query_plan=QueryPlan((QueryBranch('bom', 'Tính BOM', bom_rollup=original.bom_rollup),
            QueryBranch('terms', 'Điều kiện thanh toán', text_query=True),
            QueryBranch('guide', 'Hướng dẫn đối soát', text_query=True))))
    executor = DefaultRagExecutor(retrieval_adapter=runtime, budget_limits=RequestBudgetLimits(subqueries=2))
    with bind_repository_runtime(db_engine=engine):
        events = list(executor.run(selected, RagInvocation('mixed-budget', mode='test')))
    assert isinstance(events[-1], RagFailed)
    assert calls == []


@pytest.mark.parametrize('contract', [
    'semantic-fixed-task-routing-v2-draft',
    'semantic-graph-fixed-task-routing-v3-draft',
    'semantic-candidate-task-routing-v4-draft'])
def test_mixed_plan_cannot_enter_existing_planner_contracts(offline_pipeline, contract):
    from types import SimpleNamespace
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    from mech_chatbot.rag.execution import (
        DefaultRagExecutor, RagRequest, AccessScope, RagInvocation, RagCompleted, RagFailed, RagCitation)
    _, retrievals, _, runtime = offline_pipeline
    provider_calls = []
    def forbidden(*args, **kwargs):
        provider_calls.append(True)
        pytest.fail('An unsupported mixed contract must not call a provider')
    executor = DefaultRagExecutor(retrieval_adapter=runtime,
        provider_adapter=SimpleNamespace(invoke=forbidden, invoke_once=forbidden))
    request = RagRequest('Thực hiện các việc đã chọn', AccessScope(roles=frozenset({'reviewer'})),
        query_plan=QueryPlan((QueryBranch('cost', 'So sánh hai kỳ', scope_comparison=('a', 'b')),
                             QueryBranch('guide', 'Tra hướng dẫn', text_query=True))))
    events = list(executor.run(request, RagInvocation('mixed-contract-guard', mode='test',
        task_routing_contract=contract)))
    terminal = events[-1]
    assert isinstance(terminal, (RagCompleted, RagFailed))
    if isinstance(terminal, RagCompleted):
        assert terminal.outcome == 'refused'
        assert terminal.diagnostics.budget.planners == 0
        assert terminal.diagnostics.budget.subqueries == 0
        assert terminal.diagnostics.budget.calculations == 0
    assert provider_calls == [] and retrievals == []
    assert not any(isinstance(event, RagCitation) for event in events)


def test_missing_math_blocks_only_its_dependent_text(bom_store, offline_pipeline):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    from mech_chatbot.rag.execution import RagInvocation, collect_rag_events
    _, engine = bom_store
    _, _, executor, _ = offline_pipeline
    original = request()
    plan = QueryPlan((
        QueryBranch('cost', 'So sánh chi phí', scope_comparison=('missing-a', 'missing-b')),
        QueryBranch('dependent', 'Giải thích kết quả', text_query=True, depends_on=('cost',)),
        QueryBranch('independent', 'Hướng dẫn đối soát', text_query=True)))
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(executor.run(replace(original, bom_rollup=None,
            query_plan=plan, question='So sánh chi phí; giải thích; hướng dẫn'),
            RagInvocation('mixed-dependency', mode='test')))
    branches = result.diagnostics['query_v2']['branches']
    assert [branch['status'] for branch in branches] == ['unknown', 'blocked', 'unknown']
    assert [branch['executed'] for branch in branches] == [True, False, True]
    assert branches[1]['reason'] == 'dependency_unavailable'
    assert all(branch['sources'] == [] for branch in branches)
    assert result.diagnostics.budget.subqueries == 2
    assert result.diagnostics.budget.planners == 0


@pytest.mark.parametrize('stop', ['cancel', 'deadline'])
def test_mixed_request_stops_before_any_source_or_answer(offline_pipeline, stop):
    import threading
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    from mech_chatbot.rag.execution import (
        DefaultRagExecutor, RagInvocation, RagRequest, AccessScope,
        RagToken, RagCitation, RagCancelled, RagFailed, RequestBudgetLimits)
    _, calls, _, runtime = offline_pipeline
    cancel = threading.Event()
    if stop == 'cancel':
        cancel.set()
    executor = DefaultRagExecutor(retrieval_adapter=runtime,
        budget_limits=RequestBudgetLimits(deadline_seconds=0 if stop == 'deadline' else 120))
    request = RagRequest('So sánh; hướng dẫn', AccessScope(roles=frozenset({'reviewer'})),
        query_plan=QueryPlan((QueryBranch('cost', 'So sánh', scope_comparison=('a', 'b')),
                             QueryBranch('guide', 'Hướng dẫn', text_query=True))))
    events = list(executor.run(request, RagInvocation('mixed-stop', mode='test'), cancellation=cancel))
    assert isinstance(events[-1], (RagCancelled, RagFailed))
    assert not any(isinstance(event, (RagToken, RagCitation)) for event in events)
    assert calls == []


def test_mixed_bom_and_missing_text_preserve_independent_math(bom_store, offline_pipeline):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    from mech_chatbot.rag.execution import RagInvocation, collect_rag_events
    _, engine = bom_store
    _, _, executor, _ = offline_pipeline
    original = request()
    plan = QueryPlan((QueryBranch('total', 'Tính BOM cho 3 cụm', bom_rollup=original.bom_rollup),
                      QueryBranch('payment', 'Điều kiện thanh toán', text_query=True),
                      QueryBranch('guide', 'Hướng dẫn đối chiếu', text_query=True)))
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(executor.run(replace(original, bom_rollup=None, query_plan=plan,
            question='Tính BOM; điều kiện thanh toán; hướng dẫn đối chiếu'), RagInvocation('mixed', mode='test')))
    assert result.outcome == 'answered'
    query = result.diagnostics['query_v2']
    assert query['schema'] == 'query-plan-mixed-v1-draft'
    assert [b['branch_id'] for b in query['branches']] == ['total', 'payment', 'guide']
    assert [b['status'] for b in query['branches']] == ['complete', 'unknown', 'unknown']
    assert result.diagnostics.budget.subqueries == 3
    assert result.diagnostics.budget.planners == 0
    assert result.diagnostics.budget.final_generations == 0
    assert '6.00' in result.answer

@pytest.mark.parametrize('missing',[False,True])
@pytest.mark.parametrize('uncited',[False,True])
@pytest.mark.parametrize('negative_note',[False,True])
def test_mixed_text_batch_generates_once_and_preserves_branch_order(bom_store,offline_pipeline,monkeypatch,missing,uncited,negative_note):
    from sqlalchemy import text
    from contextlib import nullcontext
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    from mech_chatbot.rag.execution import DefaultRagExecutor,RagInvocation,collect_rag_events
    from mech_chatbot.rag import pipeline_steps
    from .test_rag_pipeline_public_characterization import _document
    source_store,engine=bom_store
    with source_store.begin() as conn:
        conn.execute(text('ALTER TABLE TaiLieu ADD COLUMN ExternalProcessingPolicy TEXT'))
        conn.execute(text("UPDATE TaiLieu SET ExternalProcessingPolicy='all_external'"))
    _,_,_,runtime=offline_pipeline
    documents=[_document('Điều kiện thanh toán: thanh toán trong vòng ba mươi ngày.'),
               _document('Hướng dẫn đối chiếu: nhân viên kiểm tra hóa đơn theo quy trình nội bộ.' +
                   (' Khi còn thiếu dòng hoặc chưa xác nhận đủ danh sách, chưa công bố tổng khoản cần kiểm tra.'
                    if negative_note else ''))]
    documents=[type(doc)(page_content=doc.page_content,metadata={**doc.metadata,'doc_id':17+i})
               for i,doc in enumerate(documents)]
    class Store:
        def __init__(self,**kwargs):pass
        def similarity_search(self,query,**kwargs):
            return [documents[0]] if 'thanh' in query else [] if missing else [documents[1]]
    monkeypatch.setattr('langchain_qdrant.QdrantVectorStore',Store)
    monkeypatch.setattr(pipeline_steps,'audited_external_call',lambda **kwargs:nullcontext())
    generations=[]
    def generate(_prompt):
        import re
        generations.append(True)
        if uncited:return AIMessage(content='Chưa đủ dữ liệu để trả lời.')
        basis = ' '.join('[SRC:'+source+']' for source in sorted(set(
            re.findall(r'D\d+L[a-fA-F0-9]{16}', str(_prompt)))))
        return AIMessage(content='Thanh toán trong vòng ba mươi ngày [SRC:D17P2]. '+
            ('' if missing else 'Nhân viên kiểm tra hóa đơn theo quy trình nội bộ [SRC:D18P2]. '+basis))
    provider=SimpleNamespace(client=RunnableLambda(generate),settings=SimpleNamespace(
        model_name='fixture-model',base_url='http://127.0.0.1/fixture'),
        invoke=lambda *a,**k:pytest.fail('Explicit branches must not invoke a planner'))
    executor=DefaultRagExecutor(retrieval_adapter=runtime,provider_adapter=provider)
    original=request()
    original=replace(original,access=replace(original.access,allowed_departments=frozenset({'HR','ENG','tech'}),
                                            allowed_sites=frozenset({'factory','HCM'})))
    plan=QueryPlan((QueryBranch('terms','Điều kiện thanh toán',text_query=True),
                   QueryBranch('total','Tính BOM cho 3 cụm',bom_rollup=original.bom_rollup),
                   QueryBranch('guide','Hướng dẫn đối chiếu',text_query=True,depends_on=('total',))))
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(executor.run(replace(original,bom_rollup=None,query_plan=plan,
            question='Điều kiện thanh toán; tính BOM; hướng dẫn đối chiếu'),RagInvocation('mixed-ready',mode='test')))
    assert result.outcome=='answered'
    branches=result.diagnostics['query_v2']['branches']
    assert [branch['branch_id'] for branch in branches]==['terms','total','guide']
    assert [branch['status'] for branch in branches]==['unknown' if uncited else 'complete','complete','unknown' if missing or uncited else 'complete'], (result.answer, result.diagnostics['generation_metrics'])
    assert [{source['doc_id'] for source in branch['sources']} for branch in (branches[0],branches[2])]==[
        set() if uncited else {17},set() if missing or uncited else {1,18,99}]
    assert result.diagnostics.budget.subqueries==3
    assert result.diagnostics.budget.planners==0 and result.diagnostics.budget.final_generations==1
    assert generations==[True] and '6.00' in result.answer
    assert 'input_tokens' in result.diagnostics['generation_metrics']
    assert 'output_tokens' in result.diagnostics['generation_metrics']
    assert result.answer.count('Thanh toán trong vòng ba mươi ngày')==(0 if uncited else 1)


def test_mixed_draft_rejects_text_dependencies_and_multiple_actions():
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    with pytest.raises(ValueError,match='dependencies on text'):
        QueryPlan((QueryBranch('source','Danh sách hóa đơn',text_query=True),
                   QueryBranch('total','Tổng tiền',scope_comparison=('a','b'),depends_on=('source',))))
    with pytest.raises(ValueError):
        QueryBranch('invalid','Invalid',scope_comparison=('a','b'),text_query=True)
    with pytest.raises(ValueError):
        QueryBranch('invalid','Invalid',text_query='true')

@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_mixed_rest_and_sse_keep_math_and_text_statuses(path,rag_client,bom_store,offline_pipeline,monkeypatch):
    import json
    from types import SimpleNamespace
    from sqlalchemy import create_engine,event
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    from .test_rag_bom_chat import FixtureEngine,SERVICE_HEADERS,VIEWER_PROFILE,PROFILE,_replace_runtime,_replace_server_state
    source_store,_=bom_store
    _,_,executor,_=offline_pipeline
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(shared,'connect')
    def functions(dbapi,record):
        dbapi.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        def json_value(value,path):
            result=json.loads(value) if value else {}
            for key in path[2:].split('.'):
                result=result.get(key) if isinstance(result,dict) else None
            return result
        dbapi.create_function('JSON_VALUE',2,json_value)
    with source_store.connect() as source,shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    monkeypatch.setattr(core,'load_user_profile',lambda **kwargs:{**VIEWER_PROFILE,**PROFILE})
    _replace_runtime(rag_client,executor=executor,runtime_contract=RagRuntimeContract('evaluation',False,120))
    _replace_server_state(rag_client,database_runtime=SimpleNamespace(engine=FixtureEngine(shared)))
    plan={'branches':[{'branch_id':'math','question':'Tính BOM','bom_rollup':{
        'root_key':'document:1','product_scope':'product','site':'factory','order_quantity':'3','rounding_scope':'policy'}},
        {'branch_id':'text','question':'Hướng dẫn đối chiếu','text_query':True}]}
    try:
        response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_id':7,
            'user_question':'Tính BOM; hướng dẫn đối chiếu','query_plan':plan})
        assert response.status_code==200 and '6.00' in response.text
        if path=='/chat':
            query=response.json()['debug_info']['query_v2']
            assert query['schema']=='query-plan-mixed-v1-draft' and query['status']=='partial'
            assert [branch['status'] for branch in query['branches']]==['complete','unknown']
            assert query['branches'][1]['sources']==[]
        else:
            assert 'event: done' in response.text and 'event: citation' in response.text
            assert 'query-plan-mixed-v1-draft' in response.text
    finally:shared.dispose()

