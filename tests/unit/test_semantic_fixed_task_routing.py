"""Draft routing lifecycle through actual approved SQL and fixed Math executors.

Provider stubs prove structural/lifecycle behavior, not model semantic quality.
"""
from dataclasses import replace
import json
import threading
from types import SimpleNamespace

import pytest
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_rag_scope_comparison import approved_pair, approved_empty_pair, request, store
from .test_rag_bom_chat import bom_store, request as bom_request, connection, PROFILE, expand_bom

VERSION = 'semantic-fixed-task-routing-v2-draft'
# Exact text from the frozen 004fcd5dce3a bank; tests do not require ignored reports.
QUESTIONS = {36: 'Hai bảng đã duyệt cùng tính tiền VND: bảng A có 0.1 và 0.2, bảng B có 0.6. Hãy tính tổng A, tổng B, chênh lệch B−A và tỷ lệ tăng, kèm nguồn từng bước.', 41: 'Bảng A đã xác nhận không có dòng nào, bảng B có số 1.235 và quy tắc làm tròn hai chữ số đã duyệt. Hãy cho cả giá trị gốc và số hiển thị của B.', 43: 'Bảng A đã được xác nhận đầy đủ và không có dòng nào; bảng B có 1.235. Tổng A có được tính là 0 không, hay phải coi là thiếu dữ liệu?'}


def question(number):
    return QUESTIONS[number]


def payload(text, slots=(('comparison', 'compare_two_scopes'),), outputs=None):
    return {'schema': VERSION, 'disposition': 'ready', 'tasks': [
        {'task_id': key, 'selection_id': key, 'template': template,
         'spans': [{'start': 0, 'end': len(text), 'text': text}],
         'requested_outputs': outputs or (['sum_a', 'sum_b', 'delta', 'percent_change'] if template == 'compare_two_scopes' else ['totals_by_part'])}
        for key, template in slots], 'unresolved': []}


class Planner:
    def __init__(self, response):
        self.response, self.calls = response, []

    def invoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if callable(self.response):
            return self.response(messages, **kwargs)
        return SimpleNamespace(content=self.response if isinstance(self.response, str) else json.dumps(self.response, ensure_ascii=False))


def execute(engine, selected, planner, **kwargs):
    with bind_repository_runtime(db_engine=engine):
        return collect_rag_events(DefaultRagExecutor(provider_adapter=planner, **kwargs).run(selected,
            RagInvocation('semantic-fixture', mode='evaluation', capture_math_observations=True,
                          task_routing_contract=VERSION)))


def test_tec036_numeric_premises_are_one_comparison_not_seven_branches(store):
    approved_pair(store)
    selected = replace(request(), question=question(36))
    planner = Planner(payload(selected.question))
    result = execute(store, selected, planner)
    assert result.outcome == 'answered'
    assert len(planner.calls) == result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.calculations == 4
    assert [s['value'] for s in result.observed_math_results[0]['steps']] == ['4', '8', '4', '100']
    assert len(result.citations) == 2
    assert planner.calls[0][1]['surface'] == 'query_decomposition'
    assert planner.calls[0][1]['retry_counter'].limits.provider_retries == 0


@pytest.mark.parametrize('language', ['vi', 'en'])
@pytest.mark.parametrize('number', [41, 43])
def test_selected_empty_comparison_renders_all_requested_canonical_projections(store, tmp_path, language, number):
    selected, _ = approved_empty_pair(store, tmp_path)
    selected = replace(selected, question=question(number), response_language=language)
    planner = Planner(payload(selected.question, outputs=['sum_a', 'sum_b', 'delta', 'percent_change',
                            'pre_round_value', 'display_value', 'formula', 'citations', 'reviewed_empty_basis']))
    result = execute(store, selected, planner)
    assert result.outcome == 'answered' and result.diagnostics['answer_outcome'] == 'partial_answer'
    for step in result.observed_math_results[0]['steps']:
        assert step['formula'] in result.answer
    assert '1.235' in result.answer and '1.24' in result.answer
    assert ('reviewed empty scope' if language == 'en' else 'phạm vi rỗng đã duyệt') in result.answer
    assert [s['value'] for s in result.observed_math_results[0]['steps']] == ['0', '1.235', '1.235', None]
    assert result.diagnostics.budget.calculations == 4 and len(result.citations) == 2


