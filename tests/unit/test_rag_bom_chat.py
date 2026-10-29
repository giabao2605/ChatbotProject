"""BOM through the public chat executor with reviewed SQL fixture sources."""
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
import json

import pytest
from sqlalchemy import text
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope,review_numeric_fact,review_scope_completeness
from mech_chatbot.domain.structured_evidence import NumericFact,SourceLocator,ScopeManifest,RoundingRule
from mech_chatbot.rag.execution import AccessScope,DefaultRagExecutor,RagRequest,RagInvocation,collect_rag_events
from .test_structured_evidence_repository import store
from .test_structured_chat_history import history_store
from .test_rag_server_endpoints import rag_client,SERVICE_HEADERS,VIEWER_PROFILE,_replace_runtime,_replace_server_state
from .test_graph_v2_repository import connection,SqliteConnection

PROFILE={'user_id':7,'username':'approver','roles':['reviewer'],'allowed_departments':['tech','ENG'],
         'allowed_sites':['factory'],'max_security_level':'internal'}


class FixtureEngine:
    def __init__(self,store):self.store=store

    @contextmanager
    def connect(self):
        with self.store.connect() as conn:
            wrapper=SqliteConnection(conn);wrapper.dialect=conn.dialect
            yield wrapper

    @contextmanager
    def begin(self):
        with self.store.begin() as conn:
            wrapper=SqliteConnection(conn);wrapper.dialect=conn.dialect
            yield wrapper


@pytest.fixture
def bom_store(store,connection):
    # Merge existing offline fixtures; all numerical policies use the real review API.
    with store.begin() as target:
        target.execute(text('DELETE FROM TaiLieu'))
        for name,sql in connection.conn.execute(text("SELECT name,sql FROM sqlite_master WHERE type='table'")):
            if name.startswith('Structured') or name=='PhongBanChiaSe':continue
            if name!='TaiLieu':target.execute(text(sql))
            for row in connection.conn.execute(text(f'SELECT * FROM {name}')).mappings():
                values=dict(row)
                if name=='TaiLieu':values={**values,'KnowledgeApproverUserID':7,'TenFile':'fixture-bom.pdf'}
                columns=','.join(values);params=','.join(':'+key for key in values)
                target.execute(text(f'INSERT INTO {name}({columns}) VALUES({params})'),values)
        target.execute(text("INSERT INTO PhongBanChiaSe VALUES(1,'tech'),(2,'tech'),(3,'finance'),(99,'ENG')"))
        target.execute(text("INSERT INTO TaiLieu(DocID,VersionNo,TenFile,ThuMuc,Site,SecurityLevel,LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,KnowledgeApproverUserID) VALUES(99,5,'policy.csv','ENG','factory','internal','published','approved','published',1,1,'effective',7)"))
        dbapi=target.connection.driver_connection
        dbapi.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        def json_value(value,path):
            result=json.loads(value) if value else {}
            for key in path[2:].split('.'):result=result.get(key) if isinstance(result,dict) else None
            return result
        dbapi.create_function('JSON_VALUE',2,json_value)
    for scope_id,doc,version,snapshot,digest,value,row in (
        ('quantity-scope','1','1','snapshot','a'*64,'2','page-1:row-1'),
        ('policy','99','5','policy-snapshot','b'*64,'1','policy-row')):
        fact=NumericFact('fact-'+scope_id,'Quantity',value,Decimal(value),'piece',SourceLocator(doc,version,page=1,row_key=row),
                         source_hash=digest,extraction_method='fixture')
        scope=ScopeManifest(scope_id,snapshot,(fact,),source_revisions=((doc,version),),unit='piece')
        save_extracted_scope(scope,db_engine=store)
        review_numeric_fact(fact.fact_id,PROFILE,action='approve',note='Offline fixture source review',db_engine=store)
        review_scope_completeness(scope_id,PROFILE,expected_row_ids=(row,),rounding_rule=RoundingRule(2),
                                 definition='Reviewed piece quantity',note='Offline fixture policy review',db_engine=store)
    return store,FixtureEngine(store)


