"""Explicit candidate runner and bounded codecs through public seams."""
from dataclasses import replace
import json
import threading

import pytest

from scripts.eval.run_fixed_math_v2 import run_fixed_case
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor, RequestBudgetLimits, RagCompleted
from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING
from mech_chatbot.api.evidence_contracts import GraphQueryRequest, BomRollupRequest, QueryPlanRequest
from .test_fixed_math_runner_v2 import frozen_case, current_profile, BINDING, ACTOR
from .test_rag_scope_comparison import approved_pair, store
from .test_rag_bom_chat import bom_store, connection, expand_bom
from .test_semantic_graph_task_routing import payload, Planner
from .test_bound_adjudication_v2 import artifact, bound_case
from .test_rag_server_endpoints import rag_client, SERVICE_HEADERS


CASE_SCHEMA = 'fixed-task-routing-case-v3-draft'
OBSERVATION_SCHEMA = 'fixed-task-routing-case-observation-v3-draft'
GRAPH = {'root_key': 'document:1', 'product_scope': 'product', 'site': 'factory'}


def candidate_case(root, **changes):
    return frozen_case(root, **{'schema':CASE_SCHEMA, 'task_routing_contract':GRAPH_TASK_ROUTING,
        'response_language':'en', 'question':'Compare the two selected scopes with their sources.', **changes})


def run_candidate(ref, root, engine, planner, **kwargs):
    with bind_repository_runtime(db_engine=engine):
        return run_fixed_case(ref, root=root, run_binding=BINDING, profile_loader=current_profile,
            executor=DefaultRagExecutor(provider_adapter=planner), task_routing_contract=GRAPH_TASK_ROUTING, **kwargs)


def test_candidate_runner_uses_real_planner_and_exports_bound_observation(store, tmp_path):
    approved_pair(store)
    ref = candidate_case(tmp_path)
    question = json.loads((tmp_path / ref['path']).read_text())['question']
    planner = Planner(payload(question, (('comparison', 'compare_two_scopes'),)))
    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
            executor=DefaultRagExecutor(provider_adapter=planner), task_routing_contract=GRAPH_TASK_ROUTING)
    assert observed['schema'] == OBSERVATION_SCHEMA
    assert observed['task_routing']['planner_count'] == len(planner.calls) == 1
    assert observed['response_language'] == 'en' and observed['calculation_count'] == 4


@pytest.mark.parametrize('language,question', [('en', 'Compare the selected scopes and cite their original sources.'),
    ('vi', 'Đối chiếu hai phạm vi đã chọn và dẫn nguồn số liệu.')])
def test_candidate_language_contract_and_pending_bindings_reach_planner(store, tmp_path, language, question):
    approved_pair(store)
    ref = candidate_case(tmp_path, response_language=language, question=question)
    planner = Planner(payload(question, (('comparison', 'compare_two_scopes'),)))
    observed = run_candidate(ref, tmp_path, store, planner)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['schema'] == GRAPH_TASK_ROUTING and prompt['language'] == language
    assert prompt['question'] == question
    assert observed['task_routing_contract'] == observed['task_routing']['contract'] == GRAPH_TASK_ROUTING
    assert observed['response_language'] == language and observed['case_sha256'] == ref['sha256']
    assert all(observed[k] == v for k, v in BINDING.items())
    assert observed['actor'] == ACTOR and observed['binding_verification'] == 'pending'
    assert observed['adjudication_reference'] is None and not observed['accepted'] and observed['offline_only']


@pytest.mark.parametrize('contract', ['wrong', 'semantic-fixed-task-routing-v2-draft', '', True, [], {}])
def test_invalid_explicit_contract_rejected_before_artifact_or_actor_reads(tmp_path, contract):
    def forbidden(*_a, **_k):
        pytest.fail('Actor must not be read')
    with pytest.raises(ValueError, match='explicit V3'):
        run_fixed_case({'invalid_reference': True}, root=tmp_path, run_binding=BINDING,
                       profile_loader=forbidden, task_routing_contract=contract)


@pytest.mark.parametrize('fault', ['hash', 'legacy_case', 'candidate_default', 'schema', 'extra', 'missing_contract',
    'missing_language', 'wrong_contract', 'bool_contract', 'list_contract', 'language_upper', 'language_space',
    'language_unknown', 'language_bool', 'language_list', 'language_null', 'selection', 'bounds'])