@pytest.mark.parametrize('language', ['vi', 'en'])
def test_raw_projection_is_visible_even_if_it_equals_the_display_numeric_value(store, language):
    approved_pair(store)
    selected = replace(request(), question='Please show the exact raw totals as well.', response_language=language)
    result = execute(store, selected, Planner(payload(selected.question, outputs=['pre_round_value', 'formula'])))
    assert ('value before rounding: 4' if language == 'en' else 'giá trị trước làm tròn: 4') in result.answer
    assert result.observed_math_results[0]['steps'][0]['formula'] in result.answer


@pytest.mark.parametrize('language', ['vi', 'en'])
def test_bom_requested_formula_and_quantity_basis_use_actual_occurrence_sources(bom_store, language):
    _, engine = bom_store
    selected = replace(bom_request(), question='Please explain this selected bill of materials and its calculation.', response_language=language)
    planner = Planner(payload(selected.question, (('bom', 'bom_rollup'),), ['totals_by_part', 'formula', 'quantity_basis', 'citations']))
    result = execute(engine, selected, planner)
    step = result.observed_math_results[0]['steps'][0]
    assert step['formula'] in result.answer
    occurrence = result.observed_math_results[0]['sources'][0]['occurrences'][0]
    assert occurrence['occurrence_id'] in result.answer
    assert ('quantity path' if language == 'en' else 'đường số lượng') in result.answer
    assert ('per parent' if language == 'en' else 'mỗi cụm cha') in result.answer
    assert all(f"[SRC:{card['source_id']}]" in result.answer for card in result.citations)
    assert result.diagnostics.budget.calculations == 1


def decimal_pair(store):
    from decimal import Decimal
    from mech_chatbot.domain.structured_evidence import NumericFact, SourceLocator, ScopeManifest, RoundingRule
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact, review_scope_completeness
    for scope_id, values in (('a', ('0.1', '0.2')), ('b', ('0.6',))):
        facts = tuple(NumericFact(f'{scope_id}-{i}', 'Value', value, Decimal(value), 'VND',
            SourceLocator('1', '2', sheet='CSV', cell=f'B{i+2}', row_key=f'{scope_id}-row-{i}'),
            source_hash='a'*64, currency='VND', extraction_method='csv') for i, value in enumerate(values))
        save_extracted_scope(ScopeManifest(scope_id, 'batch-1', facts, source_revisions=(('1','2'),), unit='VND', currency='VND'), db_engine=store)
        for fact in facts:
            review_numeric_fact(fact.fact_id, PROFILE, action='approve', value_text=str(fact.value),
                                note='Synthetic exact decimal review', db_engine=store)
        review_scope_completeness(scope_id, PROFILE, expected_row_ids=tuple(f.locator.row_key for f in facts),
            rounding_rule=RoundingRule(2), definition='Synthetic amount', note='Synthetic complete rows review', db_engine=store)


def test_tec036_three_approved_decimal_rows_supply_the_math_not_the_planner(store):
    decimal_pair(store)
    selected = replace(request(), question=question(36))
    planner = Planner(payload(selected.question))
    result = execute(store, selected, planner)
    assert [s['value'] for s in result.observed_math_results[0]['steps']] == ['0.3', '0.6', '0.3', '100']
    assert len(result.citations) == 2 and [c['fact_count'] for c in result.citations] == [2, 1]
    assert result.diagnostics.budget.calculations == 4
    assert [f['value'] for f in result.observed_math_results[0]['sources'][0]['facts']] == ['0.1', '0.2']
    assert 'facts' not in json.loads(planner.calls[0][0][1]['content'])


@pytest.mark.parametrize('text', [
    'Compare the two selected exports: totals, delta, percent, and citations.',
    '1. Tổng A\n2. Tổng B\n3. Chênh lệch\n4. Phần trăm thay đổi',
    'A has 4, B has 8; show four results and their approved source cells.',
    'B so với A khác bao nhiêu, tỷ lệ tăng là gì, lấy cả hai tổng và nguồn?',
])
def test_paraphrases_and_four_dependent_outputs_use_one_planner_one_fixed_action(store, text):
    approved_pair(store)
    planner = Planner(payload(text))
    result = execute(store, replace(request(), question=text), planner)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.calculations == 4 and len(planner.calls) == 1


