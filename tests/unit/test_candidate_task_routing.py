"""Candidate resolution really executes typed actions through the public executor."""
import json
from dataclasses import replace
import pytest
from types import SimpleNamespace

from mech_chatbot.domain.task_routing import (
    CandidateSelection, CandidateSelectionContext, CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING)
from .test_semantic_graph_task_routing import bom_store, store, connection, graph_request, Planner, execute, payload
from .test_rag_bom_chat import request as bom_request, expand_bom
from sqlalchemy import text


def candidate_request(question='Show the assembly relations and their source.'):
    selected = graph_request(question)
    context = CandidateSelectionContext(tuple(CandidateSelection(key, graph_query=selected.graph_query)
                                             for key in ('first', 'second', 'third', 'fourth')))
    return replace(selected, graph_query=None, candidate_selection_context=context)


def candidate_payload(question, slots=(('first', 'graph_contains'),)):
    value = payload(question, slots)
    return {**value, 'schema': 'candidate-task-routing-quotes-v1',
            'tasks': [{**task, 'spans': [{'text': question}]} for task in value['tasks']]}


@pytest.mark.parametrize('version', [CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING])
def test_planner_is_told_capability_limits_before_accounting_for_declared_question(bom_store, version):
    # This checks the outgoing instruction contract, not a model's interpretation.
    _, engine = bom_store
    question = 'Can independently approved documents be combined, or is a matching revision required?'
    selected = candidate_request(question)
    first = replace(selected.candidate_selection_context.candidates[0], question=question)
    selected = replace(selected, candidate_selection_context=CandidateSelectionContext((first,)))

    def unsupported(messages, **kwargs):
        instruction = messages[0]['content']
        prompt = json.loads(messages[1]['content'])
        assert prompt['question'] == prompt['candidates'][0]['question'] == question
        assert 'must be accounted for' in instruction and 'not necessarily executed' in instruction
        assert 'graph_contains' in instruction and 'graph_where_used' in instruction
        assert 'source compatibility' in instruction and 'unsupported' in instruction
        assert 'only the outputs requested or entailed' in instruction
        assert 'Graph tasks must always request relations, citations and coverage' in instruction
        assert 'Identifying a blocked calculation requests execution status, not formula' in instruction
        assert 'missing sources are not a reviewed-empty basis' in instruction
        return SimpleNamespace(content=json.dumps({'schema':prompt['schema'], 'disposition':'unsupported', 'tasks':[],
            'unresolved':[{'spans':[{'text':question}], 'reason':'unsupported_task'}]}))

    planner = Planner(unsupported)
    result = execute(engine, selected, planner, version=version)
    assert result.refusal_reason == 'task_clarification' and result.citations == ()
    assert result.observed_task_routing['disposition'] == 'unsupported'
    assert len(planner.calls) == result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


def test_four_candidates_can_resolve_one_task_and_execute_with_one_planner(bom_store):
    _, engine = bom_store
    selected = candidate_request()
    planner = Planner(candidate_payload(selected.question))
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and 'part:p' in result.answer and result.citations
    assert result.diagnostics.budget.planners == len(planner.calls) == 1
    assert result.observed_task_routing['schema'] == 'candidate-task-routing-observation-v4'
    assert result.observed_task_routing['tasks'][0]['selection_id'] == 'first'
    assert len(result.observed_task_routing['tasks']) == 1
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert len(prompt['candidates']) == 4 and prompt['schema'] == 'candidate-task-routing-quotes-v1'
    assert prompt['question'] == selected.question and 'question_bounds' not in prompt


@pytest.mark.parametrize('question', [
    'Làm bốn việc riêng: xem cụm, tìm nơi dùng, tính BOM, so sánh hai kỳ.',
    'Please do four independent tasks: contains, where used, BOM, compare.',
    'Perform 4 separate tasks for this assembly.',
    '1. Show the assembly relations\n2. Find where the part is used\n3. Calculate the BOM\n4. Compare the scopes'])
