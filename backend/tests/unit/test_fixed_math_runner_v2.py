"""Provider-free candidate observations at the frozen-case runner seam."""
import hashlib
import json

import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from .test_rag_scope_comparison import approved_pair, store
from .test_rag_bom_chat import bom_store, connection


BINDING = {'source_commit':'a'*40, 'snapshot_fingerprint':'b'*64,
           'config_hash':'c'*64, 'gate_hash':'d'*64, 'eval_hash':'e'*64}
ACTOR = {'user_id':7, 'username':'reviewer-fixture','user_department':'ENG',
         'user_roles':['reviewer'], 'allowed_departments':['ENG'],
         'allowed_sites':['factory'], 'max_security_level':'internal'}


def frozen_case(root, **changes):
    case={'schema':'fixed-template-case-v2', **BINDING, 'run_id':'fixture-run',
          'case_id':'ENG-1', 'department':'ENG', 'dataset_sha256':'f'*64,
          'actor':ACTOR, 'question':'So sánh hai phạm vi đã chọn',
          'selection':{'scope_comparison':{'scope_a':'a','scope_b':'b'}}, **changes}
    raw=json.dumps(case).encode()
    path=root/'frozen-case.json'
    path.write_bytes(raw)
    return {'path':path.name,'sha256':hashlib.sha256(raw).hexdigest(),'schema':case['schema']}


def current_profile(user_id, username, *, strict):
    assert user_id==7 and username=='reviewer-fixture' and strict is True
    from mech_chatbot.db.evidence_snapshot import evidence_snapshot_active
    assert evidence_snapshot_active()
    return {'user_id':7, 'username':username,'department':'ENG','roles':['reviewer'],
            'allowed_departments':['ENG'],'allowed_sites':['factory'],'max_security_level':'internal'}


def test_frozen_case_runner_observes_real_operands_without_provider_or_adjudication(store,tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    approved_pair(store)
    ref=frozen_case(tmp_path)
    with bind_repository_runtime(db_engine=store):
        observation=run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=current_profile)
    assert observation['schema']=='fixed-template-case-observation-v2'
    assert observation['case_id']=='ENG-1' and observation['run_id']=='fixture-run'
    assert observation['case_sha256']==ref['sha256']
    assert observation['actor']==ACTOR and observation['calculation_count']==4
    assert [s['facts'][0]['value'] for s in observation['math_results'][0]['sources']]==['4','8']
    assert observation['math_results'][0]['steps'][2]['value']=='4'
    assert observation['actual_outcome']=='full_answer'
    assert observation['accepted'] is False and observation['offline_only'] is True
    assert observation['binding_verification']=='pending' and observation['adjudication_reference'] is None
    assert 'answer_correct' not in observation and 'reviewer_id' not in observation
    assert observation['execution_budget']==dict.fromkeys((
        'planners','subqueries','corrections','repairs','graph_edges','graph_examined_relations',
        'provider_retries','final_generations'),0) | {'deadline_exceeded':False}


@pytest.mark.parametrize('fault',['hash','binding','viewer','sites','template','inactive'])
def test_unbound_or_unauthorized_frozen_case_never_executes(store,tmp_path,fault):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.rag.execution import DefaultRagExecutor
    ref=frozen_case(tmp_path,**({'selection':{'arbitrary_expression':'1+1'}} if fault=='template' else {}))
    binding={**BINDING, 'source_commit':'0'*40} if fault=='binding' else BINDING
    if fault=='hash':ref={**ref,'sha256':'0'*64}
    def loader(*args,**kwargs):
        profile=current_profile(*args,**kwargs)
        if fault=='viewer':profile={**profile,'roles':['viewer']}
        if fault=='sites':profile={**profile,'allowed_sites':[]}
        return None if fault=='inactive' else profile
    def forbidden(state):
        raise AssertionError('Rejected cases must not execute')
    with bind_repository_runtime(db_engine=store), pytest.raises((ValueError,PermissionError)):
        run_fixed_case(ref,root=tmp_path,run_binding=binding,profile_loader=loader,
                       executor=DefaultRagExecutor(execute_pipeline=forbidden))


