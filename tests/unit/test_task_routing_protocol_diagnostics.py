"""Bounded decoder diagnostics through the public executor, without model calls."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from .test_semantic_graph_task_routing import (
    execute, graph_request, payload, Planner, bom_store, store, connection,
)


def test_invalid_json_has_bounded_candidate_code_without_provider_text(bom_store):
    _, engine = bom_store
    planner = Planner('private-source-788: malformed response')
    result = execute(engine, graph_request(), planner)
    assert getattr(result, 'observed_task_routing_failure_code', None) == 'json_syntax'
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.diagnostics.budget.graph_examined_relations == 0
    assert 'private-source-788' not in result.answer + repr(dict(result.diagnostics))


def malformed_response(fault, question):
    value = payload(question)
    task = value['tasks'][0]
    raw = json.dumps(value, ensure_ascii=False)
    direct = {'syntax': '```json\n' + raw + '\n```', 'duplicate_key': raw[:-1] + ',"tasks":[]}',
              'nonfinite': raw.replace('"start": 0', '"start": NaN'), 'nonstring': None,
              'oversize': ' ' * 65537, 'envelope': '[]'}
    if fault in direct:
        return direct[fault]
    if fault == 'contract':
        value['schema'] = 'wrong'
    elif fault == 'tasks_bound':
        value['tasks'] *= 9
    elif fault == 'task_shape':
        task['depends_on'] = []
    elif fault == 'reference':
        task['selection_id'] = 'private source 788'
    elif fault == 'outputs':
        task['requested_outputs'] = ['relations', 'relations']
    elif fault == 'span_shape':
        task['spans'] = []
    elif fault == 'span_fields':
        task['spans'][0]['extra'] = True
    elif fault == 'span_bounds':
        task['spans'][0]['start'] = True
    elif fault == 'span_text':
        task['spans'][0]['text'] = 'private-source-788'
    elif fault == 'duplicate_ids':
        value['tasks'] = [task, task]
    elif fault == 'unresolved_shape':
        value['unresolved'] = [{'reason': 'selection_mismatch'}]
    elif fault == 'unresolved_reason':
        value['unresolved'] = [{'spans': task['spans'], 'reason': 'private-source-788'}]
    elif fault == 'slot_order':
        task['selection_id'] = 'different'
    elif fault == 'overflow_count':
        value['disposition'] = 'overflow'
    return value


FAULT_CODES = [('syntax', 'json_syntax'), ('duplicate_key', 'json_duplicate_key'),
    ('nonfinite', 'json_nonfinite'), ('nonstring', 'payload_shape'), ('oversize', 'payload_shape'),
    ('envelope', 'envelope_shape'), ('contract', 'contract'), ('tasks_bound', 'payload_shape'),
    ('task_shape', 'task_shape'), ('reference', 'task_reference'), ('outputs', 'requested_outputs'),
    ('span_shape', 'span_shape'), ('span_fields', 'span_shape'), ('span_bounds', 'span_bounds'),
    ('span_text', 'span_text'), ('duplicate_ids', 'duplicate_task_ids'),
    ('unresolved_shape', 'unresolved_shape'), ('unresolved_reason', 'unresolved_reason'),
    ('slot_order', 'ready_slot_order'), ('overflow_count', 'overflow_count')]


@pytest.mark.parametrize('fault,code', FAULT_CODES)
def test_decoder_categories_are_static_and_stop_actions(bom_store, fault, code):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    selected = graph_request()
    response = malformed_response(fault, selected.question)
    planner = Planner(lambda *_a, **_k: SimpleNamespace(
        content=json.dumps(response, ensure_ascii=False) if isinstance(response, dict) else response))
    result = execute(engine, selected, planner)
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.observed_task_routing_failure_code == code
    assert len(planner.calls) == result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.graph_examined_relations == result.diagnostics.budget.calculations == 0
    assert result.citations == result.observed_math_results == ()
    exported = export_task_routing_diagnostics(result)
    assert exported['failure_code'] == code
    for private in ('private-source-788', selected.question, 'spans', 'document:1', 'quantity'):
        assert private not in json.dumps(exported)
    assert 'failure_code' not in dict(result.diagnostics)


@pytest.mark.parametrize('error_type', [ValueError, TypeError, KeyError, RecursionError])
def test_unknown_validation_fault_never_exports_exception_details(bom_store, error_type):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    def fail(*_a, **_k):
        raise error_type('private-source-788 quantity 997')
    result = execute(engine, graph_request(), Planner(fail))
    assert export_task_routing_diagnostics(result)['failure_code'] == 'unknown_validation'
    assert 'private-source-788' not in result.answer + repr(dict(result.diagnostics))


@pytest.mark.parametrize('code', ['private-source-788', None, ['json_syntax']])
def test_unknown_typed_failure_code_is_bounded_at_public_execution(bom_store, code):
    from mech_chatbot.rag.query_decomposition import TaskRoutingValidationError
    _, engine = bom_store
    def fail(*_a, **_k):
        raise TaskRoutingValidationError(code, 'private-source-788')
    result = execute(engine, graph_request(), Planner(fail))
    assert result.observed_task_routing_failure_code == 'unknown_validation'
    assert 'private-source-788' not in result.answer + repr(dict(result.diagnostics))


def test_v2_decoder_failure_retains_legacy_refusal_without_new_code(store):
    from .test_rag_scope_comparison import request
    selected = replace(request(), question='Compare both selected scopes and their sources.')
    result = execute(store, selected, Planner('private-source-788'),
                     version='semantic-fixed-task-routing-v2-draft')
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.observed_task_routing_failure_code is None
    assert result.observed_task_routing is None


def test_unicode_parent_partial_span_and_v3_protocol_instruction(bom_store):
    _, engine = bom_store
    question = 'Á\U00010400 Quan hệ cụm đã chọn và nguồn.'
    value = payload(question)
    value['tasks'][0]['spans'] = [{'start': 3, 'end': 18, 'text': question[3:18]}]
    value['tasks'][0]['requested_outputs'] = ['relations']
    planner = Planner(value)
    result = execute(engine, graph_request(question), planner)
    assert result.outcome == 'answered'
    assert result.observed_task_routing_failure_code is None
    instruction = planner.calls[0][0][0]['content']
    assert 'exactly four keys: schema, disposition, tasks, unresolved' in instruction
    assert 'exactly five keys' in instruction and 'never include depends_on' in instruction
    assert 'zero-based Unicode code-point indices' in instruction and 'end exclusive' in instruction
    assert 'original intent overrides available slots' in instruction
    assert 'only requested outputs' in instruction and 'without Markdown fences' in instruction
    assert 'Child slot questions are not the span coordinate system' in instruction
    assert 'source coverage' not in result.answer


@pytest.mark.parametrize('version', [None, 'semantic-fixed-task-routing-v2-draft'])
def test_legacy_contracts_do_not_gain_decoder_telemetry_or_new_instruction(store, version):
    from .test_rag_scope_comparison import request
    from .test_semantic_fixed_task_routing import payload as legacy_payload
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    selected = replace(request(), question='Compare both selected scopes and their sources.')
    planner = Planner(legacy_payload(selected.question) if version else 'unused')
    result = execute(store, selected, planner, version=version)
    assert result.observed_task_routing_failure_code is None
    with pytest.raises(ValueError):
        export_task_routing_diagnostics(result)
    if version:
        assert 'exactly five keys' not in planner.calls[0][0][0]['content']
