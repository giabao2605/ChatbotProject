"""Three bounded branches share the real SQL/evidence and request lifecycle."""
from dataclasses import replace
import pytest
from sqlalchemy import text
from .test_rag_bom_chat import bom_store,request,run,expand_bom,history_store,PROFILE,store,connection,rag_client


def combined_request():
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    single=request()
    plan=QueryPlan((QueryBranch('relations','Cụm chứa gì?',graph_query=single.bom_rollup.graph),
                    QueryBranch('total','Tính BOM cho 3 cụm',bom_rollup=single.bom_rollup)))
    return replace(single,question='Cụm chứa gì; tính BOM cho 3 cụm',bom_rollup=None,query_plan=plan)


def test_graph_and_bom_branches_keep_separate_results_and_shared_budget(bom_store):
    store,engine=bom_store
    result=run(engine,combined_request())
    assert result.outcome=='answered' and 'part:p' in result.answer and '6.00' in result.answer
    query=result.diagnostics['query_v2']
    assert query['status']=='complete' and [b['status'] for b in query['branches']]==['complete','complete']
    assert [b['branch_id'] for b in query['branches']]==['relations','total']
    assert result.diagnostics.budget.subqueries==2 and result.diagnostics.budget.calculations==1
    assert result.diagnostics.budget.graph_edges==2
    assert all(b['sources'] for b in query['branches'])


def test_large_bom_keeps_full_inputs_while_the_whole_request_context_stays_at_50(bom_store):
    store,engine=bom_store
    expand_bom(store)
    from mech_chatbot.domain.query_plan import QueryPlan
    query=combined_request()
    query=replace(query,query_plan=QueryPlan((query.query_plan.branches[0],
        replace(query.query_plan.branches[1],depends_on=('relations',)))))
    result=run(engine,query)
    branches=result.diagnostics['query_v2']['branches']
    assert result.outcome=='answered' and result.diagnostics['query_v2']['status']=='partial'
    assert [b['status'] for b in branches]==['partial','complete']
    assert branches[0]['data_complete'] is True
    assert branches[1]['executed'] is True and branches[1]['depends_on']==['relations']
    assert branches[1]['math']['steps'][0]['display_value']=='306.00'
    assert result.diagnostics.budget.graph_edges==50
    assert result.diagnostics.budget.graph_examined_relations==51
    assert branches[1]['math']['graph_reused_from']=='relations'


def test_independent_graph_answer_survives_missing_bom_policy(bom_store):
    store,engine=bom_store
    with store.begin() as conn:conn.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='policy'"))
    result=run(engine,combined_request())
    assert result.outcome=='answered' and result.diagnostics['answer_outcome']=='partial_answer'
    branches=result.diagnostics['query_v2']['branches']
    assert [b['status'] for b in branches]==['complete','unknown']
    assert branches[0]['sources'] and branches[1]['sources']==[]
    assert 'part:p' in result.answer and '6.00' not in result.answer