def request(quantity='3'):
    from mech_chatbot.domain.graph_query import GraphQuery,BomRollupQuery
    return RagRequest('Tính BOM cho số lượng đã chọn',AccessScope(roles=frozenset({'reviewer'}),
        allowed_departments=frozenset({'tech','ENG'}),allowed_sites=frozenset({'factory'}),max_security_level='internal'),
        bom_rollup=BomRollupQuery(GraphQuery('document:1','product','factory'),quantity,'policy'))


def run(engine,query=None):
    with bind_repository_runtime(db_engine=engine):
        return collect_rag_events(DefaultRagExecutor().run(query or request(),RagInvocation('bom',mode='evaluation')))


def test_bom_chat_uses_reviewed_occurrences_and_independent_policy_revision(bom_store):
    store,engine=bom_store
    result=run(engine)
    assert result.outcome=='answered'
    math=result.diagnostics['math_v2']
    assert math['template']=='bom_rollup' and math['status']=='complete'
    assert [(s['step_id'],s['display_value']) for s in math['steps']]==[('part:p','6.00')]
    assert result.diagnostics.budget.calculations==1 and result.diagnostics.budget.final_generations==0
    assert {c['doc_id'] for c in result.citations}=={1,99}
    assert {c['version_no'] for c in result.citations}=={1,5}
    assert all('[SRC:'+c['source_id']+']' in result.answer for c in result.citations)


def expand_bom(store,count=51,unique_parts=False):
    facts=[]
    with store.begin() as conn:
        conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=:n WHERE DocID=1'),{'n':count})
        for number in range(2,count+1):
            part=f'p{number}' if unique_parts else 'p'
            row=f'row-{number}';quote=json.dumps({'source_row_id':row,'part':part,'quantity':2})
            if unique_parts:conn.execute(text('INSERT INTO KnowledgeGraphNode VALUES(:id,:key)'),{'id':number+1,'key':'part:'+part})
            conn.execute(text("INSERT INTO KnowledgeGraphEdge VALUES(:id,1,:target,'approved','CONTAINS_PART',1,1,1,:quote)"),
                         {'id':number,'target':number+1 if unique_parts else 2,'quote':quote})
            conn.execute(text('INSERT INTO BangKeVatTu VALUES(1,1,:part,:quote)'),{'part':part,'quote':quote})
            conn.execute(text("INSERT INTO ReviewedGraphOccurrences VALUES(:id,:edge,1,1,'product','factory',:part,NULL,'approved','reviewer','now',1,'quantity',:row,'2','piece','per_parent',1)"),
                         {'id':f'1:{row}:1','edge':number,'part':'part:'+part,'row':row})
            facts.append(NumericFact('extra-'+str(number),'Quantity','2',Decimal('2'),'piece',
                SourceLocator('1','1',page=1,row_key='page-1:'+row),source_hash='a'*64,extraction_method='fixture'))
    scope=ScopeManifest('expanded-quantity-scope','snapshot',tuple(facts),source_revisions=(('1','1'),),unit='piece')
    save_extracted_scope(scope,db_engine=store)
    for fact in facts:review_numeric_fact(fact.fact_id,PROFILE,action='approve',note='Offline fixture BOM row review',db_engine=store)
    review_scope_completeness(scope.scope_id,PROFILE,expected_row_ids=tuple(f.locator.row_key for f in facts),
        rounding_rule=RoundingRule(2),definition='Reviewed extra BOM rows',note='Offline fixture scope review',db_engine=store)