@pytest.mark.parametrize('text', ['Compare selected scopes.', 'So sánh hai phạm vi đã chọn?'])
def test_full_string_simple_comparison_skips_planner(store, text):
    approved_pair(store)
    planner = Planner(lambda *_args, **_kwargs: pytest.fail('No planner for full-string grammar'))
    result = execute(store, replace(request(), question=text), planner)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0


@pytest.mark.parametrize('text', ['Calculate selected BOM', 'Tính BOM đã chọn!'])
def test_full_string_simple_bom_skips_planner(bom_store, text):
    _, engine = bom_store
    planner = Planner(lambda *_args, **_kwargs: pytest.fail('No planner for full-string grammar'))
    result = execute(engine, replace(bom_request(), question=text), planner)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0


@pytest.mark.parametrize('text', ['Compare selected scopes and list the material.',
    'Hỏi bản vẽ con trước, rồi so sánh hai phạm vi đã chọn.', 'So sánh hai phạm vi, vật liệu là gì, dẫn nguồn.'])
def test_unsupported_tail_is_a_planner_refusal_before_any_evidence_action(store, text):
    planner = Planner({'schema': VERSION, 'disposition': 'unsupported', 'tasks': [],
        'unresolved': [{'spans': [{'start': 0, 'end': len(text), 'text': text}], 'reason': 'unsupported_task'}]})
    result = execute(store, replace(request(), question=text), planner)
    assert result.outcome == 'refused' and result.citations == ()
    assert result.diagnostics.budget.calculations == result.diagnostics.budget.subqueries == 0
    assert result.diagnostics.budget.planners == 1 and text not in result.answer


def invalid_payload(kind, text):
    value = payload(text)
    task = value['tasks'][0]
    changes = {
        'schema': lambda: value.update(schema='v1'),
        'unknown_key': lambda: value.update(sql='SELECT private'),
        'confidence': lambda: task.update(confidence=1),
        'operand': lambda: task.update(operands=[1, 2]),
        'expression': lambda: task.update(expression='a+b'),
        'unknown_output': lambda: task.update(requested_outputs=['material']),
        'duplicate_output': lambda: task.update(requested_outputs=['sum_a', 'sum_a']),
        'unknown_template': lambda: task.update(template='graph_query'),
        'unknown_selection': lambda: task.update(selection_id='private'),
        'null_selection': lambda: task.update(selection_id=None),
        'empty_tasks': lambda: value.update(tasks=[]),
        'duplicate_task': lambda: value['tasks'].append(dict(task)),
        'unresolved_ready': lambda: value.update(unresolved=[{'spans': task['spans'], 'reason': 'unsupported_task'}]),
        'fake_span': lambda: task['spans'][0].update(text='private'),
        'bool_offset': lambda: task['spans'][0].update(start=False),
        'float_offset': lambda: task['spans'][0].update(start=0.0),
        'negative_offset': lambda: task['spans'][0].update(start=-1),
        'long_offset': lambda: task['spans'][0].update(end=len(text)+1),
        'empty_spans': lambda: task.update(spans=[]),
        'unknown_reason': lambda: value.update(disposition='clarify', unresolved=[{'spans': task['spans'], 'reason': 'private'}]),
        'fake_overflow': lambda: value.update(disposition='overflow'),
    }
    if kind in changes:
        changes[kind]()
        return value
    if kind == 'duplicate_json_key':
        return json.dumps(value)[:-1] + ', "schema": "' + VERSION + '"}'
    if kind == 'nonfinite':
        return json.dumps(value).replace('"selection_id": "comparison"', '"selection_id": NaN').replace('"ready"', '"clarify"')
    return {'invalid_json': '{', 'too_long': ' '*65537, 'wrong_type': '[]', 'too_deep': '['*1100+'0'+']'*1100}[kind]


