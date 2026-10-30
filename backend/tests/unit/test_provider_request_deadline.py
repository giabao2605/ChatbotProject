"""Request deadlines reach the real text adapter's HTTP transport."""
from contextlib import nullcontext
import json
from threading import Event

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from mech_chatbot.llm import llm_client
from mech_chatbot.rag.execution import (
    AccessScope, DefaultRagExecutor, RagInvocation, RagRequest,
    RagCancelled, RagCompleted, RagFailed, RagPrepared, RequestBudgetLimits, collect_rag_events,
)
from .test_provider_client_composition import _llm_settings


def text_adapter(monkeypatch, requests, *, status=200):
    def respond(request):
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={'error': {'message': 'fixture rate limit',
                'type': 'rate_limit_error', 'code': 'rate_limit_exceeded'}})
        return httpx.Response(200, json={
            'id': 'fixture-response', 'object': 'chat.completion', 'created': 0,
            'model': 'fixture-model', 'choices': [{
                'index': 0, 'message': {'role': 'assistant', 'content': 'fixture'},
                'finish_reason': 'stop',
            }], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
        })
    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(llm_client, 'ChatOpenAI',
                        lambda **kwargs: ChatOpenAI(**kwargs, http_client=client))
    monkeypatch.setattr(llm_client, 'audited_external_call', lambda **_: nullcontext())
    return llm_client.build_llm_adapter(_llm_settings()), client


@pytest.mark.parametrize('deadline,method,expected', [
    (10, 'invoke_once', 2.0), (10, 'invoke', 2.0), (40, 'invoke_once', 17.0),
])
def test_text_http_timeout_uses_remaining_request_deadline(monkeypatch, deadline, method, expected):
    requests, clock = [], [100.0]
    monkeypatch.setattr('time.monotonic', lambda: clock[0])
    monkeypatch.setattr(llm_client, 'monotonic', lambda: clock[0])
    adapter, client = text_adapter(monkeypatch, requests)

    def execute(state):
        clock[0] = 108.0
        response = getattr(adapter, method)([HumanMessage(content='fixture')])
        return state.prepared((iter((response.content,)), '', (), (), {}))

    with client:
        result = collect_rag_events(DefaultRagExecutor(execute_pipeline=execute,
            budget_limits=RequestBudgetLimits(deadline_seconds=deadline)).run(
                RagRequest('fixture', AccessScope()), RagInvocation('provider-deadline', mode='test')))

    assert result.outcome == 'answered' and result.answer == 'fixture'
    assert len(requests) == 1
    assert requests[0].extensions['timeout'] == {
        'connect': expected, 'read': expected, 'write': expected, 'pool': expected,
    }
    assert 'timeout' not in json.loads(requests[0].content)
    assert adapter.settings.timeout_seconds == adapter.client.request_timeout == 17.0


def test_calls_outside_a_rag_request_keep_the_configured_timeout(monkeypatch):
    requests = []
    adapter, client = text_adapter(monkeypatch, requests)
    with client:
        assert adapter.invoke_once([HumanMessage(content='fixture')]).content == 'fixture'
    assert requests[0].extensions['timeout']['read'] == 17.0


def test_multiple_provider_calls_share_the_remaining_time(monkeypatch):
    requests, clock = [], [100.0]
    monkeypatch.setattr('time.monotonic', lambda: clock[0])
    monkeypatch.setattr(llm_client, 'monotonic', lambda: clock[0])
    adapter, client = text_adapter(monkeypatch, requests)

    def execute(state):
        clock[0] = 108.0
        adapter.invoke_once([HumanMessage(content='first')])
        clock[0] = 109.0
        response = adapter.invoke_once([HumanMessage(content='second')])
        return state.prepared((iter((response.content,)), '', (), (), {}))

    with client:
        result = collect_rag_events(DefaultRagExecutor(execute_pipeline=execute,
            budget_limits=RequestBudgetLimits(deadline_seconds=10)).run(
                RagRequest('fixture', AccessScope()), RagInvocation('shared-timeout', mode='test')))
    assert result.outcome == 'answered'
    assert [r.extensions['timeout']['read'] for r in requests] == [2.0, 1.0]
    assert adapter.client.request_timeout == 17.0


