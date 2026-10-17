"""Versioned Graph routing through the public executor and reviewed SQLite fixtures.

Planner stubs verify the contract and lifecycle, not natural-language quality.
"""
from dataclasses import replace
import json
import threading
from types import SimpleNamespace
import pytest
from sqlalchemy import text

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_rag_bom_chat import bom_store, request, store, connection
from .test_semantic_fixed_task_routing import Planner
from .test_rag_bom_chat import expand_bom
from .test_rag_query_plan import combined_request
from mech_chatbot.domain.graph_query import GraphQuery
from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch

VERSION = 'semantic-graph-fixed-task-routing-v3-draft'


def graph_request(question='Show the selected relations; include their sources and coverage.'):
    selected = request()
    return replace(selected, question=question, graph_query=selected.bom_rollup.graph, bom_rollup=None,
                   response_language='en')


def payload(question, slots=(('graph', 'graph_contains'),)):
    return {'schema': VERSION, 'disposition': 'ready', 'tasks': [
        {'task_id': key, 'selection_id': key, 'template': template,
         'spans': [{'start': 0, 'end': len(question), 'text': question}],
         'requested_outputs': ['relations', 'citations', 'coverage'] if template.startswith('graph_')
         else ['sum_a', 'sum_b', 'delta', 'percent_change'] if template == 'compare_two_scopes'
         else ['totals_by_part']}
        for key, template in slots], 'unresolved': []}


def execute(engine, selected, planner, version=VERSION, **kwargs):
    with bind_repository_runtime(db_engine=engine):
        return collect_rag_events(DefaultRagExecutor(provider_adapter=planner, **kwargs).run(selected,
            RagInvocation('semantic-graph-fixture', mode='evaluation', capture_math_observations=True,
                          task_routing_contract=version)))


def test_selected_contains_projects_relations_sources_and_explicit_coverage(bom_store):
    _, engine = bom_store
    selected = graph_request()
    planner = Planner(payload(selected.question))
    result = execute(engine, selected, planner)
    assert result.outcome == 'answered'
    assert 'part:p' in result.answer and 'source coverage is confirmed complete' in result.answer
    assert len(result.citations) == 1
    assert len(planner.calls) == result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.calculations == 0
    selected_slot = json.loads(planner.calls[0][0][1]['content'])['slots'][0]
    assert selected_slot['selection']['root_key'] == 'document:1'
    assert selected_slot['selection']['direction'] == 'contains'


@pytest.mark.parametrize('question', ['Show selected assembly relations.', 'Xem quan hệ cụm đã chọn?'])
def test_simple_graph_command_avoids_planner(bom_store, question):
    _, engine = bom_store
    planner = Planner(lambda *_a, **_k: pytest.fail('Simple command must skip planner'))
    result = execute(engine, graph_request(question), planner)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0
    assert not planner.calls


@pytest.mark.parametrize('part, expected', [('part:p', 'partial'), ('part:missing', 'unknown')])
def test_incoming_relations_never_claim_corpus_coverage(bom_store, part, expected):
    _, engine = bom_store
    selected = replace(graph_request(), graph_query=GraphQuery(part, 'product', 'factory', 'where_used'))
    planner = Planner(payload(selected.question, (('graph', 'graph_where_used'),)))
    result = execute(engine, selected, planner)
    assert result.diagnostics['graph_v2']['source_status'] == expected
    assert 'source coverage is confirmed complete' not in result.answer
    assert ('Coverage is not confirmed complete.' if expected == 'partial' else 'Source coverage is unknown.') in result.answer
    assert result.diagnostics.budget.planners == 1
    if expected == 'unknown':
        assert result.citations == () and part not in result.answer