def test_explicit_independent_overflow_stops_before_planner(bom_store, question):
    _, engine = bom_store
    planner = Planner(lambda *_a, **_k: pytest.fail('Obvious overflow must not call provider'))
    result = execute(engine, candidate_request(question), planner, version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'intent_overflow'
    assert result.diagnostics.budget.planners == result.diagnostics.budget.subqueries == 0
    assert result.citations == () and result.observed_task_routing['disposition'] == 'overflow'


def test_ready_cannot_drop_a_caller_declared_task_with_an_unsupported_tail(bom_store):
    _, engine = bom_store
    selected = candidate_request()
    first, second, *_ = selected.candidate_selection_context.candidates
    context = CandidateSelectionContext((first, replace(second, question='Show the assembly and approve its purchase.')))
    selected = replace(selected, candidate_selection_context=context)
    result = execute(engine, selected, Planner(candidate_payload(selected.question)), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_clarification' and result.citations == ()
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.graph_examined_relations == 0
    assert result.observed_task_routing['tasks'][0]['selection_id'] is None


def mixed_request(dependent=False):
    selected = bom_request()
    context = CandidateSelectionContext((
        CandidateSelection('bom', bom_rollup=selected.bom_rollup, question='Calculate this BOM for 3 assemblies.',
                           depends_on=('graph',) if dependent else ()),
        CandidateSelection('graph', graph_query=selected.bom_rollup.graph, question='Show the assembly relations.'),
        CandidateSelection('compare', scope_comparison=('quantity-scope', 'policy'), question='Compare the quantity scopes.')))
    return replace(selected, question='Show relations, calculate this BOM and compare the quantity scopes.',
                   bom_rollup=None, candidate_selection_context=context)


def test_three_mixed_actions_keep_declared_questions_dependencies_and_shared_math_budget(bom_store):
    _, engine = bom_store
    selected = mixed_request(dependent=True)
    planner = Planner(candidate_payload(selected.question, (('graph', 'graph_contains'), ('bom', 'bom_rollup'),
                                                            ('compare', 'compare_two_scopes'))))
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    branches = result.diagnostics['query_v2']['branches']
    assert result.outcome == 'answered' and [branch['status'] for branch in branches] == ['complete'] * 3
    assert [branch['question'] for branch in branches] == ['Show the assembly relations.',
        'Calculate this BOM for 3 assemblies.', 'Compare the quantity scopes.']
    assert branches[1]['depends_on'] == ['graph']
    assert result.diagnostics.budget.planners == len(planner.calls) == 1
    assert result.diagnostics.budget.subqueries == 3 and result.diagnostics.budget.calculations == 5
    assert [record['branch_id'] for record in result.observed_math_results] == ['bom', 'compare']


def test_missing_selected_dependency_clarifies_before_any_action(bom_store):
    _, engine = bom_store
    selected = mixed_request(dependent=True)
    result = execute(engine, selected, Planner(candidate_payload(selected.question, (('bom', 'bom_rollup'),
                     ('graph', 'graph_contains'), ('compare', 'compare_two_scopes')))), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_clarification' and result.diagnostics.budget.subqueries == 0
    assert result.citations == () and all(task['selection_id'] is None for task in result.observed_task_routing['tasks'])


def test_unconfirmed_graph_blocks_dependent_bom_but_independent_comparison_runs(bom_store):
    database, engine = bom_store
    with database.begin() as conn:
        conn.execute(text('UPDATE ReviewedGraphOccurrences SET IsLeaf=0'))
    selected = mixed_request(dependent=True)
    result = execute(engine, selected, Planner(candidate_payload(selected.question, (('graph', 'graph_contains'),
                     ('bom', 'bom_rollup'), ('compare', 'compare_two_scopes')))), version=CANDIDATE_TASK_ROUTING)
    branches = result.diagnostics['query_v2']['branches']
    assert [branch['status'] for branch in branches] == ['partial', 'blocked', 'complete']
    assert branches[1]['executed'] is False and branches[1]['sources'] == []
    assert result.diagnostics.budget.subqueries == 2 and result.diagnostics.budget.calculations == 4


def test_full_manifest_not_context_50_edges_controls_bom_dependency(bom_store):
    database, engine = bom_store
    expand_bom(database)
    selected = mixed_request(dependent=True)
    result = execute(engine, selected, Planner(candidate_payload(selected.question, (('graph', 'graph_contains'),
                     ('bom', 'bom_rollup'), ('compare', 'compare_two_scopes')))), version=CANDIDATE_TASK_ROUTING)
    branches = result.diagnostics['query_v2']['branches']
    assert branches[0]['status'] == 'partial' and branches[0]['data_complete'] is True
    assert branches[1]['status'] == 'complete' and branches[1]['math']['steps'][0]['display_value'] == '306.00'
    assert result.diagnostics.budget.graph_edges == 50 and result.diagnostics.budget.graph_examined_relations == 51
    assert branches[1]['math']['graph_reused_from'] == 'graph'


def test_uncertain_overflow_uses_one_planner_and_no_actions(bom_store):
    _, engine = bom_store
    selected = candidate_request('Show relations; find parents; calculate the BOM; compare both scopes.')
    value = candidate_payload(selected.question, tuple((key, 'graph_contains') for key in ('first', 'second', 'third', 'fourth')))
    value['disposition'] = 'overflow'
    result = execute(engine, selected, Planner(value), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'intent_overflow' and result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0 and not result.citations
    assert all(task['selection_id'] is None and not task['depends_on'] for task in result.observed_task_routing['tasks'])


def test_four_arithmetic_outputs_are_one_task_and_keep_one_planner(bom_store):
    _, engine = bom_store
    selected = candidate_request('Compare scopes: 1. sum A; 2. sum B; 3. delta; 4. percent change.')
    selected = replace(selected, candidate_selection_context=CandidateSelectionContext((
        CandidateSelection('compare', scope_comparison=('quantity-scope', 'policy')),)))
    result = execute(engine, selected, Planner(candidate_payload(selected.question, (('compare', 'compare_two_scopes'),))),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and result.diagnostics.budget.calculations == 4
    assert result.diagnostics.budget.planners == result.diagnostics.budget.subqueries == 1


def test_missing_reference_is_clarified_with_actual_empty_history(bom_store):
    _, engine = bom_store
    selected = candidate_request('Tính lại BOM cho cụm đó.')
    value = candidate_payload(selected.question, ())
    value.update(disposition='clarify', unresolved=[{'spans': [{'text': selected.question}],
        'reason': 'ambiguous_reference'}])
    planner = Planner(value)
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_clarification' and result.citations == ()
    assert json.loads(planner.calls[0][0][1]['content'])['history'] == []
    assert result.diagnostics.budget.subqueries == 0


def test_partial_quote_becomes_exact_child_question_and_context_is_bound(bom_store):
    from mech_chatbot.rag.candidate_task_routing import candidate_context_sha256
    _, engine = bom_store
    selected = candidate_request('A\u0301\U00020000: show the assembly.')
    value = candidate_payload(selected.question)
    value['tasks'][0]['spans'] = [{'text': 'show the assembly.'}]
    result = execute(engine, selected, Planner(value), version=CANDIDATE_TASK_ROUTING)
    assert result.diagnostics['query_v2']['branches'][0]['question'] == 'show the assembly.'
    assert result.observed_task_routing['candidate_context_sha256'] == candidate_context_sha256(selected)
    assert len(result.observed_task_routing['resolved_request_sha256']) == 64


@pytest.mark.parametrize('fault,code', [('type', 'span_shape'), ('outside', 'span_shape'), ('text', 'span_text'),
                                       ('extra', 'task_shape'), ('direction', None)])
def test_invalid_or_mismatched_candidate_plan_never_reads_evidence(bom_store, fault, code):
    _, engine = bom_store
    selected = candidate_request()
    value = candidate_payload(selected.question)
    span = value['tasks'][0]['spans'][0]
    if fault == 'type': span['start'] = True
    elif fault == 'outside': span['end'] = len(selected.question) + 1
    elif fault == 'text': span['text'] = 'Wrong question'
    elif fault == 'extra': value['tasks'][0]['depends_on'] = []
    else: value['tasks'][0]['template'] = 'graph_where_used'
    result = execute(engine, selected, Planner(value), version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'refused' and not result.citations
    assert result.observed_task_routing_failure_code == code
    assert result.diagnostics.budget.graph_examined_relations == result.diagnostics.budget.calculations == 0


def test_candidate_acl_denial_does_not_expose_relation_or_source(bom_store):
    _, engine = bom_store
    selected = candidate_request()
    selected = replace(selected, access=replace(selected.access, allowed_sites=frozenset({'other-site'})))
    result = execute(engine, selected, Planner(candidate_payload(selected.question)), version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'refused' and result.citations == ()
    assert 'part:p' not in result.answer and 'document:1' not in result.answer


def test_mutated_original_history_during_planner_is_invalid_before_actions(bom_store):
    _, engine = bom_store
    history = {'role': 'user', 'content': 'Original context'}
    selected = replace(candidate_request(), history=(history,))
    def mutate(*_args, **_kwargs):
        history['content'] = 'Changed context'
        return SimpleNamespace(content=json.dumps(candidate_payload(selected.question)))
    result = execute(engine, selected, Planner(mutate), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_invalid_plan' and result.citations == ()
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.graph_examined_relations == 0


def test_simple_single_candidate_command_uses_no_planner(bom_store):
    _, engine = bom_store
    selected = candidate_request('Show selected assembly relations.')
    selected = replace(selected, candidate_selection_context=CandidateSelectionContext((selected.candidate_selection_context.candidates[0],)))
    result = execute(engine, selected, Planner(lambda *_a, **_k: pytest.fail('Simple matching command must skip planner')),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0
    assert result.observed_task_routing['origin'] == 'fast_path'


def test_simple_command_with_multiple_alternatives_still_requires_selection(bom_store):
    _, engine = bom_store
    selected = candidate_request('Show selected assembly relations.')
    result = execute(engine, selected, Planner(candidate_payload(selected.question)), version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 1


def test_different_declared_question_cannot_hide_behind_simple_parent_command(bom_store):
    _, engine = bom_store
    selected = candidate_request('Show selected assembly relations.')
    candidate = replace(selected.candidate_selection_context.candidates[0], question='Show relations and approve a purchase.')
    selected = replace(selected, candidate_selection_context=CandidateSelectionContext((candidate,)))
    value = candidate_payload(selected.question, ())
    value.update(disposition='unsupported', unresolved=[{'spans': [{'text': selected.question}],
        'reason': 'unsupported_task'}])
    result = execute(engine, selected, Planner(value), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_clarification' and result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.subqueries == 0 and not result.citations


def test_v4_without_candidate_context_refuses_without_a_planner(bom_store):
    _, engine = bom_store
    result = execute(engine, graph_request(), Planner(lambda *_a, **_k: pytest.fail('No context cannot plan')),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_invalid_plan' and result.diagnostics.budget.planners == 0
    assert result.observed_task_routing['candidate_context_sha256'] is None and not result.citations


def test_candidate_input_is_not_allowed_through_legacy_contract(bom_store):
    _, engine = bom_store
    with pytest.raises(ValueError, match='exact evaluation contract'):
        execute(engine, candidate_request(), Planner(''), version='semantic-graph-fixed-task-routing-v3-draft')


def test_candidate_contract_requires_production_authority_or_evaluation_reviewer(bom_store):
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    with pytest.raises(ValueError, match='source authority'):
        collect_rag_events(DefaultRagExecutor().run(candidate_request(),
            RagInvocation('production', task_routing_contract=CANDIDATE_TASK_ROUTING)))
    _, engine = bom_store
    selected = candidate_request()
    selected = replace(selected, access=replace(selected.access, roles=frozenset({'employee'})))
    with pytest.raises(PermissionError):
        execute(engine, selected, Planner(''), version=CANDIDATE_TASK_ROUTING)


@pytest.mark.parametrize('raw,code', [('{', 'json_syntax'), ('{"schema":1,"schema":2}', 'json_duplicate_key'),
    ('{"x":NaN}', 'json_nonfinite'), ('[]', 'envelope_shape'), ('x' * 65537, 'payload_shape')],
    ids=('syntax', 'duplicate-key', 'nonfinite', 'envelope', 'oversized'))
def test_strict_json_failures_are_static_and_never_persist_provider_text(bom_store, raw, code):
    _, engine = bom_store
    result = execute(engine, candidate_request(), Planner(raw), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_invalid_plan' and result.observed_task_routing_failure_code == code
    assert result.observed_task_routing['tasks'] == [] and result.citations == ()


def test_shared_ledger_keeps_start_deadline_and_one_planner_after_resolution(bom_store):
    _, engine = bom_store
    selected = mixed_request()
    observed = []
    def reply(_messages, **kwargs):
        ledger = kwargs['retry_counter']
        observed.append((ledger, ledger.started_monotonic, ledger.deadline_monotonic))
        return SimpleNamespace(content=json.dumps(candidate_payload(selected.question,
            (('graph', 'graph_contains'), ('bom', 'bom_rollup'), ('compare', 'compare_two_scopes')))))
    result = execute(engine, selected, Planner(reply), version=CANDIDATE_TASK_ROUTING)
    ledger, started, deadline = observed[0]
    assert ledger.started_monotonic == started and ledger.deadline_monotonic == deadline
    assert ledger.planners == 1 and ledger.subqueries == 3 and ledger.calculations == 5
    assert result.diagnostics.budget.provider_retries == 0


def test_cancellation_after_planner_prevents_all_actions(bom_store):
    import threading
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, consume_rag_events, RagCancelled
    _, engine = bom_store
    selected = candidate_request()
    cancellation = threading.Event()
    def reply(_messages, **_kwargs):
        cancellation.set()
        return SimpleNamespace(content=json.dumps(candidate_payload(selected.question)))
    with bind_repository_runtime(db_engine=engine):
        terminal = consume_rag_events(DefaultRagExecutor(provider_adapter=Planner(reply)).run(selected,
            RagInvocation('cancel-candidate', mode='evaluation', task_routing_contract=CANDIDATE_TASK_ROUTING), cancellation))
    assert isinstance(terminal, RagCancelled)


def test_original_deadline_expired_in_planner_is_not_reset_by_resolution(bom_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, consume_rag_events, RagFailed
    _, engine = bom_store
    selected = candidate_request()
    def reply(_messages, **kwargs):
        kwargs['retry_counter'].started_monotonic -= 1000
        return SimpleNamespace(content=json.dumps(candidate_payload(selected.question)))
    with bind_repository_runtime(db_engine=engine):
        terminal = consume_rag_events(DefaultRagExecutor(provider_adapter=Planner(reply)).run(selected,
            RagInvocation('deadline-candidate', mode='evaluation', task_routing_contract=CANDIDATE_TASK_ROUTING)))
    assert isinstance(terminal, RagFailed) and isinstance(terminal.cause, TimeoutError)
    assert terminal.diagnostics.budget.deadline_exceeded is True


@pytest.mark.parametrize('question', [
    '1. Show sum A for the selected pair\n2. Show sum B for the same pair\n3. Show their difference\n4. Show percentage change',
    '1. Xem tổng A\n2. Xem tổng B\n3. Xem chênh lệch\n4. Xem tỷ lệ thay đổi',
    '1. Compare the selected scopes and show sum A\n2. Compare the selected scopes and show sum B\n'
    '3. Compare the selected scopes and show difference\n4. Compare the selected scopes and show percentage',
    '1. So sánh hai phạm vi và cho tổng A\n2. So sánh hai phạm vi và cho tổng B\n'
    '3. So sánh hai phạm vi và cho chênh lệch\n4. So sánh hai phạm vi và cho tỷ lệ'])
def test_numbered_arithmetic_outputs_are_not_proof_of_independent_overflow(bom_store, question):
    _, engine = bom_store
    selected = candidate_request(question)
    selected = replace(selected, candidate_selection_context=CandidateSelectionContext((
        CandidateSelection('compare', scope_comparison=('quantity-scope', 'policy')),)))
    result = execute(engine, selected, Planner(candidate_payload(question, (('compare', 'compare_two_scopes'),))),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.calculations == 4


def test_four_required_caller_questions_overflow_before_planner(bom_store):
    _, engine = bom_store
    selected = candidate_request('Do the declared tasks.')
    context = CandidateSelectionContext(tuple(replace(candidate, question='Declared task ' + candidate.selection_id)
                                             for candidate in selected.candidate_selection_context.candidates))
    result = execute(engine, replace(selected, candidate_selection_context=context),
                     Planner(lambda *_a, **_k: pytest.fail('Four declared tasks cannot plan')), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'intent_overflow' and result.diagnostics.budget.planners == 0
    assert result.diagnostics.budget.subqueries == 0 and result.citations == ()


@pytest.mark.parametrize('fault,code', [
    ('schema', 'contract'), ('tasks-shape', 'payload_shape'), ('unresolved-shape', 'payload_shape'),
    ('duplicate-id', 'duplicate_task_ids'), ('unresolved-extra', 'unresolved_shape'),
    ('unresolved-reason', 'unresolved_reason'), ('ready-empty', 'ready_slot_order'),
    ('ready-four', 'ready_slot_order'), ('overflow-one', 'overflow_count')])
def test_invalid_candidate_envelopes_are_refused_before_any_action(bom_store, fault, code):
    _, engine = bom_store
    selected = candidate_request()
    value = candidate_payload(selected.question)
    if fault == 'schema': value['schema'] = 'semantic-graph-fixed-task-routing-v3-draft'
    elif fault == 'tasks-shape': value['tasks'] = {}
    elif fault == 'unresolved-shape': value['unresolved'] = {}
    elif fault == 'duplicate-id': value['tasks'].append({**value['tasks'][0], 'selection_id': 'second'})
    elif fault == 'unresolved-extra': value['unresolved'] = [{'spans': [], 'reason': 'ambiguous_reference', 'secret': 'x'}]
    elif fault == 'unresolved-reason': value['unresolved'] = [{'spans': [{'text': selected.question}], 'reason': 'guessed'}]
    elif fault == 'ready-empty': value['tasks'] = []
    elif fault == 'ready-four': value = candidate_payload(selected.question, tuple((key, 'graph_contains') for key in ('first', 'second', 'third', 'fourth')))
    else: value['disposition'] = 'overflow'
    result = execute(engine, selected, Planner(value), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_invalid_plan' and result.observed_task_routing_failure_code == code
    assert result.diagnostics.budget.subqueries == 0 and not result.citations
    assert result.observed_task_routing['tasks'] == []


def test_same_candidate_cannot_be_invented_as_two_independent_tasks(bom_store):
    _, engine = bom_store
    selected = candidate_request()
    value = candidate_payload(selected.question)
    value['tasks'].append({**value['tasks'][0], 'task_id': 'second-task'})
    result = execute(engine, selected, Planner(value), version=CANDIDATE_TASK_ROUTING)
    assert result.refusal_reason == 'task_clarification' and result.diagnostics.budget.subqueries == 0


def test_empty_question_is_unsupported_before_any_provider_call(bom_store):
    _, engine = bom_store
    result = execute(engine, candidate_request(''), Planner(lambda *_a, **_k: pytest.fail('Empty question')),
                     version=CANDIDATE_TASK_ROUTING)
    assert result.observed_task_routing['disposition'] == 'unsupported' and result.diagnostics.budget.planners == 0


def test_reviewer_guard_applies_even_without_math_observation_capture(bom_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    _, engine = bom_store
    selected = candidate_request()
    selected = replace(selected, access=replace(selected.access, roles=frozenset({'employee'})))
    with bind_repository_runtime(db_engine=engine), pytest.raises(PermissionError):
        collect_rag_events(DefaultRagExecutor(provider_adapter=Planner('')).run(selected,
            RagInvocation('guard-candidate', mode='evaluation', task_routing_contract=CANDIDATE_TASK_ROUTING)))


def test_planner_receives_actual_current_part_reference_context(bom_store):
    _, engine = bom_store
    selected = replace(candidate_request(), current_part_ids=('PART-A', 'PART-B'),
                       history=({'role': 'user', 'content': 'Use PART-A.'},),
                       conversation_context={'assembly': 'document:1'})
    planner = Planner(candidate_payload(selected.question))
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    prompt = json.loads(planner.calls[0][0][1]['content'])
    assert prompt['current_part_ids'] == ['PART-A', 'PART-B']
    assert prompt['history'] == [{'role': 'user', 'content': 'Use PART-A.'}]
    assert prompt['conversation_context'] == {'assembly': 'document:1'}
    assert result.outcome == 'answered'


def test_context_changed_at_snapshot_open_cannot_reuse_child_receipt(bom_store):
    from contextlib import contextmanager
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, consume_rag_events, RagFailed

    _, engine = bom_store
    history = {'role': 'user', 'content': 'Original source context'}
    selected = replace(candidate_request(), history=(history,))

    class ChangedContextEngine:
        @contextmanager
        def connect(self):
            history['content'] = 'Changed source context'
            with engine.connect() as conn:
                yield conn

    with bind_repository_runtime(db_engine=ChangedContextEngine()):
        terminal = consume_rag_events(DefaultRagExecutor(provider_adapter=Planner(candidate_payload(selected.question))).run(
            selected, RagInvocation('stale-child', mode='evaluation', task_routing_contract=CANDIDATE_TASK_ROUTING)))
    assert isinstance(terminal, RagFailed) and isinstance(terminal.cause, ValueError)
    assert terminal.diagnostics.budget.graph_examined_relations == terminal.diagnostics.budget.calculations == 0


def test_context_changed_after_child_binding_refuses_before_graph_read(bom_store):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events, current_request_budget

    _, engine = bom_store
    history = {'role': 'user', 'content': 'Original source context'}
    selected = replace(candidate_request(), history=(history,))

    class ConcurrentContextChange:
        checkpoints = 0

        def is_set(self):
            budget = current_request_budget()
            if budget is not None and budget.subqueries == 1:
                self.checkpoints += 1
                if self.checkpoints == 2:
                    history['content'] = 'Changed source context'
            return False

    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor(provider_adapter=Planner(candidate_payload(selected.question))).run(
            selected, RagInvocation('stale-child-guard', mode='evaluation', task_routing_contract=CANDIDATE_TASK_ROUTING),
            ConcurrentContextChange()))
    assert result.outcome == 'refused' and result.citations == ()
    assert result.diagnostics['query_v2']['branches'][0]['reason'] == 'task_binding_mismatch'
    assert result.diagnostics.budget.graph_examined_relations == result.diagnostics.budget.calculations == 0
