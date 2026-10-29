from copy import deepcopy

import pytest

from mech_chatbot.rag.grounded_math_v2 import sum_reviewed_scope, serialize_result
from .test_grounded_math_v2 import scope


def test_total_uses_a_separate_evaluation_version_without_reopening_the_old_gate():
    from mech_chatbot.evaluation.scope_total import evaluate_scope_total
    from mech_chatbot.evaluation.grounded_math_v2 import evaluate_math_v2
    source = scope('ledger', ['0.1', '0.2', '-0.05'])
    actual = serialize_result(sum_reviewed_scope(source))
    evaluation = evaluate_scope_total(actual, scope=source)
    assert evaluation['schema'] == 'grounded-scope-total-evaluation-v1'
    assert evaluation['passed'] and evaluation['domain_step_count'] == 1 and not evaluation['accepted']
    assert not evaluate_math_v2('sum_reviewed_scope', actual, scopes=(source,))['applicable']


@pytest.mark.parametrize('fault', ['value', 'rounding', 'unit', 'source', 'extra_step', 'schema'])
def test_total_evaluator_checks_every_step_and_source(fault):
    from mech_chatbot.evaluation.scope_total import evaluate_scope_total
    source = scope('ledger', ['0.1', '0.2'])
    actual = deepcopy(serialize_result(sum_reviewed_scope(source)))
    if fault == 'value': actual['steps'][0]['value'] = '999'
    elif fault == 'rounding': actual['steps'][0]['display_value'] = '0.31'
    elif fault == 'unit': actual['steps'][0]['unit'] = 'USD'
    elif fault == 'source': actual['sources'][0]['facts'][0]['locator']['row_key'] = 'absent'
    elif fault == 'extra_step': actual['steps'].append(actual['steps'][0])
    else: actual['schema'] = 'grounded-calculation-v2'
    assert not evaluate_scope_total(actual, scope=source)['passed']