@pytest.mark.parametrize('interrupt', ['cancel', 'deadline'])
def test_provider_does_not_start_http_after_interrupt(monkeypatch, interrupt):
    requests, clock, cancellation = [], [100.0], Event()
    monkeypatch.setattr('time.monotonic', lambda: clock[0])
    monkeypatch.setattr(llm_client, 'monotonic', lambda: clock[0])
    adapter, client = text_adapter(monkeypatch, requests)

    def execute(state):
        if interrupt == 'cancel':
            cancellation.set()
        else:
            clock[0] = 110.0
        adapter.invoke_once([HumanMessage(content='fixture')])
        raise AssertionError('Provider must refuse before HTTP starts')

    with client:
        events = list(DefaultRagExecutor(execute_pipeline=execute,
            budget_limits=RequestBudgetLimits(deadline_seconds=10)).run(
                RagRequest('fixture', AccessScope()), RagInvocation('interrupt-provider', mode='test'), cancellation))
    assert requests == []
    assert all(isinstance(e, (RagPrepared, RagCancelled if interrupt == 'cancel' else RagFailed)) for e in events)
    assert isinstance(events[-1], RagCancelled if interrupt == 'cancel' else RagFailed)
    if interrupt == 'deadline':
        assert events[-1].diagnostics.budget.deadline_exceeded


def test_audit_time_does_not_reset_or_extend_the_transport_deadline(monkeypatch):
    requests, clock = [], [100.0]
    monkeypatch.setattr('time.monotonic', lambda: clock[0])
    monkeypatch.setattr(llm_client, 'monotonic', lambda: clock[0])
    adapter, client = text_adapter(monkeypatch, requests)

    def audited(**kwargs):
        clock[0] = 109.0
        return nullcontext()

    monkeypatch.setattr(llm_client, 'audited_external_call', audited)

    def execute(state):
        clock[0] = 108.0
        response = adapter.invoke_once([HumanMessage(content='fixture')])
        return state.prepared((iter((response.content,)), '', (), (), {}))

    with client:
        result = collect_rag_events(DefaultRagExecutor(execute_pipeline=execute,
            budget_limits=RequestBudgetLimits(deadline_seconds=10)).run(
                RagRequest('fixture', AccessScope()), RagInvocation('audit-timeout', mode='test')))
    assert result.outcome == 'answered'
    assert requests[0].extensions['timeout']['read'] == 1.0


def test_single_attempt_adapter_does_not_retry_an_http_rate_limit(monkeypatch):
    requests = []
    adapter, client = text_adapter(monkeypatch, requests, status=429)

    def execute(state):
        adapter.invoke_once([HumanMessage(content='fixture')], retry_counter=state.budget)
        raise AssertionError('One rejected attempt cannot yield a successful answer')

    with client:
        events = list(DefaultRagExecutor(execute_pipeline=execute,
            budget_limits=RequestBudgetLimits(provider_retries=0)).run(
                RagRequest('fixture', AccessScope()), RagInvocation('no-provider-retry', mode='test')))
    assert len(requests) == 1 and isinstance(events[-1], RagFailed)
    assert all(isinstance(e, (RagPrepared, RagFailed)) for e in events)
    assert events[-1].diagnostics.budget.provider_retries == 0


def test_deadline_expiring_between_checkpoint_and_http_keeps_timeout_diagnostics(monkeypatch):
    requests, clock = [], [100.0]
    monkeypatch.setattr('time.monotonic', lambda: clock[0])
    monkeypatch.setattr(llm_client, 'monotonic', lambda: clock[0] + 2.0)
    adapter, client = text_adapter(monkeypatch, requests)

    def execute(state):
        clock[0] = 108.0
        adapter.invoke_once([HumanMessage(content='fixture')])
        raise AssertionError('The transport cannot start with zero time remaining')

    with client:
        events = list(DefaultRagExecutor(execute_pipeline=execute,
            budget_limits=RequestBudgetLimits(deadline_seconds=10)).run(
                RagRequest('fixture', AccessScope()), RagInvocation('checkpoint-race', mode='test')))
    assert requests == [] and isinstance(events[-1], RagFailed)
    assert events[-1].diagnostics.budget.deadline_exceeded
    assert not any(isinstance(e, RagCompleted) for e in events)