def test_unavailable_evidence_storage_refuses_the_plan_without_numeric_results(bom_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    store,engine=bom_store
    with store.begin() as conn:conn.execute(text('DROP TABLE StructuredEvidenceScope'))
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(DefaultRagExecutor().run(combined_request(),
            RagInvocation('failed-branches',mode='evaluation',capture_math_observations=True)))
    assert result.outcome=='refused' and result.citations==()
    assert result.diagnostics['query_v2']['status']=='unknown'
    assert all(b['status']=='unknown' and not b['sources'] for b in result.diagnostics['query_v2']['branches'])
    assert '6.00' not in result.answer and 'StructuredEvidenceScope' not in result.answer
    observation=export_math_observation(result)
    assert observation['query_branch_schema']=='query-branch-execution-v2'
    assert observation['math_results']==[] and observation['calculation_count']==0
    assert observation['query_branches']==[
        {'branch_id':branch_id,'template':template,'depends_on':[],'executed':True,
         'status':'unknown','data_complete':False,'reason':reason,'source_ids':[]}
        for branch_id,template,reason in (('relations','graph_query','graph_evidence_unavailable'),
                                          ('total','bom_rollup','evidence_unavailable'))]


def test_incomplete_graph_blocks_its_dependent_bom_branch(bom_store):
    store,engine=bom_store
    query=combined_request()
    from mech_chatbot.domain.query_plan import QueryPlan
    second=replace(query.query_plan.branches[1],depends_on=('relations',))
    query=replace(query,query_plan=QueryPlan((query.query_plan.branches[0],second)))
    with store.begin() as conn:conn.execute(text('UPDATE ReviewedGraphOccurrences SET IsLeaf=0'))
    result=run(engine,query)
    assert result.diagnostics['query_v2']['branches'][1]['status']=='blocked'
    assert result.diagnostics.budget.subqueries==1 and result.diagnostics.budget.calculations==0
    assert '6.00' not in result.answer


def test_three_graph_bom_comparison_branches_record_all_five_math_steps(bom_store):
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    store,engine=bom_store
    query=combined_request()
    plan=QueryPlan((*query.query_plan.branches,QueryBranch('compare','So sánh hai phạm vi',scope_comparison=('quantity-scope','policy'))))
    result=run(engine,replace(query,question='Cụm chứa gì; tính BOM; so sánh hai phạm vi',query_plan=plan))
    assert result.diagnostics['query_v2']['status']=='complete' and result.diagnostics.budget.subqueries==3
    assert result.diagnostics.budget.calculations==5 and result.diagnostics.budget.graph_examined_relations==2
    assert [s['step_id'] for s in result.diagnostics['query_v2']['branches'][2]['math']['steps']]==['sum_a','sum_b','delta','percent_change']


def test_completed_plan_captures_actual_math_results_in_branch_order(bom_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    _, engine = bom_store
    query = combined_request()
    plan = QueryPlan((*query.query_plan.branches,
                      QueryBranch('compare', 'So sánh hai phạm vi', scope_comparison=('quantity-scope', 'policy'))))
    query = replace(query, question='Cụm chứa gì; tính BOM; so sánh hai phạm vi', query_plan=plan)
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor().run(query,
            RagInvocation('observed-plan', mode='evaluation', capture_math_observations=True)))
    assert [record['template'] for record in result.observed_math_results] == ['bom_rollup', 'compare_two_scopes']
    assert [record['branch_id'] for record in result.observed_math_results] == ['total', 'compare']
    assert sum(record['domain_step_count'] for record in result.observed_math_results) == 5
    assert result.observed_math_results[0]['sources'][0]['order_quantity'] == '3'
    assert result.observed_math_results[1]['sources'][1]['source_revisions'] == [['99', '5']]
    assert 'observed_math_results' not in dict(result.diagnostics)
    observation=export_math_observation(result)
    assert observation['query_branch_schema']=='query-branch-execution-v2'
    assert [b['template'] for b in observation['query_branches']]==['graph_query','bom_rollup','compare_two_scopes']
    for branch in observation['query_branches']:
        assert set(branch)=={'branch_id','template','depends_on','executed','status','data_complete','reason','source_ids'}
        assert branch['executed'] is True and branch['data_complete'] is True
        assert branch['depends_on']==[] and branch['status']=='complete' and branch['reason'] is None
        assert branch['source_ids']


def test_dependency_skipped_math_is_observed_as_a_branch_not_a_fabricated_calculation(bom_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryPlan
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    store, engine = bom_store
    query = combined_request()
    query = replace(query, query_plan=QueryPlan((query.query_plan.branches[0],
        replace(query.query_plan.branches[1], depends_on=('relations',)))))
    with store.begin() as conn:
        conn.execute(text('UPDATE ReviewedGraphOccurrences SET IsLeaf=0'))
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor().run(query,
            RagInvocation('skipped-math', mode='evaluation', capture_math_observations=True)))
    observation = export_math_observation(result)
    assert observation['math_results'] == [] and observation['calculation_count'] == 0
    assert observation['query_branch_schema']=='query-branch-execution-v2'
    assert observation['query_branches'][0]['executed'] is True
    assert observation['query_branches'][0]['data_complete'] is False
    assert observation['query_branches'][1] == {
        'branch_id':'total','template':'bom_rollup','depends_on':['relations'],'executed':False,
        'status':'blocked','data_complete':False,'reason':'dependency_unavailable','source_ids':[]}


def test_captured_bom_uses_all_51_occurrences_despite_the_50_relation_answer_context(bom_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    store, engine = bom_store
    expand_bom(store)
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor().run(combined_request(),
            RagInvocation('whole-bom', mode='evaluation', capture_math_observations=True)))
    observation = export_math_observation(result)
    record = observation['math_results'][0]
    assert len(record['sources'][0]['occurrences']) == 51
    assert record['steps'][0]['value'] == '306'
    assert result.diagnostics.budget.graph_edges == 50
    assert observation['calculation_count'] == 1


@pytest.mark.parametrize('mode',['query','standalone'])
def test_all_branches_share_one_sql_snapshot_when_a_review_changes_between_reads(bom_store,tmp_path,mode):
    import json
    from sqlalchemy import create_engine,event
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    from mech_chatbot.db.repositories.structured_evidence import review_numeric_fact,review_scope_completeness
    from mech_chatbot.domain.structured_evidence import RoundingRule
    from .test_rag_bom_chat import FixtureEngine
    store,engine=bom_store
    database=tmp_path/'query-snapshot.sqlite'
    reader=create_engine('sqlite:///'+database.as_posix())
    writer=create_engine('sqlite:///'+database.as_posix())
    @event.listens_for(reader,'connect')
    def functions(dbapi,record):
        dbapi.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        dbapi.create_function('JSON_VALUE',2,lambda value,path:json.loads(value).get(path[2:]))
    with store.connect() as source,reader.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
        target.exec_driver_sql('PRAGMA journal_mode=WAL')
    policy_reads=[]
    changed=[]
    @event.listens_for(reader,'before_cursor_execute')
    def change_review(conn,cursor,statement,params,context,many):
        if 'FROM StructuredEvidenceScope WHERE ScopeID=' in statement and params==('policy',):
            policy_reads.append(True)
            if len(policy_reads)==2:
                changed.append(True)
                review_numeric_fact('fact-policy',PROFILE,action='approve',value_text='4',note='Changed fixture review',db_engine=writer)
                review_scope_completeness('policy',PROFILE,expected_row_ids=('policy-row',),rounding_rule=RoundingRule(2),
                    definition='Reviewed piece quantity',note='Changed fixture scope',db_engine=writer)
    branch=QueryBranch('first','So sánh hai phạm vi',scope_comparison=('quantity-scope','policy'))
    query=replace(request(),bom_rollup=None,question='So sánh lần một; so sánh lần hai',
                  query_plan=QueryPlan((branch,replace(branch,branch_id='second'))))
    if mode=='standalone':query=replace(query,query_plan=None,scope_comparison=('quantity-scope','policy'))
    try:
        result=run(FixtureEngine(reader),query)
        assert changed and result.outcome=='answered'
        if mode=='query':
            branches=result.diagnostics['query_v2']['branches']
            assert [b['math']['steps'][1]['display_value'] for b in branches]==['1.00','1.00']
            assert branches[0]['math']['scopes']==branches[1]['math']['scopes']
        else:assert result.diagnostics['math_v2']['steps'][1]['display_value']=='1.00'
        current=run(FixtureEngine(reader),replace(query,query_plan=None,scope_comparison=('quantity-scope','policy')))
        assert current.diagnostics['math_v2']['steps'][1]['display_value']=='4.00'
    finally:
        reader.dispose();writer.dispose()


def test_all_branches_share_the_same_examined_relation_ceiling(bom_store):
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    from mech_chatbot.rag.execution import DefaultRagExecutor,RequestBudgetLimits,RagInvocation,collect_rag_events
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    store,engine=bom_store
    query=combined_request()
    query=replace(query,question='Cụm chứa gì; tính BOM; xem quan hệ',query_plan=QueryPlan((*query.query_plan.branches,
        QueryBranch('extra','Xem quan hệ',graph_query=query.query_plan.branches[0].graph_query))))
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(DefaultRagExecutor(budget_limits=RequestBudgetLimits(graph_examined_relations=3)).run(query,RagInvocation('limit',mode='test')))
    assert result.diagnostics.budget.graph_examined_relations==3
    assert result.diagnostics['query_v2']['branches'][2]['status']=='partial'
    assert result.diagnostics['query_v2']['branches'][2]['graph']['graph_context']['max_relations']==1


@pytest.mark.parametrize('count,ceiling',[(51,153),(167,500)])
def test_custom_executor_budgets_cannot_raise_the_graph_v2_request_caps(bom_store,count,ceiling):
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    from mech_chatbot.rag.execution import DefaultRagExecutor,RequestBudgetLimits,RagInvocation,collect_rag_events
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    store,engine=bom_store
    expand_bom(store,count=count)
    query=combined_request()
    graph=query.query_plan.branches[0].graph_query
    query=replace(query,question='Cụm chứa gì; dùng ở đâu; xem quan hệ',query_plan=QueryPlan(tuple(
        QueryBranch(str(i),'Xem quan hệ',graph_query=graph) for i in range(3))))
    limits=RequestBudgetLimits(graph_edges=150,graph_examined_relations=1500)
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(DefaultRagExecutor(budget_limits=limits).run(query,RagInvocation('caps',mode='test')))
    assert result.diagnostics.budget.graph_edges==50
    assert result.diagnostics.budget.graph_examined_relations==ceiling
    assert limits.graph_edges==150 and limits.graph_examined_relations==1500


def test_combined_history_revalidates_the_stored_remaining_bounds(bom_store,history_store):
    from mech_chatbot.db.repositories import chat
    store,engine=bom_store
    expand_bom(store)
    result=run(engine,combined_request())
    with store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'plan','Compare',:answer,NULL,NULL,NULL,'approver','2026-10-06')"),{'answer':result.answer})
    assert chat.save_answer_evidence(11,list(result.diagnostics['retrieved_docs']),db_engine=engine)
    assert chat.save_answer_sources(11,list(result.citations),db_engine=engine)
    def read():return chat.get_chat_history('plan',username='approver',allowed_departments=['tech','ENG'],
        allowed_sites=['factory'],user_clearance='internal',db_engine=engine)[-1]
    assert read()['content']==result.answer
    with store.begin() as conn:conn.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='policy'"))
    assert '306.00' not in read()['content']


