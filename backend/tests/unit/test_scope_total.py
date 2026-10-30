from dataclasses import replace
from decimal import Decimal

import pytest

from mech_chatbot.domain.structured_evidence import RoundingRule
from mech_chatbot.rag import grounded_math_v2 as math
from .test_grounded_math_v2 import scope
from .test_structured_evidence_repository import empty_scope


def test_sum_one_reviewed_scope_records_one_exact_step_and_rounds_after_sum():
    source = scope('ledger', ['0.005', '0.005', '-0.001'])
    result = math.sum_reviewed_scope(source)
    assert result.template == 'sum_reviewed_scope' and result.status == 'complete'
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.step_id == 'sum' and step.value == Decimal('0.009') and step.display_value == '0.01'
    assert step.formula == 'sum(ledger)' and step.source_ids == ('ledger',)
    assert math.serialize_result(result)['sources'][0]['expected_row_ids'] == ['r0', 'r1', 'r2']
    assert source.facts[0].value == Decimal('0.005')


@pytest.mark.parametrize('fault', ['incomplete', 'missing_row', 'pending', 'conflict', 'unauthorized', 'rounding'])
def test_sum_refuses_unreviewed_or_incomplete_evidence_without_partial_numbers(fault):
    source = scope('ledger', ['100', '20'])
    if fault == 'incomplete': source = replace(source, completeness_reviewed=False)
    elif fault == 'missing_row': source = replace(source, expected_row_count=3, expected_row_ids=('r0', 'r1', 'r2'))
    elif fault == 'pending': source = replace(source, facts=(replace(source.facts[0], review_status='pending'), source.facts[1]))
    elif fault == 'conflict': source = replace(source, facts=(replace(source.facts[0], conflicts=('amount_conflict',)), source.facts[1]))
    elif fault == 'unauthorized': source = replace(source, authorized=False)
    else: source = replace(source, rounding_rule=RoundingRule(2, reviewed=False))
    result = math.sum_reviewed_scope(source)
    assert result.status == 'blocked' and result.reason == 'invalid_scope_evidence'
    assert result.steps == () and math.serialize_result(result)['sources'] == []


def test_only_an_explicit_reviewed_empty_scope_can_sum_to_zero():
    source = replace(empty_scope(), authorized=True, completeness_reviewed=True, expected_row_count=0,
        rounding_rule=RoundingRule(2, reviewed=True), definition='Confirmed no activity', unit='VND', currency='VND')
    result = math.sum_reviewed_scope(source)
    assert result.status == 'complete' and result.steps[0].display_value == '0.00'
    undeclared = replace(source, empty_source_locator=None, empty_source_hash='', empty_source_extraction_method='')
    assert math.sum_reviewed_scope(undeclared).status == 'blocked'


def test_sum_numeric_range_is_bounded_without_partial_results():
    result = math.sum_reviewed_scope(scope('ledger', ['1E4096', '1E-4096']))
    assert result.status == 'blocked' and result.reason == 'numeric_range' and not result.steps