def test_bom_chat_sums_all_51_occurrences_instead_of_the_50_context_edges(bom_store):
    store,engine=bom_store
    expand_bom(store)
    result=run(engine)
    assert result.outcome=='answered' and result.diagnostics['math_v2']['steps'][0]['display_value']=='306.00'
    assert len(result.diagnostics['math_v2']['steps'][0]['source_ids'])==51
    assert result.diagnostics.budget.graph_edges==50 and result.diagnostics['graph_examined_relations']==51
    assert len([c for c in result.citations if c.get('graph_context')])==51


def test_bom_chat_records_51_part_totals_as_51_steps_without_changing_v1(bom_store):
    store,engine=bom_store
    expand_bom(store,unique_parts=True)
    result=run(engine)
    assert len(result.diagnostics['math_v2']['steps'])==51 and result.diagnostics.budget.calculations==51
    assert all(step['display_value']=='6.00' for step in result.diagnostics['math_v2']['steps'])
    from mech_chatbot.rag.execution import RequestBudgetLimits
    assert RequestBudgetLimits().calculations==1


@pytest.mark.parametrize('change',['missing_row','unreviewed','unresolved','policy','rights','revision'])
def test_incomplete_bom_or_policy_refuses_totals_and_private_metadata(bom_store,change):
    store,engine=bom_store
    statements={'missing_row':'UPDATE ReviewedGraphDeclarations SET ExpectedRows=2 WHERE DocID=1',
                'unreviewed':"UPDATE ReviewedGraphOccurrences SET ReviewStatus='pending'",
                'unresolved':'UPDATE ReviewedGraphOccurrences SET IsLeaf=0',
                'policy':"UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='policy'",
                'rights':'DELETE FROM PhongBanChiaSe WHERE DocID=99',
                'revision':'UPDATE TaiLieu SET VersionNo=6 WHERE DocID=99'}
    with store.begin() as conn:conn.execute(text(statements[change]))
    result=run(engine)
    assert result.outcome=='refused' and result.citations==()
    assert result.diagnostics['math_v2']['steps']==[] and result.diagnostics.budget.calculations==0
    assert 'part:p' not in result.answer and 'policy-snapshot' not in str(result.diagnostics['math_v2'])


def test_bom_history_keeps_all_51_occurrence_pins_and_rechecks_policy(bom_store,history_store):
    from mech_chatbot.db.repositories import chat
    store,engine=bom_store
    expand_bom(store)
    result=run(engine)
    with store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'bom','BOM',:answer,NULL,NULL,NULL,'approver','2026-10-06')"),{'answer':result.answer})
    assert chat.save_answer_evidence(11,list(result.diagnostics['retrieved_docs']),db_engine=engine)
    assert chat.save_answer_sources(11,list(result.citations),db_engine=engine)
    def read():return chat.get_chat_history('bom',username='approver',allowed_departments=['tech','ENG'],
                                            allowed_sites=['factory'],user_clearance='internal',db_engine=engine)[-1]
    restored=read()
    assert restored['content']==result.answer and len(restored['structured_citations'])==52
    review_scope_completeness('policy',PROFILE,expected_row_ids=('policy-row',),rounding_rule=RoundingRule(0),
                             definition='Reviewed piece quantity',note='Offline changed policy',db_engine=store)
    revoked=read()
    assert '306.00' not in revoked['content'] and revoked.get('structured_citations',[])==[]


@pytest.mark.parametrize('quantity',['0','3.5'])
def test_bom_chat_preserves_decimal_order_quantity_including_zero(bom_store,quantity):
    store,engine=bom_store
    result=run(engine,request(quantity))
    assert result.outcome=='answered'
    assert result.diagnostics['math_v2']['steps'][0]['display_value']==('0.00' if quantity=='0' else '7.00')


def test_reviewed_empty_bom_keeps_the_declaration_and_policy_sources(bom_store):
    store,engine=bom_store
    with store.begin() as conn:
        conn.execute(text('DELETE FROM ReviewedGraphOccurrences'))
        conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=0 WHERE DocID=1'))
    result=run(engine)
    assert result.outcome=='answered' and result.diagnostics['math_v2']['steps']==[]
    assert {c['doc_id'] for c in result.citations}=={1,99} and 'Không có chi tiết' in result.answer