def test_candidate_bad_case_fails_before_actor_or_provider(store, tmp_path, fault):
    ref = candidate_case(tmp_path)
    case = json.loads((tmp_path / ref['path']).read_text())
    contract = GRAPH_TASK_ROUTING
    if fault == 'hash':
        ref = {**ref, 'sha256':'0' * 64}
    elif fault == 'legacy_case':
        ref = frozen_case(tmp_path)
    elif fault == 'candidate_default':
        contract = None
    else:
        if fault.startswith('missing_'):
            case.pop('task_routing_contract' if fault == 'missing_contract' else 'response_language')
        elif fault == 'selection':
            case['selection'] = {'unsupported':{}}
        elif fault == 'bounds':
            case['selection'] = {'graph_query':{**GRAPH, 'max_relations':501}}
        elif fault.startswith('language_'):
            case['response_language'] = {'language_upper':'EN', 'language_space':' en ', 'language_unknown':'fr',
                'language_bool':True, 'language_list':['en'], 'language_null':None}[fault]
        else:
            field, value = {'schema':('schema','wrong'), 'extra':('extra',True),
                'wrong_contract':('task_routing_contract','semantic-fixed-task-routing-v2-draft'),
                'bool_contract':('task_routing_contract',True), 'list_contract':('task_routing_contract',[])}[fault]
            case[field] = value
        ref = artifact(tmp_path, 'invalid-candidate', case)
    def forbidden(*_a, **_k):
        pytest.fail('Malformed case must not read actor')
    with bind_repository_runtime(db_engine=store), pytest.raises(ValueError):
        run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=forbidden,
                       executor=DefaultRagExecutor(provider_adapter=Planner(forbidden)), task_routing_contract=contract)


@pytest.mark.parametrize('disposition', ['overflow', 'clarify', 'unsupported', 'invalid'])
def test_candidate_nonready_observations_have_one_planner_and_no_actions(store, tmp_path, disposition):
    question = 'Review the selected assemblies and their use, with BOM totals and a scope comparison.'
    ref = candidate_case(tmp_path, question=question)
    slots = (('a','graph_contains'), ('b','graph_where_used'), ('c','bom_rollup'), ('d','compare_two_scopes'))
    response = payload(question, slots if disposition == 'overflow' else ())
    response['disposition'] = disposition
    planner = Planner('private invalid JSON' if disposition == 'invalid' else response)
    observed = run_candidate(ref, tmp_path, store, planner)
    assert observed['task_routing']['disposition'] == disposition
    assert observed['task_routing']['planner_count'] == len(planner.calls) == 1
    assert observed['calculation_count'] == 0 and observed['math_results'] == observed['citations'] == observed['query_branches'] == []
    assert observed['actual_outcome'] == ('clarification_required' if disposition == 'overflow' else 'insufficient_evidence')
    assert 'private' not in json.dumps(observed)


@pytest.mark.parametrize('fault', ['missing', 'malformed', 'wrong_contract'])
def test_injected_executor_cannot_return_legacy_or_invalid_candidate_observation(store, tmp_path, fault):
    approved_pair(store)
    ref = candidate_case(tmp_path, question='Compare selected scopes.')
    class AlteredExecutor:
        def run(self, request, invocation, **kwargs):
            invocation = replace(invocation, task_routing_contract=None) if fault == 'missing' else invocation
            for event in DefaultRagExecutor().run(request, invocation, **kwargs):
                if isinstance(event, RagCompleted) and fault != 'missing':
                    routing = {**event.observed_task_routing, **({'extra':True} if fault == 'malformed'
                        else {'contract':'semantic-fixed-task-routing-v2-draft'})}
                    event = replace(event, observed_task_routing=routing)
                yield event
    with bind_repository_runtime(db_engine=store), pytest.raises(ValueError):
        run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
                       executor=AlteredExecutor(), task_routing_contract=GRAPH_TASK_ROUTING)