@pytest.mark.parametrize('dependent', [False, True])
def test_graph_bom_comparison_share_one_planner_and_keep_branch_sources(bom_store, dependent):
    _, engine = bom_store
    selected = combined_request()
    graph, bom = selected.query_plan.branches
    selected = replace(selected, query_plan=QueryPlan((graph, replace(bom, depends_on=('relations',) if dependent else ()),
        QueryBranch('compare', 'Compare both selected quantity scopes.', scope_comparison=('quantity-scope', 'policy')))))
    slots = (('relations', 'graph_contains'), ('total', 'bom_rollup'), ('compare', 'compare_two_scopes'))
    planner = Planner(payload(selected.question, slots))
    result = execute(engine, selected, planner)
    branches = result.diagnostics['query_v2']['branches']
    assert [b['branch_id'] for b in branches] == ['relations', 'total', 'compare']
    assert all(b['status'] == 'complete' and b['sources'] for b in branches)
    assert result.diagnostics.budget.planners == len(planner.calls) == 1
    assert result.diagnostics.budget.subqueries == 3 and result.diagnostics.budget.calculations == 5
    assert [m['branch_id'] for m in result.observed_math_results] == ['total', 'compare']
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['slots'][1]['depends_on'] == (['relations'] if dependent else [])
    assert [s['question'] for s in prompt['slots']] == [b.question for b in selected.query_plan.branches]


@pytest.mark.parametrize('missing_child', [False, True])
def test_source_completeness_controls_dependency_not_50_edge_presentation(bom_store, missing_child):
    store, engine = bom_store
    expand_bom(store)
    if missing_child:
        with store.begin() as conn:
            conn.execute(text('UPDATE ReviewedGraphOccurrences SET IsLeaf=0'))
    selected = combined_request()
    graph, bom = selected.query_plan.branches
    selected = replace(selected, query_plan=QueryPlan((graph, replace(bom, depends_on=('relations',)))))
    planner = Planner(payload(selected.question, (('relations', 'graph_contains'), ('total', 'bom_rollup'))))
    result = execute(engine, selected, planner)
    branches = result.diagnostics['query_v2']['branches']
    assert branches[0]['status'] == 'partial'
    assert branches[0]['data_complete'] is (not missing_child)
    assert branches[1]['status'] == ('blocked' if missing_child else 'complete')
    assert branches[1]['executed'] is (not missing_child)
    assert result.diagnostics.budget.calculations == (0 if missing_child else 1)
    assert result.diagnostics.budget.graph_edges == 50
    assert result.diagnostics.budget.graph_examined_relations == 51
    if not missing_child:
        assert branches[1]['math']['graph_reused_from'] == 'relations'
        assert '306.00' in branches[1]['answer']
        assert '50/51' in branches[0]['answer']


@pytest.mark.parametrize('version', [None, 'semantic-fixed-task-routing-v2-draft'])
def test_old_contracts_do_not_inherit_new_graph_semantics(bom_store, version):
    _, engine = bom_store
    selected = graph_request('1. A\n2. B\n3. C\n4. D')
    planner = Planner(lambda *_a, **_k: pytest.fail('Old contracts must not call planner for Graph'))
    result = execute(engine, selected, planner, version)
    assert result.refusal_reason == ('intent_overflow' if version is None else 'task_unsupported')
    assert result.diagnostics.budget.graph_examined_relations == 0
    assert not planner.calls and result.citations == ()


