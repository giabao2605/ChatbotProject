"""Versioned one-scope evaluation, separate from the two-template v2 gate."""
from decimal import Decimal, DecimalException, localcontext

from mech_chatbot.evaluation.grounded_math_v2 import _json_matches, _record, _reference_step
from mech_chatbot.rag.grounded_math_v2 import SCOPE_TOTAL_CONTRACT, _precision, _scope_valid


def evaluate_scope_total(actual, *, scope):
    """Independently verify the sum, rounding, exact row set and recorded policy."""
    try:
        valid = _scope_valid(scope)
    except (ValueError, TypeError, AttributeError):
        valid = False
    reference = _record('sum_reviewed_scope', status='blocked', reason='invalid_scope_evidence')
    if valid:
        try:
            with localcontext() as context:
                context.prec = _precision(tuple(fact.value for fact in scope.facts))
                total = Decimal(0)
                for fact in scope.facts:
                    total += fact.value
                step = _reference_step('sum', total, scope.unit, scope.currency,
                                       f'sum({scope.scope_id})', (scope.scope_id,), scope.rounding_rule)
            reference = _record('sum_reviewed_scope', steps=(step,), sources=(scope,), policy=scope.rounding_rule)
        except DecimalException:
            reference = _record('sum_reviewed_scope', status='blocked', reason='numeric_range')
    reference = {**reference, 'schema': 'grounded-calculation-scope-total-v1'}
    observed = actual if isinstance(actual, dict) else {}
    checks = {field: _json_matches(observed.get(field), value) for field, value in reference.items()}
    return {'schema': 'grounded-scope-total-evaluation-v1', 'contract': SCOPE_TOTAL_CONTRACT,
            'template': 'sum_reviewed_scope', 'passed': all(checks.values()), 'checks': checks,
            'expected_status': reference['status'], 'domain_step_count': reference['domain_step_count'],
            'evidence_kind': 'offline', 'accepted': False}