@pytest.mark.parametrize('kind', ['schema','unknown_key','confidence','operand','expression','unknown_output',
    'duplicate_output','unknown_template','unknown_selection','null_selection','empty_tasks','duplicate_task',
    'unresolved_ready','fake_span','bool_offset','float_offset','negative_offset','long_offset','empty_spans',
    'unknown_reason','fake_overflow','duplicate_json_key','nonfinite','invalid_json','too_long','wrong_type','too_deep'])
def test_malformed_planner_json_is_generic_clarification_not_math_or_fake_overflow(store, kind):
    text = 'Show the selected comparison and all relevant arithmetic outputs.'
    result = execute(store, replace(request(), question=text), Planner(invalid_payload(kind, text)))
    assert result.outcome == 'refused' and result.citations == ()
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.diagnostics.budget.calculations == 0 and result.diagnostics.budget.planners == 1
    assert 'private' not in result.answer and 'SELECT' not in result.answer


def combined_selection():
    from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
    base = bom_request()
    return replace(base, bom_rollup=None, question='Compare the approved quantities and policy, then calculate BOM with the supplied order.',
        query_plan=QueryPlan((QueryBranch('compare', question(36), scope_comparison=('quantity-scope', 'policy')),
            QueryBranch('total', 'Calculate BOM; give every part, basis, formula, sources.', bom_rollup=base.bom_rollup))))


@pytest.mark.parametrize('dependent', [False, True])
def test_comparison_bom_parent_keeps_order_dependencies_and_one_shared_planner(bom_store, dependent):
    from mech_chatbot.domain.query_plan import QueryPlan
    _, engine = bom_store
    selected = combined_selection()
    if dependent:
        first, second = selected.query_plan.branches
        selected = replace(selected, query_plan=QueryPlan((first, replace(second, depends_on=('compare',)))))
    planner = Planner(payload(selected.question, (('compare','compare_two_scopes'), ('total','bom_rollup'))))
    result = execute(engine, selected, planner)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 1
    assert len(planner.calls) == 1 and result.diagnostics.budget.subqueries == 2
    assert result.diagnostics.budget.calculations == 5 and result.diagnostics.budget.final_generations == 0
    assert [r['branch_id'] for r in result.observed_math_results] == ['compare', 'total']
    branches = result.diagnostics['query_v2']['branches']
    assert [b['depends_on'] for b in branches] == [[], ['compare'] if dependent else []]
    assert all(b['executed'] and b['sources'] for b in branches)


def test_missing_dependency_does_not_invent_a_math_observation(bom_store):
    from sqlalchemy import text
    from mech_chatbot.domain.query_plan import QueryPlan
    store, engine = bom_store
    selected = combined_selection()
    first, second = selected.query_plan.branches
    selected = replace(selected, query_plan=QueryPlan((first, replace(second, depends_on=('compare',)))))
    with store.begin() as conn:
        conn.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='quantity-scope'"))
    result = execute(engine, selected, Planner(payload(selected.question, (('compare','compare_two_scopes'), ('total','bom_rollup')))))
    branches = result.diagnostics['query_v2']['branches']
    assert branches[0]['status'] == 'unknown' and branches[1]['status'] == 'blocked'
    assert branches[1]['executed'] is False and branches[1]['sources'] == []
    assert result.diagnostics.budget.calculations == 0 and result.diagnostics.budget.subqueries == 1
    assert len(result.observed_math_results) == 1 and result.observed_math_results[0]['steps'] == []


@pytest.mark.parametrize('change', ['omit', 'reorder', 'change_dependency'])
def test_ready_cannot_drop_or_reorder_declared_query_slots(bom_store, change):
    _, engine = bom_store
    selected = combined_selection()
    value = payload(selected.question, (('compare','compare_two_scopes'), ('total','bom_rollup')))
    if change == 'omit':
        value['tasks'] = value['tasks'][:1]
    elif change == 'reorder':
        value['tasks'].reverse()
    else:
        value['tasks'][1]['depends_on'] = []
    result = execute(engine, selected, Planner(value))
    assert result.refusal_reason == 'task_invalid_plan' and result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('selected', [replace(request(), scope_comparison=None),
    replace(bom_request(), bom_rollup=None, graph_query=bom_request().bom_rollup.graph),
    replace(request(), scope_comparison=None, text_query=True)])