@pytest.mark.parametrize('mutation', ['schema', 'direction', 'output', 'selection', 'operand', 'dropped'])
def test_invalid_planner_output_refuses_before_graph_reads(bom_store, mutation):
    _, engine = bom_store
    selected = graph_request()
    response = payload(selected.question)
    task = response['tasks'][0]
    if mutation == 'schema':
        response['schema'] = 'semantic-fixed-task-routing-v2-draft'
    elif mutation == 'direction':
        task['template'] = 'graph_where_used'
    elif mutation == 'output':
        task['requested_outputs'] = ['materials']
    elif mutation == 'selection':
        task['selection_id'] = 'invented'
    elif mutation == 'operand':
        task['root_key'] = 'document:99'
    else:
        response['tasks'] = []
    result = execute(engine, selected, Planner(response))
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.citations == () and result.diagnostics.budget.graph_examined_relations == 0
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('change', ['root', 'scope', 'site', 'direction', 'relations', 'context', 'question', 'language', 'version', 'slot'])
def test_compiled_receipt_binds_graph_parameters_and_contract(bom_store, change):
    from mech_chatbot.rag.pipeline import execute_pipeline
    from mech_chatbot.rag.query_decomposition import fixed_task_guard
    _, engine = bom_store
    selected = graph_request()
    def altered(state):
        assert fixed_task_guard(state) is None
        graph_changes = {'root': {'root_key': 'document:2'}, 'scope': {'product_scope': 'other'}, 'site': {'site': 'other'},
                         'direction': {'root_key': 'part:p', 'direction': 'where_used'},
                         'relations': {'max_relations': 10}, 'context': {'max_generation_edges': 10}}
        if change in graph_changes:
            state.request = replace(state.request, graph_query=replace(state.request.graph_query, **graph_changes[change]))
        elif change == 'question':
            state.request = replace(state.request, question='Also get materials.')
        elif change == 'language':
            state.request = replace(state.request, response_language='vi')
        elif change == 'version':
            state.invocation = replace(state.invocation, task_routing_contract='semantic-fixed-task-routing-v2-draft')
        else:
            state.task_slot = 'other'
        return execute_pipeline(state)
    planner = Planner(payload(selected.question))
    result = execute(engine, selected, planner, execute_pipeline=altered)
    assert result.refusal_reason == 'task_binding_mismatch'
    assert len(planner.calls) == 1 and result.citations == ()
    assert result.diagnostics.budget.graph_examined_relations == 0


@pytest.mark.parametrize('simple', [False, True])
def test_overflow_never_runs_graph(bom_store, simple):
    _, engine = bom_store
    question = ('1. Show selected assembly relations [a]\n2. Show selected part parents [b]\n'
                '3. Calculate selected BOM [c]\n4. Compare selected scopes [d]') if simple else 'Four independent selected actions, please.'
    selected = graph_request(question)
    response = payload(question, (('a', 'graph_contains'), ('b', 'graph_where_used'), ('c', 'bom_rollup'), ('d', 'compare_two_scopes')))
    response['disposition'] = 'overflow'
    planner = Planner(response)
    result = execute(engine, selected, planner)
    assert result.refusal_reason == 'intent_overflow'
    assert result.diagnostics.budget.planners == (0 if simple else 1)
    assert result.diagnostics.budget.graph_examined_relations == 0 and result.citations == ()


@pytest.mark.parametrize('change', ['withdrawn', 'department', 'site'])
def test_coverage_does_not_disclose_inaccessible_source_identity(bom_store, change):
    store, engine = bom_store
    selected = graph_request()
    if change == 'withdrawn':
        with store.begin() as conn:
            conn.execute(text("UPDATE TaiLieu SET Servable=0 WHERE DocID=1"))
    else:
        access = replace(selected.access, **({'allowed_departments': frozenset({'other'})} if change == 'department'
                                            else {'allowed_sites': frozenset({'other'})}))
        selected = replace(selected, access=access)
    result = execute(engine, selected, Planner(payload(selected.question)))
    assert result.outcome == 'refused' and result.citations == ()
    assert 'Source coverage is unknown.' in result.answer
    assert 'document:1' not in result.answer and 'part:p' not in result.answer and 'fixture-bom.pdf' not in result.answer
    assert result.diagnostics['graph_v2']['edges'] == ()


@pytest.mark.parametrize('question', ['Show selected assembly relations and list materials.', 'Show selected part parents.'])
def test_tail_or_direction_mismatch_cannot_use_simple_contains_fast_path(bom_store, question):
    _, engine = bom_store
    selected = graph_request(question)
    planner = Planner({'schema': VERSION, 'disposition': 'unsupported', 'tasks': [], 'unresolved': []})
    result = execute(engine, selected, planner)
    assert result.refusal_reason == 'task_clarification' and len(planner.calls) == 1
    assert result.diagnostics.budget.graph_examined_relations == 0


