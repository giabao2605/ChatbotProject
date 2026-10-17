"""Strict V4 receipts at the completed-result export boundary."""
from dataclasses import replace
import json

import pytest

from .test_task_routing_observation import overflow_result, bom_store, connection, store
from .test_semantic_graph_task_routing import graph_request, execute, Planner


VERSION = 'semantic-candidate-task-routing-v4-draft'


def test_v4_decoder_diagnostic_receipt_is_separate_and_redacted(overflow_result):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_diagnostics

    result = replace(overflow_result, observed_task_routing=candidate_observation())
    receipt = export_candidate_task_routing_diagnostics(result)
    assert receipt == {'schema': 'candidate-task-routing-diagnostics-v4-draft', 'trace_id': result.trace_id,
        'task_routing_contract': VERSION, 'origin': 'planner', 'disposition': 'overflow',
        'planner_count': 1, 'failure_code': None, 'accepted': False, 'offline_only': True}
    assert 'tasks' not in receipt and 'candidate_context_sha256' not in receipt


@pytest.mark.parametrize('code', [None, True, {}, 'private response', 'json_syntax\nprivate'])
def test_v4_decoder_receipt_rejects_unbounded_or_missing_failure_code(overflow_result, code):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_diagnostics

    result = replace(overflow_result, refusal_reason='task_invalid_plan',
        observed_task_routing=candidate_observation(disposition='invalid', tasks=[]),
        observed_task_routing_failure_code=code)
    with pytest.raises(ValueError):
        export_candidate_task_routing_diagnostics(result)


def test_v4_ready_receipt_cannot_claim_a_decoder_failure(ready_result):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_diagnostics

    result = replace(ready_result, observed_task_routing_failure_code='json_syntax')
    with pytest.raises(ValueError):
        export_candidate_task_routing_diagnostics(result)


def candidate_observation(**changes):
    return {'schema': 'candidate-task-routing-observation-v4', 'contract': VERSION,
            'origin': 'planner', 'disposition': 'overflow',
            'candidate_context_sha256': 'a' * 64, 'resolved_request_sha256': None,
            'tasks': [{'selection_id': None, 'template': template,
                       'requested_outputs': [output], 'depends_on': []}
                      for template, output in (('graph_contains', 'relations'),
                          ('graph_where_used', 'relations'), ('bom_rollup', 'totals_by_part'),
                          ('compare_two_scopes', 'sum_a'))], **changes}


def test_v4_nonready_exports_without_actions_or_private_context(overflow_result):
    from mech_chatbot.evaluation.grounded_math_v2 import (
        export_candidate_task_routing_observation, export_math_observation,
    )
    result = replace(overflow_result, observed_task_routing=candidate_observation())
    observed = export_candidate_task_routing_observation(result)
    assert observed == {**candidate_observation(), 'planner_count': 1,
                        'accepted': False, 'offline_only': True}
    assert export_math_observation(result)['actual_outcome'] == 'clarification_required'


@pytest.fixture
def ready_result(bom_store):
    _, engine = bom_store
    result = execute(engine, graph_request('Show selected assembly relations.'), Planner('unused'))
    return replace(result, observed_task_routing={
        **candidate_observation(origin='fast_path', disposition='ready', resolved_request_sha256='b' * 64),
        'tasks': [{'selection_id': 'graph', 'template': 'graph_contains',
                   'requested_outputs': ['relations'], 'depends_on': []}]})


def test_v4_ready_export_is_separate_copied_and_source_redacted(ready_result):
    from mech_chatbot.evaluation.grounded_math_v2 import (
        export_candidate_task_routing_observation, export_task_routing_observation, export_math_observation,
    )
    value = export_candidate_task_routing_observation(ready_result)
    assert value['resolved_request_sha256'] == 'b' * 64 and value['planner_count'] == 0
    assert 'document:1' not in json.dumps(value)
    value['tasks'][0]['requested_outputs'].append('private')
    assert 'private' not in json.dumps(export_candidate_task_routing_observation(ready_result))
    assert export_math_observation(ready_result)['actual_outcome'] == 'full_answer'
    with pytest.raises(ValueError):
        export_task_routing_observation(ready_result)