def test_unsupported_graph_text_or_unselected_requests_do_not_call_provider(store, selected):
    planner = Planner(lambda *_args, **_kwargs: pytest.fail('No provider for unsupported selected template'))
    result = execute(store, selected, planner)
    assert result.refusal_reason == 'task_unsupported' and result.diagnostics.budget.planners == 0
    assert result.citations == () and result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('mode', ['production', 'pilot_replay'])
def test_draft_opt_in_rejects_production_at_the_invocation_boundary(mode):
    with pytest.raises(ValueError, match='Task routing'):
        RagInvocation('invalid', mode=mode, task_routing_contract=VERSION)


@pytest.mark.parametrize('value', [True, 2, '', 'semantic-fixed-task-routing-v2'])
def test_opt_in_requires_exact_typed_version(value):
    with pytest.raises(ValueError, match='Task routing'):
        RagInvocation('invalid', mode='test', task_routing_contract=value)


def test_viewer_is_blocked_before_provider_without_math_capture_guard(store):
    from mech_chatbot.rag.execution import AccessScope
    selected = replace(request(), access=AccessScope(roles=frozenset({'viewer'})))
    planner = Planner(lambda *_args, **_kwargs: pytest.fail('No viewer provider call'))
    with bind_repository_runtime(db_engine=store), pytest.raises(PermissionError, match='Evaluation reviewer'):
        collect_rag_events(DefaultRagExecutor(provider_adapter=planner).run(selected,
            RagInvocation('viewer', mode='test', task_routing_contract=VERSION)))


def test_v1_default_retains_original_overflow_and_does_not_call_planner(store):
    planner = Planner(lambda *_args, **_kwargs: pytest.fail('Default fixed v1 guard stays provider free'))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor(provider_adapter=planner).run(replace(request(), question=question(36)),
            RagInvocation('legacy', mode='evaluation')))
    assert result.refusal_reason == 'intent_overflow' and result.diagnostics['decomposition_intent_overflow'] is True
    assert result.diagnostics.budget.planners == result.diagnostics.budget.calculations == 0


def test_four_full_independent_commands_refuse_before_planner(store):
    text = '\n'.join(f'{i}. Compare selected scopes [comparison{i}]' for i in range(1, 5))
    planner = Planner(lambda *_args, **_kwargs: pytest.fail('Clear full-command overflow is provider free'))
    result = execute(store, replace(request(), question=text), planner)
    assert result.refusal_reason == 'intent_overflow' and result.diagnostics.budget.planners == 0


def test_uncertain_four_tasks_use_one_planner_then_refuse_before_action(store):
    text = 'Please perform all four independent comparisons of the supplied selection.'
    value = payload(text)
    value['disposition'] = 'overflow'
    value['tasks'] = [{**value['tasks'][0], 'task_id': f'task{i}'} for i in range(4)]
    result = execute(store, replace(request(), question=text), Planner(value))
    assert result.refusal_reason == 'intent_overflow' and result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('stage', ['before', 'during', 'deadline_during', 'provider_timeout', 'budget'])
def test_cancel_deadline_and_planner_budget_do_not_fall_back_or_read_math(store, stage):
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    from mech_chatbot.rag.execution import RequestBudgetLimits, RequestBudgetExceeded
    selected = replace(request(), question=question(36))
    cancel = threading.Event()
    if stage == 'before':
        cancel.set()
    def response(_messages, **kwargs):
        if stage == 'during':
            cancel.set()
        if stage == 'deadline_during':
            kwargs['retry_counter'].started_monotonic -= 1000
        if stage == 'provider_timeout':
            raise TimeoutError('Synthetic planner timeout')
        return SimpleNamespace(content=json.dumps(payload(selected.question)))
    planner = Planner(response)
    limits = RequestBudgetLimits(planners=0) if stage == 'budget' else RequestBudgetLimits()
    expected = ExternalAICallCancelled if stage in ('before', 'during') else RequestBudgetExceeded if stage == 'budget' else TimeoutError
    with bind_repository_runtime(db_engine=store), pytest.raises(expected):
        collect_rag_events(DefaultRagExecutor(provider_adapter=planner, budget_limits=limits).run(selected,
            RagInvocation('lifecycle', mode='test', task_routing_contract=VERSION), cancel))
    assert len(planner.calls) == (0 if stage in ('before', 'budget') else 1)


