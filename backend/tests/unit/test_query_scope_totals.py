from dataclasses import replace

import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan, query_plan_snapshot
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_rag_scope_comparison import approved_pair, request
from .test_structured_evidence_repository import store


def test_legacy_query_snapshot_does_not_gain_new_null_keys():
    plan = QueryPlan((QueryBranch('comparison', 'Compare', scope_comparison=('a', 'b')),))
    assert query_plan_snapshot(plan) == {'branches': ({'branch_id': 'comparison', 'question': 'Compare',
        'scope_comparison': ('a', 'b'), 'graph_query': None, 'bom_rollup': None,
        'depends_on': (), 'text_query': False},)}


def test_three_selected_actions_keep_distinct_sums_sources_and_six_real_steps(store):
    approved_pair(store)
    plan = QueryPlan((QueryBranch('total_a', 'Total A', scope_total='a'),
                      QueryBranch('total_b', 'Total B', scope_total='b'),
                      QueryBranch('comparison', 'Compare', scope_comparison=('a', 'b'))))
    chosen = replace(request(), scope_comparison=None, query_plan=plan)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(chosen,
            RagInvocation('query-totals', mode='evaluation', capture_math_observations=True)))
    query = result.diagnostics['query_v2']
    assert query['schema'] == query['contract'] == 'query-plan-scope-total-v1' and query['status'] == 'complete'
    assert [branch['template'] for branch in query['branches']] == ['sum_reviewed_scope', 'sum_reviewed_scope', 'compare_two_scopes']
    assert [record['branch_id'] for record in result.observed_math_results] == ['total_a', 'total_b', 'comparison']
    assert [[step['display_value'] for step in record['steps']] for record in result.observed_math_results] == [
        ['4.00'], ['8.00'], ['4.00', '8.00', '4.00', '100.00']]
    assert [{card['scope_id'] for card in branch['sources']} for branch in query['branches']] == [{'a'}, {'b'}, {'a', 'b'}]
    assert result.diagnostics.budget.subqueries == 3 and result.diagnostics.budget.calculations == 6
    assert result.diagnostics.budget.planners == result.diagnostics.budget.final_generations == 0


def test_missing_total_does_not_block_an_independent_total_but_blocks_its_dependency(store):
    approved_pair(store)
    plan = QueryPlan((QueryBranch('missing', 'Unavailable total', scope_total='missing'),
                      QueryBranch('independent', 'Permitted total', scope_total='a'),
                      QueryBranch('dependent', 'Dependent total', scope_total='b', depends_on=('missing',))))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(replace(request(), scope_comparison=None, query_plan=plan),
            RagInvocation('partial-totals', mode='evaluation', capture_math_observations=True)))
    query = result.diagnostics['query_v2']
    assert query['status'] == 'partial'
    assert [branch['status'] for branch in query['branches']] == ['unknown', 'complete', 'blocked']
    assert [branch['executed'] for branch in query['branches']] == [True, True, False]
    assert query['branches'][0]['sources'] == query['branches'][2]['sources'] == []
    assert result.diagnostics.budget.calculations == 1 and result.diagnostics.budget.subqueries == 2
    assert {card['scope_id'] for card in result.citations} == {'a'}


def test_scope_total_plan_never_invokes_a_planner():
    plan = QueryPlan((QueryBranch('total', 'Total', scope_total='a'),))
    with pytest.raises(ValueError, match='Scope total does not use a planner'):
        collect_rag_events(DefaultRagExecutor().run(replace(request(), scope_comparison=None, query_plan=plan),
            RagInvocation('no-plan-for-totals', mode='evaluation', task_routing_contract='semantic-fixed-task-routing-v2-draft')))


@pytest.mark.parametrize('scope_id', ['', ' ', 'x'*256, False])
def test_total_branch_requires_one_bounded_scope(scope_id):
    with pytest.raises(ValueError): QueryBranch('total', 'Total', scope_total=scope_id)


def test_total_branch_cannot_hide_another_action():
    with pytest.raises(ValueError): QueryBranch('total', 'Total', scope_total='a', scope_comparison=('a', 'b'))