@pytest.mark.parametrize('failure', ['actor', 'cancelled', 'deadline', 'commit', 'cancel_commit', 'deadline_commit'])
def test_candidate_runner_rejects_actor_or_interrupted_snapshot(store, tmp_path, monkeypatch, failure):
    from scripts.eval import run_fixed_math_v2 as runner
    from sqlalchemy import event
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    approved_pair(store)
    ref = candidate_case(tmp_path, question='Compare selected scopes.')
    cancel = threading.Event()
    if failure == 'cancelled':
        cancel.set()
    clock = [runner.monotonic()]
    monkeypatch.setattr(runner, 'monotonic', lambda:clock[0])
    def on_commit(_conn):
        if failure == 'commit':
            raise RuntimeError('Snapshot failed')
        if failure == 'cancel_commit':
            cancel.set()
        if failure == 'deadline_commit':
            clock[0] += 1000
    def loader(*args, **kwargs):
        profile = current_profile(*args, **kwargs)
        return {**profile, 'roles':['viewer']} if failure == 'actor' else profile
    commit_failure = failure in ('commit', 'cancel_commit', 'deadline_commit')
    if commit_failure:
        event.listen(store, 'commit', on_commit)
    expected = (PermissionError if failure == 'actor' else ExternalAICallCancelled if failure.startswith('cancel')
                else TimeoutError if failure.startswith('deadline') else RuntimeError)
    try:
        with bind_repository_runtime(db_engine=store), pytest.raises(expected):
            run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=loader,
                cancellation=cancel, budget_limits=RequestBudgetLimits(deadline_seconds=0 if failure == 'deadline' else 120),
                task_routing_contract=GRAPH_TASK_ROUTING)
    finally:
        if commit_failure:
            event.remove(store, 'commit', on_commit)


@pytest.mark.parametrize('bounds', [{}, {'max_relations':120,'max_generation_edges':25},
    {'max_relations':500,'max_generation_edges':50}, {'max_relations':0,'max_generation_edges':0}])
def test_graph_codec_retains_defaults_exact_bounds_and_zero(bounds):
    parsed = GraphQueryRequest.model_validate({**GRAPH, **bounds})
    query = parsed.to_query()
    assert query.max_relations == bounds.get('max_relations',500)
    assert query.max_generation_edges == bounds.get('max_generation_edges',50)
    assert GraphQueryRequest.model_validate(parsed.model_dump()).to_query() == query


@pytest.mark.parametrize('field,invalid', [('max_relations',501), ('max_generation_edges',51),
    *[(field,value) for field in ('max_relations','max_generation_edges')
      for value in (True,False,1.5,'25',None,-1,[],{})]])
def test_graph_bound_codec_rejects_coercion_or_over_cap(field, invalid):
    with pytest.raises(ValueError):
        GraphQueryRequest.model_validate({**GRAPH,field:invalid})


def test_bom_and_nested_query_codecs_preserve_graph_bounds_and_dependencies():
    bom = {**GRAPH,'max_relations':120,'max_generation_edges':25,'order_quantity':'3','rounding_scope':'policy'}
    parsed = BomRollupRequest.model_validate(bom)
    assert (parsed.to_rollup().graph.max_relations,parsed.to_rollup().graph.max_generation_edges) == (120,25)
    plan = QueryPlanRequest.model_validate({'branches':[
        {'branch_id':'graph','question':'Contains what?','graph_query':{**GRAPH,'max_relations':0,'max_generation_edges':0}},
        {'branch_id':'bom','question':'Total BOM?','bom_rollup':bom,'depends_on':['graph']}]})
    typed = plan.to_plan()
    assert typed.branches[0].graph_query.max_relations == typed.branches[0].graph_query.max_generation_edges == 0
    assert typed.branches[1].bom_rollup.graph == parsed.to_query()
    assert typed.branches[1].depends_on == ('graph',)


@pytest.mark.parametrize('kind', ['graph_query','bom_rollup','query_plan'])
def test_candidate_runner_preserves_nondefault_bounds_in_planner_and_execution(bom_store, tmp_path, kind):
    db, engine = bom_store
    expand_bom(db)
    graph = {**GRAPH,'max_relations':120,'max_generation_edges':25}
    bom = {**graph,'order_quantity':'3','rounding_scope':'policy'}
    plan = {'branches':[{'branch_id':'graph','question':'Show assembly relationships.','graph_query':graph},
        {'branch_id':'bom','question':'Roll up selected BOM.','bom_rollup':bom,'depends_on':['graph']}]}
    selection = {kind:{'graph_query':graph,'bom_rollup':bom,'query_plan':plan}[kind]}
    question = 'Review the selected assembly relationships and the requested totals with sources.'
    ref = candidate_case(tmp_path, selection=selection, question=question,
                         actor={**ACTOR,'allowed_departments':['tech','ENG']})
    slots = (('graph','graph_contains'),('bom','bom_rollup')) if kind == 'query_plan' else (
        ('graph','graph_contains') if kind == 'graph_query' else ('bom','bom_rollup'),)
    planner = Planner(payload(question,slots))
    def loader(*args,**kwargs):
        return {**current_profile(*args,**kwargs),'allowed_departments':['tech','ENG']}
    with bind_repository_runtime(db_engine=engine):
        observed = run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=loader,
            executor=DefaultRagExecutor(provider_adapter=planner),task_routing_contract=GRAPH_TASK_ROUTING)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    for slot in prompt['slots']:
        selected = slot['selection'].get('graph',slot['selection'])
        assert selected['max_relations'] == 120 and selected['max_generation_edges'] == 25
    if kind != 'bom_rollup':
        assert '25/51' in observed['answer']
    if kind != 'graph_query':
        assert observed['calculation_count'] == 1 and observed['math_results'][0]['steps'][0]['value'] == '306'
    if kind == 'query_plan':
        assert [t['selection_id'] for t in observed['task_routing']['tasks']] == ['graph','bom']
        assert observed['task_routing']['tasks'][1]['depends_on'] == ['graph']


