"""Provider quotes bind to exact source text; the server owns character offsets."""
import json
import pytest

from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING
from .test_candidate_task_routing import candidate_request
from .test_semantic_graph_task_routing import bom_store, store, connection, Planner, execute


def quoted_payload(text):
    return {'schema': 'candidate-task-routing-quotes-v1', 'disposition': 'ready', 'tasks': [{
        'task_id': 'task', 'template': 'graph_contains', 'selection_id': 'first',
        'spans': [{'text': text}], 'requested_outputs': ['relations']}], 'unresolved': []}


def test_exact_unicode_quote_executes_without_model_generated_offsets(bom_store):
    _, engine = bom_store
    question = 'Với mã e\u0301, xem cấu trúc cụm đã chọn.'
    quote = 'xem cấu trúc cụm đã chọn'
    result = execute(engine, candidate_request(question), Planner(quoted_payload(quote)),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and result.citations
    assert result.diagnostics['query_v2']['branches'][0]['question'] == quote
    assert result.diagnostics.budget.planners == 1 and result.diagnostics.budget.provider_retries == 0


@pytest.mark.parametrize('question,quote', [
    ('Với mã \U00020000: xem cấu trúc.', 'xem cấu trúc'),
    ('Show  assembly\nrelations.', 'Show  assembly\nrelations'),
    ('Mã e\u0301: xem cụm.', 'Mã e\u0301: xem cụm.'),
])
def test_source_codepoints_and_whitespace_remain_exact(bom_store, question, quote):
    _, engine = bom_store
    result = execute(engine, candidate_request(question), Planner(quoted_payload(quote)),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered'
    assert result.diagnostics['query_v2']['branches'][0]['question'] == quote


@pytest.mark.parametrize('question,quote', [
    ('Show assembly.', 'Show another assembly.'),
    ('show; show', 'show'),
    ('aaaa', 'aa'),
    ('Mã e\u0301: xem cụm.', 'Mã é: xem cụm.'),
    ('Show  assembly.', 'Show assembly.'),
])
def test_absent_ambiguous_or_normalized_quotes_never_execute(bom_store, question, quote, caplog):
    _, engine = bom_store
    result = execute(engine, candidate_request(question), Planner(quoted_payload(quote)),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.observed_task_routing_failure_code == 'span_text'
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0
    assert result.citations == () and quote not in caplog.text


@pytest.mark.parametrize('spans', [[], [{'text': ''}], [{'text': False}], [{'text': 3}],
    [{'text': 'x' * 20001}], [{'text': 'Show', 'start': 0, 'end': 4}],
    [{'quote': 'Show'}], [None], [{'text': 'Show'}] * 9, None])
def test_wire_span_bounds_and_exact_shape_are_enforced(bom_store, spans):
    _, engine = bom_store
    response = quoted_payload('Show')
    response['tasks'][0]['spans'] = spans
    result = execute(engine, candidate_request('Show the assembly.'), Planner(response),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.observed_task_routing_failure_code == 'span_shape'
    assert result.diagnostics.budget.subqueries == 0 and not result.citations


def test_coordinate_wire_cannot_silently_fallback_under_quote_contract(bom_store):
    _, engine = bom_store
    response = {**quoted_payload('Show the assembly.'), 'schema': CANDIDATE_TASK_ROUTING}
    response['tasks'][0]['spans'] = [{'start': 0, 'end': 18, 'text': 'Show the assembly.'}]
    result = execute(engine, candidate_request('Show the assembly.'), Planner(response),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.observed_task_routing_failure_code == 'contract'
    assert result.diagnostics.budget.subqueries == 0 and not result.citations


def test_several_quotes_preserve_the_original_enclosing_question(bom_store):
    _, engine = bom_store
    response = quoted_payload('Show')
    response['tasks'][0]['spans'] = [{'text': 'Show'}, {'text': 'assembly'}]
    result = execute(engine, candidate_request('Please: Show the assembly.'), Planner(response),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered'
    assert result.diagnostics['query_v2']['branches'][0]['question'] == 'Show the assembly'


def test_ambiguous_quote_can_be_disambiguated_with_exact_surrounding_text(bom_store):
    _, engine = bom_store
    response = quoted_payload('the assembly named assembly')
    result = execute(engine, candidate_request('Show the assembly named assembly.'), Planner(response),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered'
    assert result.diagnostics['query_v2']['branches'][0]['question'] == 'the assembly named assembly'


def test_unresolved_quotes_use_the_same_strict_source_binding(bom_store):
    _, engine = bom_store
    question = 'What about that assembly?'
    response = {'schema': 'candidate-task-routing-quotes-v1', 'disposition': 'clarify',
                'tasks': [], 'unresolved': [{'spans': [{'text': 'that assembly'}],
                                            'reason': 'ambiguous_reference'}]}
    result = execute(engine, candidate_request(question), Planner(response), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_clarification' and not result.citations
    assert result.diagnostics.budget.subqueries == 0
    response['unresolved'][0]['spans'][0]['text'] = 'missing phrase'
    rejected = execute(engine, candidate_request(question), Planner(response), version=CANDIDATE_TASK_ROUTING)
    assert rejected.refusal_reason == 'task_invalid_plan' and not rejected.citations


@pytest.mark.parametrize('raw,code', [
    ('{"schema":1,"schema":2}', 'json_duplicate_key'), ('{"x":NaN}', 'json_nonfinite'),
    ('no JSON', 'json_syntax'), ('[]', 'envelope_shape'), ('x' * 65537, 'payload_shape'),
    (json.dumps({'schema': 'candidate-task-routing-quotes-v1', 'disposition': 'ready',
                 'tasks': False, 'unresolved': []}), 'payload_shape'),
], ids=['duplicate', 'nonfinite', 'syntax', 'root', 'oversized', 'tasks-type'])
def test_quote_protocol_retains_bounded_strict_json_validation(bom_store, raw, code):
    _, engine = bom_store
    result = execute(engine, candidate_request(), Planner(raw), version=CANDIDATE_TASK_ROUTING)
    assert result.observed_task_routing_failure_code == code
    assert result.diagnostics.budget.subqueries == 0 and not result.citations