@pytest.mark.parametrize('change', ['question', 'selection', 'sibling'])
def test_internal_compiled_receipt_cannot_be_reused_after_question_or_selection_changes(store, change):
    from mech_chatbot.rag.pipeline import execute_pipeline
    from mech_chatbot.rag.query_decomposition import fixed_task_guard
    selected = replace(request(), question=question(36))
    def altered_pipeline(state):
        assert fixed_task_guard(state) is None
        if change == 'question':
            state.request = replace(state.request, question=state.request.question+' và vật liệu?')
        elif change == 'selection':
            state.request = replace(state.request, scope_comparison=('other','b'))
        else:
            state.task_slot = 'sibling'
        return execute_pipeline(state)
    result = execute(store, selected, Planner(payload(selected.question)), execute_pipeline=altered_pipeline)
    assert result.refusal_reason == 'task_binding_mismatch' and result.diagnostics.budget.calculations == 0


def test_planner_sees_fixed_selection_parameters_not_facts_or_access_profile(bom_store):
    _, engine = bom_store
    selected = combined_selection()
    planner = Planner(payload(selected.question, (('compare','compare_two_scopes'), ('total','bom_rollup'))))
    execute(engine, selected, planner)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['slots'][0]['selection'] == ['quantity-scope', 'policy']
    assert prompt['slots'][1]['selection']['order_quantity'] == '3'
    assert prompt['slots'][1]['selection']['rounding_scope'] == 'policy'
    assert all(key not in prompt for key in ('facts','roles','access','actor','user_id'))


def test_declared_child_questions_reach_the_single_planner_before_receipts_are_bound(bom_store):
    from mech_chatbot.domain.query_plan import QueryPlan
    _, engine = bom_store
    selected = combined_selection()
    first, second = selected.query_plan.branches
    child_question = second.question + ' Also list materials from the drawings.'
    selected = replace(selected, query_plan=QueryPlan((first, replace(second, question=child_question))))

    def classify(messages, **kwargs):
        prompt = json.loads(messages[1]['content'])
        assert [slot['question'] for slot in prompt['slots']] == [first.question, child_question]
        return SimpleNamespace(content=json.dumps({
            'schema': VERSION, 'disposition': 'unsupported', 'tasks': [], 'unresolved': [],
        }))

    result = execute(engine, selected, Planner(classify))
    assert result.outcome == 'refused' and result.citations == ()
    assert result.refusal_reason == 'task_clarification'
    assert result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


def test_simple_parent_cannot_skip_planning_a_different_child_question(store):
    from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
    child_question = 'Compare selected scopes and list drawing materials.'
    selected = replace(request(), question='Compare selected scopes', scope_comparison=None,
                       query_plan=QueryPlan((QueryBranch('compare', child_question,
                                                        scope_comparison=('scope-a', 'scope-b')),)))
    planner = Planner({'schema': VERSION, 'disposition': 'unsupported', 'tasks': [], 'unresolved': []})
    result = execute(store, selected, planner)
    assert result.refusal_reason == 'task_clarification' and len(planner.calls) == 1
    assert result.diagnostics.budget.calculations == result.diagnostics.budget.subqueries == 0


def test_child_specific_requested_outputs_are_rendered_by_the_correct_fixed_executor(bom_store):
    _, engine = bom_store
    selected = combined_selection()

    def classify(messages, **kwargs):
        prompt = json.loads(messages[1]['content'])
        assert prompt['slots'][1]['question'] == selected.query_plan.branches[1].question
        response = payload(selected.question, (('compare','compare_two_scopes'), ('total','bom_rollup')))
        response['tasks'][1]['requested_outputs'] = ['totals_by_part', 'formula', 'quantity_basis', 'citations']
        return SimpleNamespace(content=json.dumps(response))

    result = execute(engine, selected, Planner(classify))
    branches = result.diagnostics['query_v2']['branches']
    assert branches[1]['math']['steps'][0]['formula'] in branches[1]['answer']
    assert 'đường số lượng' in branches[1]['answer'] and 'mỗi cụm cha' in branches[1]['answer']
    assert result.diagnostics.budget.planners == 1 and result.diagnostics.budget.calculations == 5