@pytest.mark.parametrize('quantity,scope,direction',[('-1','policy','contains'),('NaN','policy','contains'),
    ('1e2','policy','contains'),('9'*129,'policy','contains'),('1','','contains'),('1','policy','where_used')])
def test_bom_context_rejects_unbounded_or_noncanonical_operands(quantity,scope,direction):
    from mech_chatbot.domain.graph_query import GraphQuery,BomRollupQuery
    with pytest.raises(ValueError):BomRollupQuery(GraphQuery('document:1' if direction=='contains' else 'part:p','product','factory',direction),quantity,scope)


def test_bom_cancellation_deadline_and_overflow_return_no_totals(bom_store):
    import threading
    from mech_chatbot.rag.execution import RagCancelled,RagToken,RequestBudgetLimits
    store,engine=bom_store
    cancel=threading.Event();cancel.set()
    events=list(DefaultRagExecutor().run(request(),RagInvocation('cancel',mode='evaluation'),cancellation=cancel))
    assert any(isinstance(e,RagCancelled) for e in events) and not any(isinstance(e,RagToken) for e in events)
    events=list(DefaultRagExecutor(budget_limits=RequestBudgetLimits(deadline_seconds=0)).run(request(),RagInvocation('deadline',mode='evaluation')))
    assert not any(isinstance(e,RagToken) for e in events)
    result=run(engine,replace(request(),question='Chứa gì; số lượng; vật liệu; đổi giá'))
    assert result.outcome=='refused' and result.diagnostics.budget.calculations==0


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_bom_rest_and_sse_run_actual_sql_with_versioned_math_steps(path,rag_client,bom_store,monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import create_engine,event
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    store,engine=bom_store
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(shared,'connect')
    def functions(dbapi,record):
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
    try:
        response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_id':7,'user_question':'BOM cho 3 cụm',
            'bom_rollup':{'root_key':'document:1','product_scope':'product','site':'factory','order_quantity':'3','rounding_scope':'policy'}})
        assert response.status_code==200 and '6.00' in response.text
        if path=='/chat':assert response.json()['debug_info']['math_v2']['template']=='bom_rollup'
        else:assert 'event: citation' in response.text and 'event: done' in response.text
    finally:shared.dispose()


@pytest.mark.parametrize('mode,roles,code',[('production',{'reviewer'},'ValueError'),('pilot_replay',{'reviewer'},'ValueError'),
                                         ('evaluation',{'viewer'},'PermissionError')])
def test_bom_executor_enforces_its_own_evaluation_and_role_boundary(mode,roles,code):
    from mech_chatbot.rag.execution import RagFailed
    query=request();query=replace(query,access=replace(query.access,roles=frozenset(roles)))
    events=list(DefaultRagExecutor().run(query,RagInvocation('guard',mode=mode)))
    assert any(isinstance(e,RagFailed) and e.code==code for e in events)


def test_bom_cannot_silently_mix_with_a_second_template():
    query=request()
    with pytest.raises(ValueError):replace(query,scope_comparison=('a','b'))
    with pytest.raises(ValueError):replace(query,graph_query=query.bom_rollup.graph)


def test_bom_closes_policy_snapshot_after_graph_read_before_calculating(bom_store):
    store,engine=bom_store
    changed=[]
    class ConcurrentEngine(FixtureEngine):
        @contextmanager
        def connect(self):
            with super().connect() as connection:
                execute=connection.execute
                def sql(statement,params):
                    if 'ReviewedGraphDeclarations' in str(statement) and not changed:
                        changed.append(True)
                        with store.begin() as conn:
                            conn.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='policy'"))
                    return execute(statement,params)
                connection.execute=sql
                yield connection
    result=run(ConcurrentEngine(store))
    assert changed and result.outcome=='refused' and result.citations==() and result.diagnostics.budget.calculations==0