@pytest.mark.parametrize('field,value', [
    ('schema', 'task-routing-observation-v3'), ('contract', 'unknown'), ('extra', 'private'),
    ('candidate_context_sha256', None), ('candidate_context_sha256', True),
    ('candidate_context_sha256', 'A' * 64), ('resolved_request_sha256', None),
    ('resolved_request_sha256', 'b' * 63), ('resolved_request_sha256', False),
])
def test_v4_ready_rejects_forged_shapes_and_hashes(ready_result, field, value):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_observation
    changed = {**ready_result.observed_task_routing, field: value}
    with pytest.raises(ValueError):
        export_candidate_task_routing_observation(replace(ready_result, observed_task_routing=changed))


@pytest.mark.parametrize('field', ['subqueries', 'corrections', 'repairs', 'calculations', 'graph_edges',
                                  'graph_examined_relations', 'provider_retries', 'final_generations'])
def test_v4_nonready_rejects_any_execution(overflow_result, field):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_observation
    result = replace(overflow_result, observed_task_routing=candidate_observation(),
        diagnostics=replace(overflow_result.diagnostics,
            budget=replace(overflow_result.diagnostics.budget, **{field: 1})))
    with pytest.raises(ValueError):
        export_candidate_task_routing_observation(result)


@pytest.mark.parametrize('fault', ['citation', 'retrieved', 'math', 'graph', 'reason', 'hash', 'planners', 'private_id'])
def test_v4_nonready_rejects_evidence_forgery_or_provider_ids(overflow_result, fault):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_observation
    from mech_chatbot.rag.execution import RagDiagnostics
    result = replace(overflow_result, observed_task_routing=candidate_observation())
    if fault == 'citation':
        result = replace(result, citations=({'source_id': 'private'},))
    elif fault == 'math':
        result = replace(result, observed_math_results=({'private': True},))
    elif fault in ('retrieved', 'graph'):
        field = 'retrieved_docs' if fault == 'retrieved' else 'graph_v2'
        result = replace(result, diagnostics=RagDiagnostics.from_mapping({**dict(result.diagnostics), field: [{}]}))
    elif fault == 'reason':
        result = replace(result, refusal_reason='other')
    elif fault == 'hash':
        result = replace(result, observed_task_routing=candidate_observation(resolved_request_sha256='b' * 64))
    elif fault == 'planners':
        result = replace(result, diagnostics=replace(result.diagnostics,
            budget=replace(result.diagnostics.budget, planners=2)))
    else:
        value = candidate_observation()
        value['tasks'][0]['selection_id'] = 'private_provider_id'
        result = replace(result, observed_task_routing=value)
    with pytest.raises(ValueError):
        export_candidate_task_routing_observation(result)


def test_v4_ready_rejects_provider_retry(ready_result):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_observation
    result = replace(ready_result, diagnostics=replace(ready_result.diagnostics,
        budget=replace(ready_result.diagnostics.budget, provider_retries=1)))
    with pytest.raises(ValueError):
        export_candidate_task_routing_observation(result)


@pytest.mark.parametrize('value', [None, [], False, 'private'])
def test_v4_export_requires_a_completed_structured_observation(overflow_result, value):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_observation
    with pytest.raises(ValueError):
        export_candidate_task_routing_observation(replace(overflow_result, observed_task_routing=value))
    with pytest.raises(ValueError):
        export_candidate_task_routing_observation(None)


def test_v4_missing_context_can_export_only_an_untouched_prevalidation_failure(overflow_result):
    from mech_chatbot.evaluation.grounded_math_v2 import export_candidate_task_routing_observation
    result = replace(overflow_result, refusal_reason='task_invalid_plan',
        observed_task_routing=candidate_observation(origin='prevalidation', disposition='invalid',
                                                  tasks=[], candidate_context_sha256=None),
        diagnostics=replace(overflow_result.diagnostics,
                            budget=replace(overflow_result.diagnostics.budget, planners=0)))
    observed = export_candidate_task_routing_observation(result)
    assert observed['candidate_context_sha256'] is None and observed['planner_count'] == 0
    for origin, disposition in [('planner', 'invalid'), ('prevalidation', 'unsupported')]:
        value = {**result.observed_task_routing, 'origin': origin, 'disposition': disposition}
        changed = replace(result, observed_task_routing=value,
            diagnostics=replace(result.diagnostics,
                budget=replace(result.diagnostics.budget, planners=1 if origin == 'planner' else 0)))
        with pytest.raises(ValueError):
            export_candidate_task_routing_observation(changed)