def actor_tables(engine):
    with engine.begin() as conn:
        for sql in (
            'ALTER TABLE Users ADD DisplayName TEXT',
            'ALTER TABLE Users ADD Department TEXT',
            'ALTER TABLE Users ADD PreferredLanguage TEXT',
            'CREATE TABLE UserDepartments (UserID INTEGER, Department TEXT)',
            'CREATE TABLE Departments (DeptCode TEXT, Status TEXT, IsActive INTEGER)',
            'CREATE TABLE UserSecurityClearance (UserID INTEGER, MaxLevel TEXT)',
            'CREATE TABLE UserSites (UserID INTEGER, Site TEXT)',
            "UPDATE Users SET Username='reviewer-fixture',DisplayName='Fixture',Department='ENG',PreferredLanguage='vi' WHERE UserID=7",
            "INSERT INTO UserDepartments VALUES (7,'ENG')",
            "INSERT INTO Departments VALUES ('ENG','active',1)",
            "INSERT INTO UserSecurityClearance VALUES (7,'internal')",
            "INSERT INTO UserSites VALUES (7,'factory')",
        ):
            conn.exec_driver_sql(sql)


def test_default_runner_resolves_real_actor_and_math_on_the_same_sql_connection(store,tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from sqlalchemy import event
    approved_pair(store)
    actor_tables(store)
    ref=frozen_case(tmp_path)
    connections=[]
    def capture(conn,cursor,sql,params,context,many):
        if 'FROM Users ' in sql or 'FROM StructuredEvidenceScope ' in sql:
            connections.append(id(conn))
    event.listen(store,'before_cursor_execute',capture)
    with bind_repository_runtime(db_engine=store):
        observed=run_fixed_case(ref,root=tmp_path,run_binding=BINDING)
    assert observed['calculation_count']==4 and len(connections)>=3
    assert len(set(connections))==1


@pytest.mark.parametrize('failure',['cancelled','deadline','commit'])
def test_runner_does_not_return_observations_after_cancel_deadline_or_snapshot_failure(store,tmp_path,failure):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.rag.execution import RequestBudgetLimits
    from sqlalchemy import event
    import threading
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    approved_pair(store)
    ref=frozen_case(tmp_path)
    stop=threading.Event()
    if failure=='cancelled':stop.set()
    def fail_commit(conn):
        raise RuntimeError('Snapshot commit failed')
    if failure=='commit':event.listen(store,'commit',fail_commit)
    try:
        expected={'cancelled':ExternalAICallCancelled,'deadline':TimeoutError,'commit':RuntimeError}[failure]
        with bind_repository_runtime(db_engine=store),pytest.raises(expected):
            run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=current_profile,
                cancellation=stop,budget_limits=RequestBudgetLimits(deadline_seconds=0 if failure=='deadline' else 120))
    finally:
        if failure=='commit':event.remove(store,'commit',fail_commit)


@pytest.mark.parametrize('failure',['cancelled','deadline'])
def test_cancellation_or_deadline_during_snapshot_commit_blocks_observation(store,tmp_path,monkeypatch,failure):
    from scripts.eval import run_fixed_math_v2 as runner
    from sqlalchemy import event
    import threading
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    approved_pair(store)
    ref=frozen_case(tmp_path)
    stop=threading.Event()
    clock=[runner.monotonic()]
    monkeypatch.setattr(runner,'monotonic',lambda:clock[0])
    def interrupt_commit(conn):
        if failure=='cancelled':stop.set()
        else:clock[0]+=1000
    event.listen(store,'commit',interrupt_commit)
    try:
        with bind_repository_runtime(db_engine=store),pytest.raises(ExternalAICallCancelled if failure=='cancelled' else TimeoutError):
            runner.run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=current_profile,cancellation=stop)
    finally:
        event.remove(store,'commit',interrupt_commit)


