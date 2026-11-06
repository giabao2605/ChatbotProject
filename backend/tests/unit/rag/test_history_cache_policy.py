from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mech_chatbot.rag import semantic_cache
from mech_chatbot.rag.phases import preparation, routing, generation
from tests.unit.rag.test_rag_phase_policy_branches import _request, _state, _decision


@pytest.mark.parametrize('policy,denied', [('version_history', False), ('include_archived', False), ('compare_versions', False),
                                         ('specific_version', False), ('current_only', True), (None, False)])
def test_history_policy_never_reads_or_writes_current_cache(monkeypatch, policy, denied):
    from mech_chatbot.db.repositories import semantic_cache as repository
    exact, candidates, put = Mock(), Mock(), Mock()
    monkeypatch.setattr(repository, 'sc_get_exact', exact)
    monkeypatch.setattr(repository, 'sc_get_candidates', candidates)
    monkeypatch.setattr(repository, 'sc_put', put)
    guard = {'version_policy': policy, 'history_access_denied': denied}
    assert semantic_cache.lookup_exact('Quy định đào tạo?', 'scope', **guard) is None
    assert semantic_cache.lookup('Quy định đào tạo?', [1.0], 'scope', **guard) is None
    assert list(semantic_cache.teeing_store_stream(iter(['answer']), 'Quy định đào tạo?', [1.0],
        'scope', '', [], [1], 'model', citation_snapshot=[{'doc_id': 1}],
        evidence_snapshot=[{'doc_id': 1}], **guard)) == ['answer']
    exact.assert_not_called()
    candidates.assert_not_called()
    put.assert_not_called()


def test_prepare_never_returns_cache_before_intent(monkeypatch):
    state = _state()
    context = SimpleNamespace()
    monkeypatch.setattr(preparation, '_normalize_request', lambda _: context)
    for name in ['_log_start', '_safety_terminal', '_intent_overflow_terminal', '_missing_referent_terminal']:
        monkeypatch.setattr(preparation, name, lambda *args: None)
    exact = Mock(side_effect=AssertionError('cache before intent'))
    monkeypatch.setattr(preparation, '_lookup_exact_cache', exact)
    monkeypatch.setattr(preparation, '_complete_preparation', lambda *args: _request())
    assert preparation.prepare(state).prepared is not None
    exact.assert_not_called()


@pytest.mark.parametrize('policy,denied,expected', [('current_only', False, ['intent', 'exact']),
    ('historical', False, ['intent']), ('current_only', True, ['intent'])])
def test_routing_intent_precedes_cache_and_blocks_history(monkeypatch, policy, denied, expected):
    calls = []
    prepared = replace(_request(cache_eligible=True), user_question='Quy định đào tạo?', current_part_ids=())
    setup = routing._RouteSetup(lambda: iter([]), Mock(return_value=[1.0]))
    monkeypatch.setattr(routing, '_enter_route', lambda *args: setup)
    intent = {'version_policy': policy, 'history_access_denied': denied}
    rewrite = routing._RewriteDecision(prepared.user_question, (), False, False, intent, None, None, None, False)
    monkeypatch.setattr(routing, '_rewrite_request', lambda *args: calls.append('intent') or rewrite)
    monkeypatch.setattr(semantic_cache, 'enabled', lambda *args: True)
    monkeypatch.setattr(semantic_cache, 'scope_signature', lambda *args, **kwargs: 'scope')
    monkeypatch.setattr(semantic_cache, 'lookup_exact', lambda *args, **kwargs: calls.append('exact') or {'answer': 'cached'})
    monkeypatch.setattr(semantic_cache, 'lookup', Mock(side_effect=AssertionError('unexpected semantic lookup')))
    monkeypatch.setattr(routing, '_build_search_query', lambda *args: routing._SearchQuery('query', False))
    result = routing.route(prepared, _state())
    assert calls == expected
    if policy == 'current_only' and not denied:
        assert result.reason_code == 'exact_cache_hit'
        setup.embed_cached.assert_not_called()


def test_generation_does_not_wrap_history_stream_for_current_cache(monkeypatch):
    wrapper = Mock(side_effect=AssertionError('history cache write'))
    monkeypatch.setattr(semantic_cache, 'teeing_store_stream', wrapper)
    decision = _decision(intent_data={'version_policy': 'historical'}, cache_query_embedding=[1.0])
    stream = iter(['history'])
    result = generation._wrap_semantic_cache(stream, decision, SimpleNamespace(), [SimpleNamespace(metadata={})], [], [], _state())
    assert result is stream
    wrapper.assert_not_called()


@pytest.mark.parametrize('question', [
    'Giới hạn tăng ca thay đổi thế nào giữa quy định cũ và quy định thay thế?',
    'Điều kiện thời gian đóng để nhận lương hưu thay đổi thế nào qua các giai đoạn?',
    'Mức trả thấp nhất đã tăng bao nhiêu giữa tháng Bảy năm 2022 và tháng Bảy năm 2024?',
])
def test_llm_period_comparison_without_revision_numbers_routes_to_retrieval(monkeypatch, question):
    prepared = replace(_request(cache_eligible=True), user_question=question)
    intent = {'version_policy': 'compare_versions', 'detected_versions': []}
    rewrite = routing._RewriteDecision(question, (), False, False, intent, None, None, None, False)
    monkeypatch.setattr(routing, '_enter_route', lambda *args: routing._RouteSetup(lambda: iter([]), Mock()))
    monkeypatch.setattr(routing, '_rewrite_request', lambda *args: rewrite)
    monkeypatch.setattr(routing, '_build_search_query', lambda *args: routing._SearchQuery('query', False))
    monkeypatch.setattr(semantic_cache, 'lookup_exact', Mock(side_effect=AssertionError('history cache read')))
    monkeypatch.setattr(semantic_cache, 'lookup', Mock(side_effect=AssertionError('history cache read')))
    assert routing.route(prepared, _state()).decision.intent_data is intent


def test_semantic_hit_follows_one_intent_and_exact_miss(monkeypatch):
    calls = []
    prepared = replace(_request(cache_eligible=True), user_question='Quy định đào tạo?', current_part_ids=())
    intent = {'version_policy': 'current_only'}
    rewrite = routing._RewriteDecision(prepared.user_question, (), False, False, intent, None, None, None, False)
    monkeypatch.setattr(routing, '_enter_route', lambda *args: routing._RouteSetup(lambda: iter([]), lambda q: calls.append('embed') or [1.0]))
    monkeypatch.setattr(routing, '_rewrite_request', lambda *args: calls.append('intent') or rewrite)
    monkeypatch.setattr(semantic_cache, 'enabled', lambda *args: True)
    monkeypatch.setattr(semantic_cache, 'scope_signature', lambda *args, **kwargs: 'scope')
    monkeypatch.setattr(semantic_cache, 'lookup_exact', lambda *args, **kwargs: calls.append('exact'))
    monkeypatch.setattr(semantic_cache, 'lookup', lambda *args, **kwargs: calls.append('semantic') or {'answer': 'cached'})
    result = routing.route(prepared, _state())
    assert result.reason_code == 'semantic_cache_hit'
    assert calls == ['intent', 'exact', 'embed', 'semantic']