def test_full_graph_manifest_allows_dependent_bom_despite_truncated_answer_context(bom_store):
    from mech_chatbot.domain.query_plan import QueryPlan
    store,engine=bom_store
    expand_bom(store)
    query=combined_request()
    query=replace(query,query_plan=QueryPlan((query.query_plan.branches[0],replace(query.query_plan.branches[1],depends_on=('relations',)))))
    result=run(engine,query)
    assert result.diagnostics['query_v2']['branches'][0]['status']=='partial'
    assert result.diagnostics['query_v2']['branches'][1]['status']=='complete'
    assert result.diagnostics['query_v2']['branches'][1]['math']['steps'][0]['display_value']=='306.00'


@pytest.mark.parametrize('invalid',['overflow','duplicate','forward','cycle','empty_action','two_actions'])
def test_query_plan_rejects_invalid_shape_before_execution(invalid):
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    base=combined_request().query_plan.branches
    with pytest.raises(ValueError):
        if invalid=='overflow':QueryPlan((*base,replace(base[0],branch_id='third'),replace(base[0],branch_id='fourth')))
        elif invalid=='duplicate':QueryPlan((base[0],base[0]))
        elif invalid=='forward':QueryPlan((replace(base[0],depends_on=('total',)),base[1]))
        elif invalid=='cycle':QueryPlan((replace(base[0],depends_on=('relations',)),))
        elif invalid=='empty_action':QueryBranch('none','Question')
        else:replace(base[0],bom_rollup=base[1].bom_rollup)