def test_nonfinite_json_cannot_be_disguised_as_null_on_a_nonready_task(store):
    text = 'Check my selected amounts.'
    value = payload(text)
    value['disposition'] = 'clarify'
    raw = json.dumps(value).replace('"selection_id": "comparison"', '"selection_id": Infinity')
    result = execute(store, replace(request(), question=text), Planner(raw))
    assert result.refusal_reason == 'task_invalid_plan'


def test_changed_child_question_cannot_use_an_existing_branch_receipt(bom_store):
    from mech_chatbot.rag.pipeline import execute_pipeline
    from mech_chatbot.rag.query_decomposition import fixed_task_guard
    _, engine = bom_store
    selected = combined_selection()
    def tampered_child(state):
        assert fixed_task_guard(state) is None
        branch = state.request.query_plan.branches[0]
        child = replace(state.request, query_plan=None, question=branch.question,
                        scope_comparison=branch.scope_comparison, bom_rollup=None)
        receipt = state.task_binding.child(child, branch.branch_id)
        state.task_binding, state.task_slot = receipt, branch.branch_id
        state.request = replace(child, question='Now also list materials from restricted drawings.')
        return execute_pipeline(state)
    planner = Planner(payload(selected.question, (('compare','compare_two_scopes'), ('total','bom_rollup'))))
    result = execute(engine, selected, planner, execute_pipeline=tampered_child)
    assert result.refusal_reason == 'task_binding_mismatch' and result.diagnostics.budget.calculations == 0


def test_child_receipt_refuses_a_selection_that_was_not_compiled(bom_store):
    from mech_chatbot.rag.query_decomposition import fixed_task_guard
    _, engine = bom_store
    selected = combined_selection()
    def wrong_child(state):
        assert fixed_task_guard(state) is None
        child = replace(state.request, query_plan=None, scope_comparison=('unselected','policy'))
        state.task_binding.child(child, 'compare')
        pytest.fail('Invalid child receipt must fail closed')
    with pytest.raises(ValueError, match='Stale fixed task binding'):
        execute(engine, selected, Planner(payload(selected.question, (('compare','compare_two_scopes'), ('total','bom_rollup')))),
                execute_pipeline=wrong_child)


def test_candidate_prefers_the_existing_one_attempt_adapter_surface(store):
    approved_pair(store)
    selected = replace(request(), question=question(36))
    class OneAttemptPlanner(Planner):
        def invoke(self, *_args, **_kwargs):
            pytest.fail('Retrying adapter surface must not be used')
        def invoke_once(self, *args, **kwargs):
            return super().invoke(*args, **kwargs)
    planner = OneAttemptPlanner(payload(selected.question))
    result = execute(store, selected, planner)
    assert result.outcome == 'answered' and len(planner.calls) == 1
    assert result.diagnostics.budget.provider_retries == 0


def test_candidate_without_injected_adapter_uses_one_attempt_runtime_surface(store, monkeypatch):
    from mech_chatbot.llm import llm_client
    approved_pair(store)
    selected = replace(request(), question=question(36))
    calls = []
    class RuntimeStub:
        def retry_with(self, *, stop):
            assert stop.max_attempt_number == 1
            def invoke(messages, **kwargs):
                calls.append((messages, kwargs))
                return SimpleNamespace(content=json.dumps(payload(selected.question)))
            return invoke
    monkeypatch.setattr(llm_client, 'gpt_invoke', RuntimeStub())
    result = execute(store, selected, None)
    assert result.outcome == 'answered' and len(calls) == 1
    assert result.diagnostics.budget.provider_retries == 0


def test_legacy_provider_dispatch_without_adapter_remains_the_existing_surface(store, monkeypatch):
    from mech_chatbot.llm import llm_client
    calls = []
    def invoke(messages, **kwargs):
        calls.append(messages)
        return SimpleNamespace(content='fixture response')
    monkeypatch.setattr(llm_client, 'cohere_invoke', invoke)
    def pipeline(state):
        value = state.invoke_provider(['fixture'])
        return state.prepared((iter((value.content,)), '', (), (), {}))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor(execute_pipeline=pipeline).run(request(), RagInvocation('legacy', mode='test')))
    assert result.answer == 'fixture response' and calls == [['fixture']]


