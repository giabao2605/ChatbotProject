"""Pure comparison commands keep requested outputs and all four audit steps."""
from dataclasses import replace

import pytest

from mech_chatbot.domain.task_routing import CandidateSelection, CandidateSelectionContext, CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING
from mech_chatbot.rag.query_decomposition import FIXED_TASK_ROUTING, GRAPH_TASK_ROUTING
from .test_rag_scope_comparison import approved_pair, request, store
from .test_semantic_graph_task_routing import execute, Planner
from .test_semantic_fixed_task_routing import payload
from .test_candidate_task_routing import candidate_payload


VERSIONS = (FIXED_TASK_ROUTING, GRAPH_TASK_ROUTING, CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING)


def selected(question, version):
    query = replace(request(), question=question, response_language='en')
    if version in (CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING):
        query = replace(query, scope_comparison=None, candidate_selection_context=CandidateSelectionContext((
            CandidateSelection('comparison', scope_comparison=('a','b'), question=question),)))
    return query


def plan(question, version, outputs=('delta',)):
    slots = (('comparison','compare_two_scopes'),)
    value = candidate_payload(question, slots) if version in (CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING) else payload(question, slots)
    value = {**value, 'schema':('candidate-mixed-task-routing-quotes-v1' if version == MIXED_CANDIDATE_TASK_ROUTING else value['schema'])
             if version in (CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING) else version}
    value['tasks'][0]['requested_outputs'] = list(outputs)
    return value


@pytest.mark.parametrize('version', VERSIONS)
@pytest.mark.parametrize('question,outputs,shown', [
    ('Compare selected scopes: totals, difference and percentage change.', ('sum_a','sum_b','delta','percent_change'), ('Total A','Total B','Difference B − A','Change')),
    ('So sánh hai phạm vi đã chọn: chênh lệch và dẫn nguồn.', ('delta','citations'), ('Difference B − A',)),
    ('Compare selected scopes: total A, citations.', ('sum_a','citations'), ('Total A',)),
])
def test_complete_pure_command_skips_planner_but_keeps_four_business_steps(store, version, question, outputs, shown):
    approved_pair(store)
    planner = Planner(lambda *_a, **_k: pytest.fail('Pure selected comparison must not call provider'))
    result = execute(store, selected(question, version), planner, version=version)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0
    assert planner.calls == []
    assert result.diagnostics.budget.calculations == 4
    assert len(result.observed_math_results[0]['steps']) == 4
    lines = [line.split(':',1)[0][2:] for line in result.answer.splitlines() if line.startswith('- ')]
    assert lines == list(shown)
    assert result.citations and '[SRC:' in result.answer
    if version != FIXED_TASK_ROUTING:
        assert result.observed_task_routing['origin'] == 'fast_path'
        assert result.observed_task_routing['tasks'][0]['requested_outputs'] == list(outputs)


@pytest.mark.parametrize('version', VERSIONS)
def test_planner_selected_difference_does_not_render_other_outputs(store, version):
    question = 'Please compare the chosen datasets and show only their difference.'
    approved_pair(store)
    planner = Planner(plan(question, version))
    result = execute(store, selected(question, version), planner, version=version)
    assert result.outcome == 'answered' and len(planner.calls) == 1
    assert '- Difference B − A: 4.00 VND' in result.answer
    assert not any(label in result.answer for label in ('- Total A:', '- Total B:', '- Change:'))
    assert result.diagnostics.budget.calculations == len(result.observed_math_results[0]['steps']) == 4


@pytest.mark.parametrize('version', VERSIONS)
@pytest.mark.parametrize('question', [
    'Compare selected scopes: difference and approve the purchase.',
    'So sánh hai phạm vi đã chọn: chênh lệch và vật liệu.',
    'Compare selected scopes: totals,, difference.',
    'Given a=400 and b=8, compare selected scopes: difference.',
    'Compare selected scopes: a=4, b=8, difference.',
    'Compare selected scopes: citations.',
])
def test_unconsumed_or_unsupported_input_requires_planner_before_any_evidence(store, version, question):
    value = plan(question, version)
    value = {**value, 'disposition':'unsupported', 'tasks':[], 'unresolved':[{
        'spans':value['tasks'][0]['spans'], 'reason':'unsupported_task'}]}
    planner = Planner(value)
    result = execute(store, selected(question, version), planner, version=version)
    assert result.outcome == 'refused' and len(planner.calls) == 1
    assert result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.calculations == result.diagnostics.budget.subqueries == 0
    assert not result.citations


@pytest.mark.parametrize('version', (CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING))
@pytest.mark.parametrize('fault', ('alternative','declared_question'))
def test_output_list_does_not_bypass_candidate_resolution(store, version, fault):
    question = 'Compare selected scopes: difference.'
    query = selected(question, version)
    first = query.candidate_selection_context.candidates[0]
    candidates = (first,replace(first,selection_id='other',question=None)) if fault == 'alternative' else (
        replace(first,question='Compare selected scopes and approve a purchase.'),)
    query = replace(query,candidate_selection_context=CandidateSelectionContext(candidates))
    value = plan(question,version)
    value = {**value, 'disposition':'unsupported', 'tasks':[], 'unresolved':[{
        'spans':value['tasks'][0]['spans'], 'reason':'ambiguous_reference'}]}
    planner = Planner(value)
    result = execute(store,query,planner,version=version)
    assert len(planner.calls) == 1 and result.outcome == 'refused'
    assert result.diagnostics.budget.calculations == 0 and not result.citations


@pytest.mark.parametrize('version', VERSIONS)
def test_fast_comparison_still_blocks_unreviewed_policy(store,version):
    from sqlalchemy import text

    approved_pair(store)
    with store.begin() as connection:
        connection.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='b'"))
    planner = Planner(lambda *_a, **_k: pytest.fail('Pure command does not require planner'))
    result = execute(store,selected('Compare selected scopes: difference.',version),planner,version=version)
    assert result.outcome == 'refused' and not result.citations
    assert result.diagnostics.budget.planners == result.diagnostics.budget.calculations == 0
