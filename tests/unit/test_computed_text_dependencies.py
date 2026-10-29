"""Fixed computation is request-local evidence for dependent text, never another calculation."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import re

import pytest
from sqlalchemy import text
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from .test_rag_scope_comparison import approved_pair, request, store
from .test_rag_pipeline_public_characterization import offline_pipeline, _document
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events


def _run_dependency(store, offline_pipeline, monkeypatch, *, fault='', independent=False):
    from mech_chatbot.rag import pipeline_steps
    approved_pair(store, values=('1000', '1200'))
    with store.begin() as conn:
        conn.execute(text('ALTER TABLE TaiLieu ADD COLUMN ExternalProcessingPolicy TEXT'))
        conn.execute(text("UPDATE TaiLieu SET ExternalProcessingPolicy='all_external'"))
        if fault == 'policy':
            conn.execute(text("UPDATE TaiLieu SET ExternalProcessingPolicy='internal_only'"))
        if fault == 'missing':
            conn.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='b'"))
    docs, _, _, runtime = offline_pipeline
    docs.append(_document('The difference between two comparable totals is B minus A. '
                          'A percentage change compares that difference to the first total.' +
                          (' The independent guide describes USD exports.' if fault == 'unit_cross_branch' else '') +
                          (' The independent guide describes kg measurements.' if fault == 'measurement_cross_branch' else '') +
                          (' The policy threshold is 999 VND.' if fault == 'guide_number' else '')))
    docs[0] = type(docs[0])(page_content=docs[0].page_content,
                           metadata={**docs[0].metadata,'external_processing_policy':'all_external'})
    if fault in {'independent_number', 'missing_guide'}:
        guide = _document('The policy threshold is 999 VND. The comparison method checks comparable amounts.')
        guide = type(guide)(page_content=guide.page_content, metadata={**guide.metadata,'doc_id':18,
                                  'external_processing_policy':'all_external'})
        docs.append(guide)
        class Store:
            def __init__(self, **_kwargs): pass
            def similarity_search(self, query, **_kwargs):
                return [guide] if 'describe' in query.casefold() else [] if fault == 'missing_guide' else [docs[0]]
        monkeypatch.setattr('langchain_qdrant.QdrantVectorStore',Store)
    prompts, audits = [], []
    def generate(prompt, **kwargs):
        assert kwargs['timeout'] > 0
        prompts.append(str(prompt))
        locators = list(dict.fromkeys(re.findall(r'D\d+L[0-9A-Fa-f]+', prompts[-1])))
        sources = ' '.join(f'[SRC:{locator}]' for locator in locators)
        if fault == 'locator':
            sources = '[SRC:D1L' + '0' * 16 + ']'
        if fault == 'revoke':
            with store.begin() as conn:
                conn.execute(text('UPDATE TaiLieu SET Servable=0 WHERE DocID=1'))
        if fault in {'missing', 'denied_dependency', 'policy'}:
            return AIMessage(content='The comparison method matches comparable totals. [SRC:D17P2]')
        value = '999' if fault in {'number', 'premise', 'guide_number'} else '1000' if fault == 'mislabeled_total' else '200'
        answer = f'The difference is {value} VND; the change is 20%. {sources} [SRC:D17P2]'
        if fault in {'unit', 'unit_cross_branch'}:
            answer = answer.replace('VND', 'USD')
        if fault == 'measurement_cross_branch':
            answer = answer.replace('VND', 'kg')
        if not fault or fault in {'revoke', 'independent_number', 'missing_guide'}:
            answer = f'The difference follows the approved delta step, subtracting Total A from Total B. {sources} [SRC:D17P2]'
        if fault in {'independent_number', 'missing_guide'}:
            answer = (answer + '\n' if fault == 'independent_number' else '') + '"The policy threshold is 999 VND." [SRC:D18P2]'
        return AIMessage(content=answer)
    def audit(**kwargs):
        audits.append(kwargs)
        return nullcontext()
    monkeypatch.setattr(pipeline_steps, 'audited_external_call', audit)
    provider = SimpleNamespace(client=RunnableLambda(generate),
        settings=SimpleNamespace(model_name='local-fixture', base_url='http://127.0.0.1/fixture'),
        invoke=lambda *_a, **_kw: SimpleNamespace(content=''))
    original = request()
    branches = [QueryBranch('cost', 'Compare the chosen scopes', scope_comparison=('a', 'b')),
                QueryBranch('explain', 'Explain the computed difference' + ('; is 999 correct?' if fault == 'premise' else ''),
                            text_query=True, depends_on=('cost',))]
    if independent:
        branches.append(QueryBranch('guide', 'Describe the comparison method', text_query=True))
    selected = replace(original, scope_comparison=None, response_language='en',
        access=replace(original.access, allowed_departments=frozenset({'ENG', 'HR'}),
                       allowed_sites=frozenset({'factory', 'HCM'})),
        question='Compare the chosen scopes; explain the computed difference.', query_plan=QueryPlan(tuple(branches)))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor(provider_adapter=provider, retrieval_adapter=runtime).run(
            selected, RagInvocation('computed-dependency', mode='test', capture_math_observations=True)))
    return result, prompts, audits


def test_computed_values_locators_and_source_policy_reach_one_generation(store, offline_pipeline, monkeypatch):
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch)
    assert len(prompts) == len(audits) == 1
    assert all(value in prompts[0] for value in ('1000', '1200', '200', '20'))
    branches = result.diagnostics['query_v2']['branches']
    assert [branch['status'] for branch in branches] == ['complete', 'complete']
    assert any('L' in card['source_id'] for card in branches[1]['sources'])
    assert set(audits[0]['doc_ids']) == {1, 17}
    assert audits[0]['policies'] == ['all_external'] * len(audits[0]['doc_ids'])
    assert result.diagnostics.budget.calculations == 4 and result.diagnostics.budget.subqueries == 2
    assert result.diagnostics.budget.final_generations == 1


@pytest.mark.parametrize('fault', ['number', 'locator', 'unit', 'premise'])
def test_wrong_computed_value_or_locator_is_withheld_but_fixed_math_survives(store, offline_pipeline, monkeypatch, fault):
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch, fault=fault)
    assert len(prompts) == 1 and len(audits) == 1
    assert result.diagnostics['query_v2']['branches'][0]['status'] == 'complete'
    assert result.diagnostics['query_v2']['branches'][1]['status'] == 'unknown'
    assert '999' not in result.answer and 'D1L0000000000000000' not in result.answer
    assert '200.00 VND' in result.answer
    assert result.diagnostics.budget.repairs == 0


def test_independent_text_branch_does_not_inherit_computation_sources(store, offline_pipeline, monkeypatch):
    result, _, _ = _run_dependency(store, offline_pipeline, monkeypatch, independent=True)
    branches = result.diagnostics['query_v2']['branches']
    assert branches[1]['status'] == branches[2]['status'] == 'complete'
    assert all('L' not in card['source_id'] for card in branches[2]['sources'])
    assert result.diagnostics.budget.calculations == 4 and result.diagnostics.budget.subqueries == 3


def test_dependency_recheck_failure_prevents_generation_and_preserves_fixed_math(store, offline_pipeline, monkeypatch):
    from mech_chatbot.services import rag_evidence_service
    def reject(*_args):
        raise PermissionError('Dependency source unavailable')
    monkeypatch.setattr(rag_evidence_service, 'dependency_source_attributes', reject)
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch)
    assert prompts == audits == []
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete', 'unknown']
    assert '200.00 VND' in result.answer


def test_external_policy_denial_stops_model_and_preserves_fixed_math(store, offline_pipeline, monkeypatch):
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch, fault='policy')
    assert prompts == audits == []
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete', 'unknown']
    assert '200.00 VND' in result.answer


def test_missing_fixed_scope_blocks_dependent_text_but_runs_independent_branch(store, offline_pipeline, monkeypatch):
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch, fault='missing', independent=True)
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['unknown', 'blocked', 'complete']
    assert len(prompts) == len(audits) == 1
    assert '<verified_computation' not in prompts[0]
    assert set(audits[0]['doc_ids']) == {17}


def test_revoked_dependency_after_model_is_withheld(store, offline_pipeline, monkeypatch):
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch, fault='revoke')
    assert len(prompts) == len(audits) == 1
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete', 'unknown']
    assert 'The difference is 200 VND' not in result.answer


def test_dependency_context_does_not_enter_legacy_external_verifier(store, offline_pipeline, monkeypatch):
    from mech_chatbot.rag import evidence_gate
    offline_pipeline[3].evidence_verifier_enabled = True
    verification_calls = []
    monkeypatch.setattr(evidence_gate, 'cohere_invoke',
                        lambda *_a, **kwargs: verification_calls.append(kwargs) or SimpleNamespace(content='{}'))
    result, prompts, _ = _run_dependency(store, offline_pipeline, monkeypatch)
    assert verification_calls == [] and len(prompts) == 1
    assert result.diagnostics['query_v2']['branches'][1]['status'] == 'complete'


def test_dependency_numbers_are_held_even_when_legacy_strict_streaming_is_disabled(store, offline_pipeline, monkeypatch):
    offline_pipeline[3].strict_answer_mode = False
    offline_pipeline[3].strict_realtime_streaming = True
    result, prompts, _ = _run_dependency(store, offline_pipeline, monkeypatch, fault='number')
    assert len(prompts) == 1 and '999' not in result.answer
    assert result.diagnostics['query_v2']['branches'][1]['status'] == 'unknown'


def test_dependency_request_bypasses_semantic_cache_and_records_unsupported_repair(store, offline_pipeline, monkeypatch):
    from mech_chatbot.rag import semantic_cache
    runtime = offline_pipeline[3]
    runtime.semantic_cache_enabled = runtime.claim_repair_enabled = True
    cache_calls = []
    monkeypatch.setattr(semantic_cache, 'lookup', lambda *_a, **_kw: cache_calls.append('lookup'))
    monkeypatch.setattr(semantic_cache, 'teeing_store_stream', lambda *_a, **_kw: cache_calls.append('store'))
    result, prompts, _ = _run_dependency(store, offline_pipeline, monkeypatch, fault='number')
    assert len(prompts) == 1 and cache_calls == []
    metrics = result.diagnostics['generation_metrics']
    assert metrics['claim_repair_skipped_reason'] == 'computed_dependency_locator'
    assert result.diagnostics.budget.repairs == 0 and '999' not in result.answer


@pytest.mark.parametrize('fault, guide, wrong_unit', [('unit_cross_branch','USD exports','USD'),
                                                    ('measurement_cross_branch','kg measurements','kg')])
def test_independent_guide_cannot_authorize_converting_computed_unit(store, offline_pipeline, monkeypatch, fault, guide, wrong_unit):
    result, prompts, _ = _run_dependency(store, offline_pipeline, monkeypatch, fault=fault, independent=True)
    assert len(prompts) == 1 and guide in prompts[0]
    assert result.diagnostics['query_v2']['branches'][1]['status'] == 'unknown'
    assert f'The difference is 200 {wrong_unit}' not in result.answer


def test_denied_dependency_is_removed_before_independent_text_generation(store, offline_pipeline, monkeypatch):
    from mech_chatbot.services import rag_evidence_service
    def reject(*_args):
        raise PermissionError('Dependency source unavailable')
    monkeypatch.setattr(rag_evidence_service, 'dependency_source_attributes', reject)
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch, fault='denied_dependency', independent=True)
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete', 'unknown', 'complete']
    assert len(prompts) == len(audits) == 1
    assert '<verified_computation' not in prompts[0] and '1000' not in prompts[0] and '1200' not in prompts[0]
    assert set(audits[0]['doc_ids']) == {17}
    assert result.diagnostics.budget.final_generations == 1 and result.diagnostics.budget.calculations == 4


@pytest.mark.parametrize('fault', ['guide_number', 'mislabeled_total'])
def test_model_cannot_replace_backend_delta_with_unrelated_or_mislabeled_value(store, offline_pipeline, monkeypatch, fault):
    result, prompts, _ = _run_dependency(store, offline_pipeline, monkeypatch, fault=fault)
    assert len(prompts) == 1
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete', 'unknown']
    assert 'The difference is' not in result.answer
    assert [step['display_value'] for step in result.diagnostics['query_v2']['branches'][0]['math']['steps']] == [
        '1000.00', '1200.00', '200.00', '20.00']


def test_independent_unique_source_can_still_support_numeric_text(store, offline_pipeline, monkeypatch):
    result, prompts, _ = _run_dependency(store, offline_pipeline, monkeypatch, fault='independent_number', independent=True)
    assert len(prompts) == 1
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete','complete','complete']
    assert 'The policy threshold is 999 VND' in result.answer
    assert result.diagnostics.budget.calculations == 4 and result.diagnostics.budget.final_generations == 1


def test_missing_guide_does_not_receive_computed_authority_or_block_independent_text(store, offline_pipeline, monkeypatch):
    result, prompts, _ = _run_dependency(store, offline_pipeline, monkeypatch, fault='missing_guide', independent=True)
    assert len(prompts) == 1 and '<verified_computation' not in prompts[0]
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete','unknown','complete']
    assert 'The policy threshold is 999 VND' in result.answer


def test_sql_external_denial_excludes_only_dependent_branch(store, offline_pipeline, monkeypatch):
    result, prompts, audits = _run_dependency(store, offline_pipeline, monkeypatch, fault='policy', independent=True)
    assert len(prompts) == len(audits) == 1 and set(audits[0]['doc_ids']) == {17}
    assert '<verified_computation' not in prompts[0] and '1200' not in prompts[0]
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']] == ['complete','unknown','complete']