@pytest.mark.parametrize('target', ['both','case','observation'])
def test_existing_v2_adjudicator_rejects_candidate_and_mixed_pairs(bound_case,tmp_path,target):
    from mech_chatbot.evaluation.adjudication import resolve_observation_reviews
    case_ref,obs_ref,reviews = bound_case
    if target in ('both','case'):
        case = json.loads((tmp_path/case_ref['path']).read_text())
        case.update(schema=CASE_SCHEMA,task_routing_contract=GRAPH_TASK_ROUTING,response_language='en')
        case_ref = artifact(tmp_path,'candidate-case',case)
    if target in ('both','observation'):
        observed = json.loads((tmp_path/obs_ref['path']).read_text())
        observed.update(schema=OBSERVATION_SCHEMA,task_routing_contract=GRAPH_TASK_ROUTING,response_language='en')
        obs_ref = artifact(tmp_path,'candidate-observation',observed)
    reviews.update(case_sha256=case_ref['sha256'],observation_sha256=obs_ref['sha256'])
    with pytest.raises(ValueError):
        resolve_observation_reviews(case_ref,obs_ref,artifact(tmp_path,'candidate-reviews',reviews),root=tmp_path)


@pytest.mark.parametrize('path', ['/chat','/chat/stream'])
@pytest.mark.parametrize('replay', [False,True])
def test_new_bounds_do_not_bypass_production_or_replay_guard(rag_client,path,replay):
    headers = {**SERVICE_HEADERS, **({'X-RAG-Pilot-Replay':'true'} if replay else {})}
    response = rag_client.post(path,headers=headers,json={'user_question':'Show assembly relations',
        'graph_query':{**GRAPH,'max_relations':120,'max_generation_edges':25}})
    assert response.status_code == 409


@pytest.mark.parametrize('examined,displayed', [(0,0),(1,1),(120,0)])
def test_lower_graph_bounds_actually_limit_traversal_and_context(bom_store,tmp_path,examined,displayed):
    db,engine = bom_store
    expand_bom(db)
    question = 'Describe the selected assembly relations and coverage.'
    ref = candidate_case(tmp_path,question=question,actor={**ACTOR,'allowed_departments':['tech','ENG']},
        selection={'graph_query':{**GRAPH,'max_relations':examined,'max_generation_edges':displayed}})
    planner = Planner(payload(question))
    completed = []
    class ObservingExecutor:
        def run(self,*args,**kwargs):
            for event in DefaultRagExecutor(provider_adapter=planner).run(*args,**kwargs):
                if isinstance(event,RagCompleted):
                    completed.append(event)
                yield event
    def loader(*args,**kwargs):
        return {**current_profile(*args,**kwargs),'allowed_departments':['tech','ENG']}
    with bind_repository_runtime(db_engine=engine):
        observed = run_fixed_case(ref,root=tmp_path,run_binding=BINDING,profile_loader=loader,
            executor=ObservingExecutor(),task_routing_contract=GRAPH_TASK_ROUTING)
    diagnostics = completed[0].diagnostics
    assert diagnostics.budget.graph_examined_relations <= examined
    assert diagnostics.budget.graph_edges <= displayed
    assert len(diagnostics['graph_v2']['edges']) <= displayed
    assert observed['task_routing']['planner_count'] == 1 and observed['calculation_count'] == 0
    assert diagnostics['graph_v2']['status'] != 'complete'
