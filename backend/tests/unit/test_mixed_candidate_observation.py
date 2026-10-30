"""Separate mixed routing receipts cannot acquire historical V4 gate authority."""
from dataclasses import replace
import pytest
from .test_candidate_task_observation import ready_result, overflow_result, bom_store, connection, store

VERSION = 'semantic-mixed-candidate-task-routing-v5-draft'


def mixed_result(result, *, ready=True):
    value = {'schema':'mixed-candidate-task-routing-observation-v5-draft', 'contract':VERSION,
        'origin':'planner', 'disposition':'ready' if ready else 'overflow',
        'candidate_context_sha256':'a'*64, 'resolved_request_sha256':'b'*64 if ready else None,
        'tasks':[{'selection_id':'guide' if ready else None, 'template':'text_query',
                  'requested_outputs':['answer','citations'], 'depends_on':[]}] * (1 if ready else 4)}
    return replace(result, observed_task_routing=value,
        diagnostics=replace(result.diagnostics,budget=replace(result.diagnostics.budget,planners=1)))


def test_mixed_routing_export_has_own_schema_and_legacy_export_rejects(ready_result):
    from mech_chatbot.evaluation.grounded_math_v2 import (
        export_mixed_candidate_task_routing_observation, export_candidate_task_routing_observation,
        export_task_routing_observation, export_math_observation)
    result = mixed_result(ready_result)
    observation = export_mixed_candidate_task_routing_observation(result)
    assert observation['contract'] == VERSION and observation['planner_count'] == 1
    assert observation['accepted'] is False and observation['offline_only'] is True
    for old_exporter in (export_candidate_task_routing_observation, export_task_routing_observation):
        with pytest.raises(ValueError): old_exporter(result)
    assert export_math_observation(result)['actual_outcome'] == 'full_answer'


def test_mixed_overflow_exports_no_actions_and_clarification(overflow_result):
    from mech_chatbot.evaluation.grounded_math_v2 import export_mixed_candidate_task_routing_observation, export_math_observation
    result = mixed_result(overflow_result, ready=False)
    assert export_mixed_candidate_task_routing_observation(result)['resolved_request_sha256'] is None
    assert export_math_observation(result)['actual_outcome'] == 'clarification_required'


@pytest.mark.parametrize('fault',['outputs','private_field','planners','contract','retry'])
def test_mixed_export_rejects_invalid_or_private_metadata(ready_result, fault):
    from mech_chatbot.evaluation.grounded_math_v2 import export_mixed_candidate_task_routing_observation
    result = mixed_result(ready_result)
    value = result.observed_task_routing
    if fault == 'outputs': value['tasks'][0]['requested_outputs'] = ['sum_a']
    elif fault == 'private_field': value['raw_provider_response'] = 'private'
    elif fault == 'contract': value['contract'] = 'semantic-candidate-task-routing-v4-draft'
    else:
        result = replace(result,diagnostics=replace(result.diagnostics,
            budget=replace(result.diagnostics.budget,**{'planners' if fault=='planners' else 'provider_retries':2})))
    with pytest.raises(ValueError): export_mixed_candidate_task_routing_observation(result)


def test_mixed_decoder_receipt_is_bounded_and_separate(overflow_result):
    from mech_chatbot.evaluation.grounded_math_v2 import export_mixed_candidate_task_routing_diagnostics
    result = mixed_result(overflow_result, ready=False)
    result = replace(result, refusal_reason='task_invalid_plan',
        observed_task_routing={**result.observed_task_routing,'disposition':'invalid','tasks':[]},
        observed_task_routing_failure_code='span_text')
    receipt = export_mixed_candidate_task_routing_diagnostics(result)
    assert receipt['schema'] == 'mixed-candidate-task-routing-diagnostics-v5-draft'
    assert receipt['failure_code'] == 'span_text'
    with pytest.raises(ValueError):
        export_mixed_candidate_task_routing_diagnostics(replace(result,observed_task_routing_failure_code='private reply'))
