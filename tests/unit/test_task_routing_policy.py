"""Policy failures stop routing before selected evidence or external dispatch."""
from dataclasses import replace

import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.domain.task_routing import (
    CandidateSelection, CandidateSelectionContext,
    CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING,
)
from mech_chatbot.llm.external_ai import (
    ExternalAIComplianceAuditUnavailable, ExternalProcessingDenied,
)
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from mech_chatbot.rag.query_decomposition import (
    FIXED_TASK_ROUTING, GRAPH_TASK_ROUTING, SCOPE_TOTAL_TASK_ROUTING,
)
from .test_rag_scope_comparison import request, store
from .test_semantic_fixed_task_routing import Planner


@pytest.mark.parametrize('contract', [FIXED_TASK_ROUTING, GRAPH_TASK_ROUTING,
    SCOPE_TOTAL_TASK_ROUTING, CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING])
@pytest.mark.parametrize('language', ['vi', 'en'])
@pytest.mark.parametrize('error,reason,notice', [
    (ExternalProcessingDenied, 'external_processing_denied',
     {'vi': 'chính sách hiện tại chưa cho phép', 'en': 'current policy does not allow'}),
    (ExternalAIComplianceAuditUnavailable, 'external_ai_audit_unavailable',
     {'vi': 'nhật ký kiểm soát tạm thời không khả dụng', 'en': 'audit log is temporarily unavailable'}),
])
def test_routing_policy_failure_is_a_refusal_before_actions(store, contract, language, error, reason, notice):
    selected = replace(request(), question='Explain both selected totals and their sources.', response_language=language)
    if contract in (CANDIDATE_TASK_ROUTING, MIXED_CANDIDATE_TASK_ROUTING):
        context = CandidateSelectionContext((CandidateSelection('comparison', scope_comparison=selected.scope_comparison),))
        selected = replace(selected, scope_comparison=None, candidate_selection_context=context)
    elif contract == SCOPE_TOTAL_TASK_ROUTING:
        selected = replace(selected, scope_comparison=None, scope_total='selected-scope')

    def blocked(*_args, **_kwargs):
        raise error('PRIVATE POLICY / INTERNAL AUDIT STORAGE')

    planner = Planner(blocked)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor(provider_adapter=planner).run(selected,
            RagInvocation('policy-refusal', mode='test', task_routing_contract=contract)))

    assert result.outcome == 'refused'
    assert result.refusal_reason == reason
    assert notice[language] in result.answer
    assert 'PRIVATE' not in result.answer and 'INTERNAL' not in result.answer
    assert result.diagnostics['task_routing_blocked_reason'] == reason
    assert result.diagnostics.budget.planners == len(planner.calls) == 1
    assert result.diagnostics.budget.calculations == result.diagnostics.budget.subqueries == 0
    assert result.diagnostics.budget.provider_retries == 0
    assert result.citations == ()
