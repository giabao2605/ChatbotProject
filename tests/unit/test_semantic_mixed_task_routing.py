"""Evaluation mixed routing executes authorized fixed and text slots once."""
from dataclasses import replace
import json
from types import SimpleNamespace
import pytest
from mech_chatbot.domain.task_routing import (
    CandidateSelection, CandidateSelectionContext, MIXED_CANDIDATE_TASK_ROUTING)
from .test_rag_bom_chat import bom_store, request, store, connection
from .test_rag_pipeline_public_characterization import offline_pipeline
from .test_semantic_graph_task_routing import execute, Planner


def mixed_request():
    original = request()
    return replace(original, bom_rollup=None, question='Calculate the BOM; payment terms; reconciliation guide.',
        candidate_selection_context=CandidateSelectionContext((
            CandidateSelection('bom', bom_rollup=original.bom_rollup),
            CandidateSelection('terms', text_query=True), CandidateSelection('guide', text_query=True))))


def mixed_payload():
    return {'schema': 'candidate-mixed-task-routing-quotes-v1', 'disposition': 'ready',
        'tasks': [{'task_id': key, 'selection_id': key, 'template': template,
                   'spans': [{'text': quote}], 'requested_outputs': outputs}
                  for key, template, quote, outputs in (
                      ('bom', 'bom_rollup', 'Calculate the BOM', ['totals_by_part']),
                      ('terms', 'text_query', 'payment terms', ['answer', 'citations']),
                      ('guide', 'text_query', 'reconciliation guide.', ['answer', 'citations']))],
        'unresolved': []}


def test_semantic_mixed_slots_derive_exact_questions_and_keep_shared_budget(bom_store, offline_pipeline):
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    planner = Planner(mixed_payload())
    result = execute(engine, mixed_request(), planner, version=MIXED_CANDIDATE_TASK_ROUTING,
                     retrieval_adapter=runtime)
    branches = result.diagnostics['query_v2']['branches']
    assert [branch['question'] for branch in branches] == [
        'Calculate the BOM', 'payment terms', 'reconciliation guide.']
    assert [branch['status'] for branch in branches] == ['complete', 'unknown', 'unknown']
    assert '6.00' in result.answer
    assert len(planner.calls) == result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.subqueries == 3 and result.diagnostics.budget.calculations == 1
    assert retrievals
    assert result.observed_task_routing['schema'] == 'mixed-candidate-task-routing-observation-v5-draft'


def test_v4_rejects_text_candidates_before_provider_or_sources(bom_store, offline_pipeline):
    from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    result = execute(engine, mixed_request(), Planner(lambda *_a, **_k: pytest.fail('V4 cannot plan text')),
                     version=CANDIDATE_TASK_ROUTING, retrieval_adapter=runtime)
    assert result.outcome == 'refused' and result.citations == ()
    assert retrievals == [] and result.diagnostics.budget.planners == 0
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


def test_v4_context_fingerprint_preserves_frozen_legacy_serialization():
    from .test_candidate_task_routing import candidate_request
    from mech_chatbot.rag.candidate_task_routing import candidate_context_sha256
    # Captured from the V4 payload fields before text_query was added to candidate serialization.
    assert candidate_context_sha256(candidate_request()) == 'a458f24893ce06a00071d498348d37bc7e9a622f71deb477e3fbbe723d51b9ba'