def test_query_plan_overflow_and_shared_cancellation_return_no_sources(bom_store):
    import threading
    from mech_chatbot.rag.execution import DefaultRagExecutor,RagInvocation,RagToken,RagCancelled,RequestBudgetLimits
    store,engine=bom_store
    result=run(engine,replace(combined_request(),question='Chứa gì; tổng BOM; vật liệu; giao hàng'))
    assert result.outcome=='refused' and result.citations==() and result.diagnostics.budget.subqueries==0
    cancel=threading.Event();cancel.set()
    events=list(DefaultRagExecutor().run(combined_request(),RagInvocation('cancel',mode='test'),cancellation=cancel))
    assert any(isinstance(e,RagCancelled) for e in events) and not any(isinstance(e,RagToken) for e in events)
    events=list(DefaultRagExecutor(budget_limits=RequestBudgetLimits(deadline_seconds=0)).run(combined_request(),RagInvocation('deadline',mode='test')))
    assert not any(isinstance(e,RagToken) for e in events)


@pytest.mark.parametrize('mode,roles,code',[
    ('production',{'reviewer'},'ValueError'),
    ('pilot_replay',{'reviewer'},'ValueError'),
    ('evaluation',{'viewer'},'PermissionError'),
])
def test_query_executor_checks_context_and_role_even_without_http(mode,roles,code):
    from mech_chatbot.rag.execution import DefaultRagExecutor,RagInvocation,RagFailed,RagToken,RagCitation
    query=combined_request()
    query=replace(query,access=replace(query.access,roles=frozenset(roles)))
    events=list(DefaultRagExecutor().run(query,RagInvocation('guard',mode=mode)))
    assert any(isinstance(e,RagFailed) and e.code==code for e in events)
    assert not any(isinstance(e,(RagToken,RagCitation)) for e in events)


