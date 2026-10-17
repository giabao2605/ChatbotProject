"""Server coordinate context at the public planner seam, without model calls."""
import json
from dataclasses import replace

import pytest

from .test_semantic_graph_task_routing import bom_store, store, connection, execute, graph_request, payload, Planner


def test_v3_supplies_original_unicode_code_point_bounds(bom_store):
    _, engine = bom_store
    question = 'A\u0301\U00020000: Cụm?'
    planner = Planner(payload(question))
    result = execute(engine, graph_request(question), planner)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['question_bounds'] == {'start': 0, 'end': 9}
    assert prompt['question'] == question
    assert result.outcome == 'answered'
    assert result.observed_task_routing_failure_code is None
    assert result.diagnostics.budget.planners == 1


@pytest.mark.parametrize('question,end', [('English?', 8), ('Cụm?', 4),
    ('A\u0301?', 3), ('\U00020000?', 2), ('A\r\nB?', 5)])
def test_bounds_preserve_original_text_without_normalization(bom_store, question, end):
    _, engine = bom_store
    planner = Planner(payload(question))
    result = execute(engine, graph_request(question), planner)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['question_bounds'] == {'start': 0, 'end': end}
    assert prompt['question'] == question
    assert result.outcome == 'answered'


def test_parent_bounds_keep_two_independent_branches_and_child_questions(bom_store):
    from .test_rag_query_plan import combined_request
    _, engine = bom_store
    selected = replace(combined_request(), question='A và B; C?')
    planner = Planner(payload(selected.question, (('relations', 'graph_contains'), ('total', 'bom_rollup'))))
    result = execute(engine, selected, planner)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['question_bounds'] == {'start': 0, 'end': 10}
    assert [slot['question'] for slot in prompt['slots']] == ['Cụm chứa gì?', 'Tính BOM cho 3 cụm']
    assert [branch['branch_id'] for branch in result.diagnostics['query_v2']['branches']] == ['relations', 'total']
    assert result.diagnostics.budget.planners == len(planner.calls) == 1
    assert result.diagnostics.budget.subqueries == 2


def test_partial_quote_keeps_its_own_exact_coordinates(bom_store):
    _, engine = bom_store
    question = 'A\u0301\U00020000: Cụm?'
    value = payload(question)
    value['tasks'][0]['spans'] = [{'start': 5, 'end': 8, 'text': 'Cụm'}]
    planner = Planner(value)
    result = execute(engine, graph_request(question), planner)
    assert result.outcome == 'answered' and result.observed_task_routing_failure_code is None
    instruction = planner.calls[0][0][0]['content']
    assert 'reuse those bounds only when the chosen quote is the entire question' in instruction
    assert 'For partial quotes, use their own exact positions' in instruction
    assert 'Bounds do not establish task meaning or justify combining independent tasks' in instruction


@pytest.mark.parametrize('fault,code', [('type', 'span_bounds'), ('outside', 'span_bounds'), ('text', 'span_text')])
def test_supplied_bounds_do_not_repair_or_accept_invalid_model_spans(bom_store, fault, code):
    _, engine = bom_store
    question = 'A\u0301\U00020000: Cụm?'
    value = payload(question)
    span = value['tasks'][0]['spans'][0]
    span.update({'start': True} if fault == 'type' else {'end': 10} if fault == 'outside' else {'text': 'Cụm?'})
    planner = Planner(value)
    result = execute(engine, graph_request(question), planner)
    assert json.loads(planner.calls[0][0][1]['content'])['question_bounds'] == {'start': 0, 'end': 9}
    assert result.refusal_reason == 'task_invalid_plan' and result.observed_task_routing_failure_code == code
    assert result.diagnostics.budget.graph_examined_relations == result.diagnostics.budget.calculations == 0
    assert result.citations == ()


def test_legacy_v2_planner_input_keeps_its_existing_fields(store):
    from .test_rag_scope_comparison import approved_pair, request
    from .test_semantic_fixed_task_routing import payload as legacy_payload
    approved_pair(store)
    selected = replace(request(), question='Compare both scopes and their sources.')
    planner = Planner(legacy_payload(selected.question))
    result = execute(store, selected, planner, version='semantic-fixed-task-routing-v2-draft')
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert set(prompt) == {'schema', 'question', 'language', 'slots', 'allowed_outputs'}
    assert 'question_bounds' not in planner.calls[0][0][0]['content']
    assert result.outcome == 'answered'


def test_simple_v3_command_keeps_fast_path_without_planner(bom_store):
    _, engine = bom_store
    planner = Planner(lambda *_a, **_k: pytest.fail('Simple command must skip the planner'))
    result = execute(engine, graph_request('Show selected assembly relations.'), planner)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0 and not planner.calls