@pytest.mark.parametrize('fault', ['null', 'unknown', 'quote', 'duplicate', 'dependency'])
def test_invalid_mixed_selection_refuses_before_evidence(bom_store, offline_pipeline, fault):
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    selected, value = mixed_request(), mixed_payload()
    if fault == 'null': value['tasks'][1]['selection_id'] = None
    elif fault == 'unknown': value['tasks'][1]['selection_id'] = 'unknown'
    elif fault == 'quote': value['tasks'][1]['spans'][0]['text'] = 'invented quotation'
    elif fault == 'duplicate': value['tasks'][2]['selection_id'] = 'terms'
    else:
        candidates = selected.candidate_selection_context.candidates
        selected = replace(selected, candidate_selection_context=CandidateSelectionContext((
            replace(candidates[0], depends_on=('terms',)), *candidates[1:])))
        value['tasks'] = [value['tasks'][1], value['tasks'][0], value['tasks'][2]]
    result = execute(engine, selected, Planner(value), version=MIXED_CANDIDATE_TASK_ROUTING,
                     retrieval_adapter=runtime)
    assert result.outcome == 'refused' and result.citations == () and retrievals == []
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('obvious', [False, True])
def test_mixed_overflow_does_not_execute_any_branch(bom_store, offline_pipeline, obvious):
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    selected, value = mixed_request(), mixed_payload()
    if obvious:
        selected = replace(selected, question='Please do four independent tasks: BOM, terms, guide, warranty.')
        planner = Planner(lambda *_a, **_k: pytest.fail('Explicit overflow cannot plan'))
    else:
        extra = CandidateSelection('warranty', text_query=True)
        selected = replace(selected, candidate_selection_context=CandidateSelectionContext((
            *selected.candidate_selection_context.candidates, extra)))
        value['disposition'] = 'overflow'
        value['tasks'].append({**value['tasks'][1], 'task_id': 'warranty', 'selection_id': 'warranty'})
        planner = Planner(value)
    result = execute(engine, selected, planner, version=MIXED_CANDIDATE_TASK_ROUTING, retrieval_adapter=runtime)
    assert result.refusal_reason == 'intent_overflow' and retrievals == [] and not result.citations
    assert result.diagnostics.budget.planners == (0 if obvious else 1)
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('fault', ['duplicate', 'access', 'intents', 'question', 'flag', 'history'])
def test_bound_text_batch_tampering_cannot_retrieve(bom_store, offline_pipeline, monkeypatch, fault):
    from mech_chatbot.rag import pipeline
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    original_execute = pipeline.execute_pipeline
    def tamper(state):
        if state.task_slot == 'text-batch':
            if fault == 'duplicate':
                branch = state.task_binding.slots[1]
                from mech_chatbot.domain.query_plan import QueryBranch
                candidate = QueryBranch(branch[0], 'payment terms', text_query=True)
                altered = replace(state.request, question='payment terms; payment terms')
                state.task_binding.text_batch(altered, (candidate, candidate), ('payment terms', 'payment terms'))
            elif fault == 'access':
                state.request = replace(state.request, access=replace(state.request.access,
                    allowed_sites=frozenset({'unauthorized'})))
            elif fault == 'intents': state.explicit_text_intents = ('injected question',)
            elif fault == 'question': state.request = replace(state.request, question='injected question')
            elif fault == 'flag': state.request = replace(state.request, text_query=False)
            else: state.request = replace(state.request, history=({'role': 'user', 'content': 'changed'},))
        return original_execute(state)
    monkeypatch.setattr(pipeline, 'execute_pipeline', tamper)
    if fault == 'duplicate':
        with pytest.raises(ValueError, match='batch'):
            execute(engine, mixed_request(), Planner(mixed_payload()), version=MIXED_CANDIDATE_TASK_ROUTING,
                    retrieval_adapter=runtime)
    else:
        result = execute(engine, mixed_request(), Planner(mixed_payload()), version=MIXED_CANDIDATE_TASK_ROUTING,
                         retrieval_adapter=runtime)
        assert [b['status'] for b in result.diagnostics['query_v2']['branches']] == ['complete', 'unknown', 'unknown']
        assert result.diagnostics.budget.subqueries == 1
    assert retrievals == []


@pytest.mark.parametrize('cancel', [False, True])
def test_mixed_planner_cancellation_and_deadline_keep_original_request(bom_store, offline_pipeline, cancel):
    import threading
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, RagCancelled, RagFailed, consume_rag_events
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    cancellation = threading.Event()
    def reply(_messages, **kwargs):
        if cancel: cancellation.set()
        else: kwargs['retry_counter'].started_monotonic -= 1000
        return SimpleNamespace(content=json.dumps(mixed_payload()))
    with bind_repository_runtime(db_engine=engine):
        terminal = consume_rag_events(DefaultRagExecutor(provider_adapter=Planner(reply), retrieval_adapter=runtime).run(
            mixed_request(), RagInvocation('mixed-lifecycle', mode='test', task_routing_contract=MIXED_CANDIDATE_TASK_ROUTING),
            cancellation))
    assert isinstance(terminal, RagCancelled if cancel else RagFailed) and retrievals == []
    if not cancel: assert isinstance(terminal.cause, TimeoutError)


@pytest.mark.parametrize('question,allowed', [
    ('Calculate the total invoice amount for this month.', False),
    ('Tính tổng tiền hóa đơn tháng này.', False),
    ('Compute invoice amount 100 + 200 USD.', False),
    ('Calculate total invoice amount according to policy.', False),
    ('Calculate total invoice amount and explain the policy.', False),
    ('Explain the policy and calculate total invoice amount.', False),
    ('Sum invoice amounts this month.', False),
    ('Tổng tiền hóa đơn tháng này là bao nhiêu?', False),
    ('Giải thích công thức tính tổng tiền', True),
    ('Tra quy định tính lương', True), ('Tổng quan hướng dẫn', True), ('Explain formula', True)])