def test_overflow_in_one_branch_is_refused_without_losing_an_independent_branch(bom_store):
    from mech_chatbot.domain.query_plan import QueryPlan
    store,engine=bom_store
    query=combined_request()
    first=replace(query.query_plan.branches[0],question='Chứa gì; dùng ở đâu; vật liệu; thời gian')
    query=replace(query,query_plan=QueryPlan((first,query.query_plan.branches[1])))
    result=run(engine,query)
    assert result.outcome=='answered' and [b['status'] for b in result.diagnostics['query_v2']['branches']]==['unknown','complete']
    assert '6.00' in result.answer


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_three_branch_plan_runs_through_rest_and_sse_with_actual_sql(path,rag_client,bom_store,monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import create_engine,event
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution import DefaultRagExecutor
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    from .test_rag_bom_chat import FixtureEngine,SERVICE_HEADERS,VIEWER_PROFILE,_replace_runtime,_replace_server_state
    store,engine=bom_store
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(shared,'connect')
    def functions(dbapi,record):
        import json
        dbapi.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        def json_value(value,path):
            result=json.loads(value) if value else {}
            for key in path[2:].split('.'):result=result.get(key) if isinstance(result,dict) else None
            return result
        dbapi.create_function('JSON_VALUE',2,json_value)
    with store.connect() as source,shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    monkeypatch.setattr(core,'load_user_profile',lambda **kwargs:{**VIEWER_PROFILE,**PROFILE})
    _replace_runtime(rag_client,executor=DefaultRagExecutor(),runtime_contract=RagRuntimeContract('evaluation',False,120))
    _replace_server_state(rag_client,database_runtime=SimpleNamespace(engine=FixtureEngine(shared)))
    graph={'root_key':'document:1','product_scope':'product','site':'factory'}
    plan={'branches':[{'branch_id':'graph','question':'Cụm chứa gì','graph_query':graph},
        {'branch_id':'bom','question':'Tính BOM','bom_rollup':{**graph,'order_quantity':'3','rounding_scope':'policy'}},
        {'branch_id':'compare','question':'So sánh phạm vi','scope_comparison':{'scope_a':'quantity-scope','scope_b':'policy'}}]}
    try:
        response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_id':7,'user_question':'Cụm chứa gì; tính BOM; so sánh phạm vi','query_plan':plan})
        assert response.status_code==200 and '6.00' in response.text and 'part:p' in response.text
        if path=='/chat':
            query=response.json()['debug_info']['query_v2']
            assert query['status']=='complete' and len(query['branches'])==3
        else:assert 'event: citation' in response.text and 'event: done' in response.text and 'query-plan-v2' in response.text
    finally:shared.dispose()