@pytest.mark.parametrize('question', ['Show selected part parents.', 'Xem cụm cha của chi tiết đã chọn?'])
def test_simple_incoming_command_uses_its_own_direction(bom_store, question):
    _, engine = bom_store
    selected = replace(graph_request(question), graph_query=GraphQuery('part:p', 'product', 'factory', 'where_used'))
    planner = Planner(lambda *_a, **_k: pytest.fail('Simple incoming command must skip planner'))
    result = execute(engine, selected, planner)
    assert result.diagnostics['graph_v2']['source_status'] == 'partial'
    assert result.diagnostics.budget.planners == 0 and not planner.calls


def test_child_only_unsupported_tail_reaches_single_parent_planner(bom_store):
    _, engine = bom_store
    selected = combined_request()
    graph, bom = selected.query_plan.branches
    tail = graph.question + ' Also list materials and render the 3D assembly.'
    selected = replace(selected, query_plan=QueryPlan((replace(graph, question=tail), bom)))
    planner = Planner({'schema': VERSION, 'disposition': 'unsupported', 'tasks': [], 'unresolved': []})
    result = execute(engine, selected, planner)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['slots'][0]['question'] == tail
    assert result.refusal_reason == 'task_clarification'
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.graph_examined_relations == 0


@pytest.mark.parametrize('stage', ['before', 'during', 'deadline'])
def test_cancellation_and_deadline_stop_graph_or_later_branches(bom_store, stage):
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    _, engine = bom_store
    selected = combined_request()
    cancel = threading.Event()
    if stage == 'before':
        cancel.set()
    def classify(_messages, **kwargs):
        if stage == 'during':
            cancel.set()
        if stage == 'deadline':
            kwargs['retry_counter'].started_monotonic -= 1000
        return SimpleNamespace(content=json.dumps(payload(selected.question, (('relations', 'graph_contains'), ('total', 'bom_rollup')))))
    planner = Planner(classify)
    with bind_repository_runtime(db_engine=engine), pytest.raises(TimeoutError if stage == 'deadline' else ExternalAICallCancelled):
        collect_rag_events(DefaultRagExecutor(provider_adapter=planner).run(selected,
            RagInvocation('cancel-graph-fixture', mode='test', task_routing_contract=VERSION), cancel))
    assert len(planner.calls) == (0 if stage == 'before' else 1)


def test_cancellation_during_actual_graph_read_prevents_later_bom(bom_store):
    from sqlalchemy import event
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    store, engine = bom_store
    selected = combined_request()
    cancel = threading.Event()
    reads = []
    def after_read(_conn, _cursor, statement, _params, _context, _many):
        reads.append(statement)
        if 'ReviewedGraphOccurrences' in statement:
            cancel.set()
    planner = Planner(payload(selected.question, (('relations', 'graph_contains'), ('total', 'bom_rollup'))))
    event.listen(store, 'after_cursor_execute', after_read)
    try:
        with bind_repository_runtime(db_engine=engine), pytest.raises(ExternalAICallCancelled):
            collect_rag_events(DefaultRagExecutor(provider_adapter=planner).run(selected,
                RagInvocation('cancel-graph-read', mode='test', task_routing_contract=VERSION), cancel))
    finally:
        event.remove(store, 'after_cursor_execute', after_read)
    assert cancel.is_set() and len(planner.calls) == 1
    assert any('ReviewedGraphOccurrences' in sql for sql in reads)
    assert planner.calls[0][1]['retry_counter'].calculations == 0


@pytest.mark.parametrize('mode', ['production', 'pilot_replay'])
def test_new_contract_cannot_be_activated_by_runtime_invocation(mode):
    with pytest.raises(ValueError, match='exact draft contract'):
        RagInvocation('no-activation', mode=mode, task_routing_contract=VERSION)