def test_clear_arithmetic_cannot_be_routed_as_top_k_text(bom_store, offline_pipeline, question, allowed):
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    selected = replace(mixed_request(), question=question,
        candidate_selection_context=CandidateSelectionContext((CandidateSelection('lookup', text_query=True),)))
    value = {'schema': 'candidate-mixed-task-routing-quotes-v1', 'disposition': 'ready', 'tasks': [
        {'task_id': 'lookup', 'selection_id': 'lookup', 'template': 'text_query',
         'spans': [{'text': question}], 'requested_outputs': ['answer', 'citations']}], 'unresolved': []}
    result = execute(engine, selected, Planner(value), version=MIXED_CANDIDATE_TASK_ROUTING, retrieval_adapter=runtime)
    assert result.diagnostics.budget.calculations == 0
    if allowed:
        assert retrievals and result.observed_task_routing['disposition'] == 'ready'
    else:
        assert retrievals == [] and not result.citations and result.refusal_reason == 'task_clarification'


def test_simple_fixed_candidate_still_avoids_planner_under_mixed_contract(bom_store):
    _, engine = bom_store
    original = request()
    selected = replace(original, bom_rollup=None, question='Calculate selected BOM.',
        candidate_selection_context=CandidateSelectionContext((CandidateSelection('bom', bom_rollup=original.bom_rollup),)))
    result = execute(engine, selected, Planner(lambda *_a, **_k: pytest.fail('Simple fixed command cannot plan')),
                     version=MIXED_CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and result.diagnostics.budget.planners == 0
    assert result.diagnostics['query_v2']['schema'] == 'query-plan-mixed-v1-draft'


def test_missing_math_keeps_independent_text_and_blocks_dependent_text(bom_store, offline_pipeline):
    from sqlalchemy import text
    database, engine = bom_store
    with database.begin() as conn:
        conn.execute(text('UPDATE ReviewedGraphOccurrences SET IsLeaf=0'))
    _, retrievals, _, runtime = offline_pipeline
    original = mixed_request()
    candidates = original.candidate_selection_context.candidates
    selected = replace(original, candidate_selection_context=CandidateSelectionContext((
        candidates[0], replace(candidates[1], depends_on=('bom',)), candidates[2])))
    result = execute(engine, selected, Planner(mixed_payload()), version=MIXED_CANDIDATE_TASK_ROUTING,
                     retrieval_adapter=runtime)
    assert [b['status'] for b in result.diagnostics['query_v2']['branches']] == ['unknown', 'blocked', 'unknown']
    assert result.diagnostics.budget.subqueries == 2 and result.diagnostics.budget.planners == 1
    assert retrievals


def test_mixed_remaining_subquery_budget_is_reserved_before_text_retrieval(bom_store, offline_pipeline):
    from mech_chatbot.rag.execution import RequestBudgetLimits
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    with pytest.raises(RuntimeError):
        execute(engine, mixed_request(), Planner(mixed_payload()), version=MIXED_CANDIDATE_TASK_ROUTING,
                retrieval_adapter=runtime, budget_limits=RequestBudgetLimits(subqueries=2))
    assert retrievals == []


def test_declared_text_question_cannot_hide_arithmetic_in_original_quote(bom_store, offline_pipeline):
    _, engine = bom_store
    _, retrievals, _, runtime = offline_pipeline
    question = 'Calculate the total invoice amount and explain the policy.'
    selected = replace(mixed_request(), question=question, candidate_selection_context=CandidateSelectionContext((
        CandidateSelection('lookup', text_query=True, question='Explain the policy.'),)))
    value = {'schema': 'candidate-mixed-task-routing-quotes-v1', 'disposition': 'ready', 'tasks': [
        {'task_id': 'lookup', 'selection_id': 'lookup', 'template': 'text_query',
         'spans': [{'text': question}], 'requested_outputs': ['answer', 'citations']}], 'unresolved': []}
    result = execute(engine, selected, Planner(value), version=MIXED_CANDIDATE_TASK_ROUTING, retrieval_adapter=runtime)
    assert result.refusal_reason == 'task_clarification' and retrievals == [] and not result.citations