@pytest.mark.parametrize('language', ['vi', 'en'])
def test_bom_basis_uses_every_source_occurrence_even_when_context_is_clipped(bom_store, language):
    store, engine = bom_store
    expand_bom(store)
    selected = replace(bom_request(), question='Explain the full BOM quantity basis with all occurrences.', response_language=language)
    planner = Planner(payload(selected.question, (('bom','bom_rollup'),), ['quantity_basis','totals_by_part']))
    result = execute(engine, selected, planner)
    occurrences = result.observed_math_results[0]['sources'][0]['occurrences']
    assert len(occurrences) == 51 and all(o['occurrence_id'] in result.answer for o in occurrences)
    assert result.diagnostics.budget.graph_edges == 50 and result.diagnostics.budget.calculations == 1
    assert result.observed_math_results[0]['steps'][0]['display_value'] == '306.00'


@pytest.mark.parametrize('language', ['vi', 'en'])
def test_bom_empty_basis_is_a_reviewed_declaration_not_missing_rows(bom_store, language):
    from sqlalchemy import text
    store, engine = bom_store
    with store.begin() as conn:
        conn.execute(text('DELETE FROM ReviewedGraphOccurrences'))
        conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=0 WHERE DocID=1'))
    selected = replace(bom_request(), question='Explain the reviewed empty BOM basis.', response_language=language)
    result = execute(engine, selected, Planner(payload(selected.question, (('bom','bom_rollup'),), ['reviewed_empty_basis','citations'])))
    assert ('Reviewed complete BOM' if language == 'en' else 'BOM đầy đủ đã duyệt') in result.answer
    assert len(result.citations) == 2 and all(f"[SRC:{c['source_id']}]" in result.answer for c in result.citations)
    assert result.observed_math_results[0]['steps'] == [] and result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('bad', [None, 7, object(), '', ' '*20001])
def test_invalid_question_is_rejected_before_planning_or_hashing(store, bad):
    planner = Planner(lambda *_args, **_kwargs: pytest.fail('No invalid question provider'))
    result = execute(store, replace(request(), question=bad), planner)
    assert result.refusal_reason == 'task_unsupported' and result.diagnostics.budget.planners == 0


def test_raw_bool_cannot_be_used_as_an_internal_compiled_receipt(store):
    from mech_chatbot.rag.pipeline import execute_pipeline
    def injected(state):
        state.task_binding = True
        return execute_pipeline(state)
    result = execute(store, request(), None, execute_pipeline=injected)
    assert result.refusal_reason == 'task_binding_mismatch' and result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('disposition', ['clarify', 'unsupported'])
def test_nonready_dispositions_return_generic_clarification(store, disposition):
    selected = replace(request(), question='Something is ambiguous about the selected comparison.')
    value = {'schema': VERSION, 'disposition': disposition, 'tasks': [], 'unresolved': []}
    result = execute(store, selected, Planner(value))
    assert result.refusal_reason == 'task_clarification' and result.citations == ()


def test_template_mismatch_in_fast_grammar_still_requires_planning(store):
    text = 'Calculate selected BOM'
    planner = Planner({'schema': VERSION, 'disposition': 'unsupported', 'tasks': [], 'unresolved': []})
    result = execute(store, replace(request(), question=text), planner)
    assert result.refusal_reason == 'task_clarification' and len(planner.calls) == 1


@pytest.mark.parametrize('field', ['tasks','unresolved'])
@pytest.mark.parametrize('value', [None, {}, [None]*9])
def test_planner_task_and_unresolved_collections_are_bounded_lists(store, field, value):
    selected = replace(request(), question='Check the selected comparison output structure.')
    response = payload(selected.question)
    response[field] = value
    result = execute(store, selected, Planner(response))
    assert result.refusal_reason == 'task_invalid_plan' and result.diagnostics.budget.calculations == 0
