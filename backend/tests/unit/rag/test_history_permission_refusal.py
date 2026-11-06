from dataclasses import replace
from unittest.mock import Mock

import pytest

from mech_chatbot.rag.phases import routing
from tests.unit.rag.test_rag_phase_policy_branches import _request, _state


@pytest.mark.parametrize('language,phrase', [('vi', 'quyền xem các phiên bản tài liệu cũ'),
                                           ('en', 'permission to view older document versions')])
@pytest.mark.parametrize('extra', [{}, {'clarification_required': True}, {'is_chitchat': True}])
def test_history_permission_denial_is_explicit_and_terminal(monkeypatch, language, phrase, extra):
    prepared = replace(_request(cache_eligible=True), response_language=language)
    intent = {'version_policy': 'current_only', 'history_access_denied': True, **extra}
    rewrite = routing._RewriteDecision(prepared.user_question, (), False, False, intent, None, None, None, False)
    monkeypatch.setattr(routing, '_enter_route', lambda *args: routing._RouteSetup(Mock(), Mock()))
    monkeypatch.setattr(routing, '_rewrite_request', lambda *args: rewrite)
    for name in ('_lookup_exact_cache', '_lookup_semantic_cache', '_build_search_query', '_build_route_decision'):
        monkeypatch.setattr(routing, name, Mock(side_effect=AssertionError('denied request continued')))
    state = _state()
    state.refuse = Mock()
    state.invoke_provider = Mock(side_effect=AssertionError('generation after denial'))
    result = routing.route(prepared, state)
    assert result.decision is None and result.reason_code == 'access_denied'
    answer, refs, images, _parts, debug = result.terminal
    assert phrase in ''.join(answer)
    assert refs == '' and images == ()
    assert debug['answer_outcome'] == 'access_denied'
    assert debug['access_hint']['reason'] == 'history_access_denied'
    assert debug['retrieved_docs'] == []
    state.refuse.assert_called_once_with('access_denied')
    state.invoke_provider.assert_not_called()