@pytest.mark.parametrize('kind',['graph_query','bom_rollup','query_plan'])
def test_runner_executes_validated_graph_bom_and_query_selections(bom_store,tmp_path,kind):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    _,engine=bom_store
    graph={'root_key':'document:1','product_scope':'product','site':'factory'}
    bom={**graph,'order_quantity':'3','rounding_scope':'policy'}
    plan={'branches':[{'branch_id':'graph','question':'Cụm chứa gì?', 'graph_query':graph},
                      {'branch_id':'bom','question':'Tính BOM', 'bom_rollup':bom,'depends_on':['graph']}]}
    selection={kind:{'graph_query':graph,'bom_rollup':bom,'query_plan':plan}[kind]}
    actor={**ACTOR,'allowed_departments':['tech','ENG']}
    ref=frozen_case(tmp_path,actor=actor,selection=selection,question='Cụm chứa gì; tính BOM')
    def loader(*args,**kwargs):
        return {**current_profile(*args,**kwargs),'allowed_departments':['tech','ENG']}
    with bind_repository_runtime(db_engine=engine):
        observed=run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=loader)
    assert observed['actual_outcome']=='full_answer' and observed['citations']
    assert observed['calculation_count']==(0 if kind=='graph_query' else 1)
    if kind!='graph_query':assert observed['math_results'][0]['steps'][0]['value']=='6'
    if kind=='query_plan':
        assert [b['executed'] for b in observed['query_branches']]==[True,True]
        assert observed['math_results'][0]['branch_id']=='bom'


@pytest.mark.parametrize('changes',[
    {'run_id':'../escape'}, {'question':''}, {'schema':'wrong'}, {'dataset_sha256':False},
    {'actor':{**ACTOR,'user_id':True}}, {'actor':{**ACTOR,'user_roles':['reviewer','reviewer']}},
    {'actor':{**ACTOR,'allowed_sites':[True]}}, {'actor':{**ACTOR,'max_security_level':'secret'}},
    {'selection':{'scope_comparison':{'scope_a':'a','scope_b':'b'},'graph_query':{}}},
])
def test_malformed_frozen_cases_fail_before_actor_reads(store,tmp_path,changes):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    ref=frozen_case(tmp_path,**changes)
    def forbidden(*args,**kwargs):
        raise AssertionError('Malformed case must fail before actor lookup')
    with bind_repository_runtime(db_engine=store),pytest.raises(ValueError):
        run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=forbidden)


@pytest.mark.parametrize('seconds',[float('nan'),float('inf'),-1,True])
def test_runner_rejects_invalid_deadlines(store,tmp_path,seconds):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.rag.execution import RequestBudgetLimits
    with bind_repository_runtime(db_engine=store),pytest.raises(ValueError):
        run_fixed_case(frozen_case(tmp_path),root=tmp_path,run_binding=BINDING,
            profile_loader=current_profile,budget_limits=RequestBudgetLimits(deadline_seconds=seconds))


@pytest.mark.parametrize('selection', [
    {'scope_comparison':{'scope_a':'a','scope_b':'b'}},
    {'graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}},
    {'bom_rollup':{'root_key':'document:1','product_scope':'product','site':'factory','order_quantity':'3','rounding_scope':'policy'}},
    {'query_plan':{'branches':[{'branch_id':'compare','question':'So sánh hai phạm vi',
                               'scope_comparison':{'scope_a':'a','scope_b':'b'}}]}},
])
def test_runner_classifies_untouched_intent_overflow_as_clarification(store,tmp_path,selection):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    ref=frozen_case(tmp_path,selection=selection,question='Tổng A; tổng B; chênh lệch; xem vật liệu')
    with bind_repository_runtime(db_engine=store):
        observed=run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=current_profile)
    assert observed['actual_outcome']=='clarification_required'
    assert observed['answer_outcome']=='refused' and observed['calculation_count']==0
    assert observed['math_results']==observed['query_branches']==observed['citations']==[]
    assert 'refusal_reason' not in observed and observed['accepted'] is False
