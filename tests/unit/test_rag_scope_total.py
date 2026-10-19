from dataclasses import replace

import pytest
from sqlalchemy import text

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_rag_scope_comparison import approved_pair, approved_empty_pair, request
from .test_structured_evidence_repository import store


def selected(scope_id='a', **changes):
    return replace(request(), question='Tổng phạm vi đã chọn', scope_comparison=None, scope_total=scope_id, **changes)


def test_selected_scope_total_emits_one_step_and_real_source_without_planner(store):
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected(),
            RagInvocation('scope-total', mode='evaluation', capture_math_observations=True)))
    assert result.outcome == 'answered' and result.diagnostics.budget.calculations == 1
    assert result.diagnostics.budget.planners == result.diagnostics.budget.final_generations == 0
    record = result.observed_math_results[0]
    assert record['schema'] == 'grounded-calculation-scope-total-v1'
    assert record['steps'][0]['value'] == '4' and record['steps'][0]['display_value'] == '4.00'
    assert result.diagnostics['math_v2']['domain_step_count'] == 1
    assert result.diagnostics['math_v2']['contract'] == 'grounded-math-v2-scope-total-evaluation-v1'
    assert len(result.citations) == 1 and result.citations[0]['page_no'] is None
    assert result.citations[0]['locator']['cell'] == 'B2'
    assert '4.00 VND' in result.answer and '4 VND' in result.answer


@pytest.mark.parametrize('fault', ['rights', 'revision', 'pending', 'rows'])
def test_scope_total_denies_unavailable_source_without_number_or_citation(store, fault):
    approved_pair(store)
    chosen = selected()
    with store.begin() as conn:
        if fault == 'rights':
            chosen = replace(chosen, access=replace(chosen.access, allowed_sites=frozenset({'other'})))
        elif fault == 'revision': conn.execute(text('UPDATE TaiLieu SET VersionNo=3'))
        elif fault == 'pending': conn.execute(text("UPDATE StructuredNumericFact SET ReviewStatus='pending'"))
        else: conn.execute(text('UPDATE StructuredEvidenceScope SET CompletenessReviewed=0'))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(chosen,
            RagInvocation('blocked-total', mode='evaluation', capture_math_observations=True)))
    assert result.outcome == 'refused' and not result.citations
    assert result.diagnostics.budget.calculations == 0
    assert result.diagnostics['math_v2']['scopes'] == ()
    assert result.observed_math_results[0]['steps'] == result.observed_math_results[0]['sources'] == []


@pytest.mark.parametrize('mode', ['production', 'pilot_replay'])
def test_old_live_authority_cannot_enable_scope_total(mode, monkeypatch):
    from mech_chatbot.db.repositories import structured_evidence
    from mech_chatbot.governance.math_v2_activation import MathV2Capability, CONTRACT
    monkeypatch.setattr(structured_evidence, 'load_approved_scope', lambda *_: pytest.fail('source read before authority'))
    cap = MathV2Capability(CONTRACT, 'compare_two_scopes', (), 'candidate', 'b'*64, 'c'*64, '2099-01-01T00:00:00Z')
    executor = DefaultRagExecutor(math_v2_authorizer=lambda: cap)
    error = PermissionError if mode == 'production' else ValueError
    with pytest.raises(error, match='Scope total'):
        collect_rag_events(executor.run(selected(), RagInvocation('no-live-total', mode=mode)))


def test_scope_total_rejects_planner_contract_before_any_action():
    with pytest.raises(ValueError, match='Scope total does not use a planner'):
        collect_rag_events(DefaultRagExecutor().run(selected(),
            RagInvocation('no-total-planner', mode='evaluation', task_routing_contract='semantic-fixed-task-routing-v2-draft')))


def test_zero_total_keeps_reviewed_empty_source(store, tmp_path):
    original, scopes = approved_empty_pair(store, tmp_path)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected(scopes[0].scope_id),
            RagInvocation('empty-total', mode='evaluation', capture_math_observations=True)))
    assert result.outcome == 'answered' and '0.00 VND' in result.answer
    assert result.citations[0]['fact_count'] == 0 and result.citations[0]['locator']['cell'] == 'A1'


@pytest.mark.parametrize('value', ['', ' ', 'x'*256, False])
def test_scope_total_requires_one_bounded_identifier(value):
    with pytest.raises(ValueError): selected(value)


def test_scope_total_cannot_be_combined_with_another_action():
    with pytest.raises(ValueError): replace(request(), scope_total='a')


@pytest.mark.parametrize('fault', ['currentness', 'citation'])
def test_scope_total_rechecks_currentness_and_citation_binding(store, monkeypatch, fault):
    from mech_chatbot.rag import scope_total
    approved_pair(store)
    if fault == 'currentness': monkeypatch.setattr(scope_total, 'scope_is_current', lambda *_: False)
    else: monkeypatch.setattr(scope_total, 'build_citation_list', lambda *a, **kw: [])
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected(),
            RagInvocation('total-provenance', mode='evaluation', capture_math_observations=True)))
    assert result.outcome == 'refused' and not result.citations
    assert result.diagnostics.budget.calculations == 0 and result.diagnostics['math_v2']['scopes'] == ()


def test_scope_total_evaluation_still_requires_reviewer(store):
    approved_pair(store)
    chosen = selected()
    chosen = replace(chosen, access=replace(chosen.access, roles=frozenset({'user'})))
    with bind_repository_runtime(db_engine=store), pytest.raises(PermissionError, match='Evaluation reviewer required'):
        collect_rag_events(DefaultRagExecutor().run(chosen, RagInvocation('total-reader', mode='evaluation')))
