"""One classification, then deterministic sums; stubs are not model evidence."""
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact, review_scope_completeness
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
from mech_chatbot.domain.structured_evidence import RoundingRule
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from mech_chatbot.rag import query_decomposition as routing
from .test_rag_scope_comparison import request
from .test_structured_evidence_repository import PROFILE, store, scope

VERSION = 'semantic-scope-total-query-routing-v1-draft'
# Exact frozen Accountant questions; ignored reports are not a test dependency.
QUESTIONS = (
    'Tổng doanh thu đã ghi sổ trong kỳ được chọn là bao nhiêu, chỉ lấy hóa đơn còn hiệu lực?',
    'Tại ngày chốt đã chọn, tổng công nợ phải trả nhà cung cấp của nhà máy là bao nhiêu?',
    'Tổng phải thu khách hàng còn mở của pháp nhân được chọn tại ngày chốt là bao nhiêu?',
)


def selected():
    return replace(request(), question='; '.join(QUESTIONS), scope_comparison=None,
        query_plan=QueryPlan(tuple(QueryBranch(f'b{i}', question, scope_total=f'scope-{i}')
                                  for i, question in enumerate(QUESTIONS))))


def payload(chosen, *, disposition='ready'):
    text = chosen.question
    return {'schema': VERSION, 'disposition': disposition, 'tasks': [
        {'task_id': branch.branch_id, 'selection_id': branch.branch_id, 'template': 'sum_reviewed_scope',
         'spans': [{'start': text.index(branch.question), 'end': text.index(branch.question)+len(branch.question),
                    'text': branch.question}], 'requested_outputs': ['sum', 'citations']}
        for branch in chosen.query_plan.branches], 'unresolved': []}


class Planner:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        value = self.answer(messages, **kwargs) if callable(self.answer) else self.answer
        return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))


def execute(engine, chosen, planner):
    with bind_repository_runtime(db_engine=engine):
        return collect_rag_events(DefaultRagExecutor(provider_adapter=planner).run(chosen,
            RagInvocation('sum-routing', mode='evaluation', capture_math_observations=True,
                          task_routing_contract=VERSION)))


def test_original_three_questions_use_one_classification_and_three_exact_sums(store):
    for index, value in enumerate(('1500.00', '500.00', '950.00')):
        candidate = replace(scope(str(index), Decimal(value)), snapshot_id='shared-export')
        save_extracted_scope(candidate, db_engine=store)
        review_numeric_fact(candidate.facts[0].fact_id, PROFILE, action='approve', note='Synthetic review', db_engine=store)
        review_scope_completeness(candidate.scope_id, PROFILE, expected_row_ids=('row1',),
            rounding_rule=RoundingRule(2), definition='Synthetic complete amount', note='Synthetic review', db_engine=store)
    chosen = selected()
    assert routing.split_query_intents(chosen.question)[1]
    planner = Planner(payload(chosen))
    result = execute(store, chosen, planner)
    assert result.outcome == 'answered'
    assert len(planner.calls) == result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 3
    assert result.diagnostics.budget.final_generations == 0
    assert [branch['math']['steps'][0]['display_value'] for branch in result.diagnostics['query_v2']['branches']] == [
        '1500.00', '500.00', '950.00']
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert [slot['selection'] for slot in prompt['slots']] == [{'scope_id': f'scope-{i}'} for i in range(3)]
    assert 'facts' not in prompt and planner.calls[0][1]['surface'] == 'query_decomposition'
    assert planner.calls[0][1]['retry_counter'].limits.provider_retries == 0


@pytest.mark.parametrize('fault', ['four', 'selection', 'span', 'order', 'extra', 'deadline'])
def test_failed_classification_never_reads_evidence_or_calculates(store, monkeypatch, fault):
    from mech_chatbot.rag import scope_total
    monkeypatch.setattr(scope_total, 'load_approved_scope', lambda *_: pytest.fail('premature SQL read'))
    chosen = selected()
    response = payload(chosen)
    if fault == 'four':
        chosen = replace(chosen, question=chosen.question+'; Hãy tổng hợp cả chi phí của kỳ này?')
        response = payload(chosen, disposition='overflow')
        response['tasks'].append({**response['tasks'][0], 'task_id': 'fourth', 'selection_id': None})
    elif fault == 'selection': response['tasks'][0]['selection_id'] = 'different'
    elif fault == 'span': response['tasks'][0]['spans'][0]['text'] = 'invented'
    elif fault == 'order': response['tasks'].reverse()
    elif fault == 'extra': response['tasks'][0]['scope_id'] = 'invented'
    def classify(*_args, **kwargs):
        if fault == 'deadline': kwargs['retry_counter'].started_monotonic -= 1000
        return response
    planner = Planner(classify)
    if fault == 'deadline':
        with pytest.raises(TimeoutError): execute(store, chosen, planner)
        assert planner.calls[0][1]['retry_counter'].calculations == 0
    else:
        result = execute(store, chosen, planner)
        assert result.outcome == 'refused' and not result.citations
        assert result.diagnostics.budget.calculations == result.diagnostics.budget.subqueries == 0
    assert len(planner.calls) == 1


@pytest.mark.parametrize('text', ['Tổng phạm vi đã chọn', 'Sum selected scope.'])
def test_simple_standalone_total_never_classifies(store, text):
    candidate = scope('simple')
    save_extracted_scope(candidate, db_engine=store)
    review_numeric_fact(candidate.facts[0].fact_id, PROFILE, action='approve', note='Synthetic', db_engine=store)
    review_scope_completeness(candidate.scope_id, PROFILE, expected_row_ids=('row1',), rounding_rule=RoundingRule(2),
        definition='Synthetic', note='Synthetic', db_engine=store)
    chosen = replace(request(), question=text, scope_comparison=None, scope_total=candidate.scope_id)
    planner = Planner(lambda *_a, **_kw: pytest.fail('simple sum does not classify'))
    result = execute(store, chosen, planner)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0


def test_clear_four_numbered_commands_are_refused_without_classification(store):
    chosen = replace(selected(), question='\n'.join(f'{i}. Sum selected scope [b{i}]' for i in range(4)))
    planner = Planner(lambda *_a, **_kw: pytest.fail('clear overflow does not classify'))
    result = execute(store, chosen, planner)
    assert result.outcome == 'refused' and result.refusal_reason == 'intent_overflow'
    assert result.diagnostics.budget.planners == result.diagnostics.budget.calculations == 0


def test_scope_change_invalidates_verified_child_binding_and_legacy_keys_are_unchanged():
    chosen = selected()
    slots = routing._task_slots(chosen, VERSION)
    binding = routing.FixedTaskBinding(routing._request_key(chosen), routing._request_key(chosen), slots,
        tuple((branch.branch_id, 'sum_reviewed_scope', ('sum',)) for branch in chosen.query_plan.branches), contract=VERSION)
    branch = chosen.query_plan.branches[0]
    child = replace(chosen, question=branch.question, query_plan=None, scope_total=branch.scope_total)
    assert binding.child(child, branch.branch_id).request_key == routing._request_key(child)
    with pytest.raises(ValueError, match='Stale fixed task binding'):
        binding.child(replace(child, scope_total='scope-other'), branch.branch_id)
    legacy = request()
    expected = hashlib.sha256(json.dumps([legacy.question, legacy.response_language, {'scope_comparison': ('a','b')}],
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert routing._request_key(legacy) == expected
