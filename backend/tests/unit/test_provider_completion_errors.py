"""HTTP 200 provider failures never become successful text completions."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from mech_chatbot.llm import external_ai, llm_client
from mech_chatbot.rag.execution import (
    AccessScope, DefaultRagExecutor, RagCitation, RagCompleted, RagFailed,
    RagInvocation, RagRequest, RagToken,
)
from .test_provider_client_composition import _llm_settings


def adapter_with_completion(monkeypatch, requests, audits, *, finish_reason='error',
                            content='503 partial secret-provider-body'):
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            'id': 'fixture-response', 'object': 'chat.completion', 'created': 0,
            'model': 'fixture-model', 'choices': [{
                'index': 0, 'message': {'role': 'assistant', 'content': content},
                'finish_reason': finish_reason,
                'error': {'message': '503 secret-provider-error', 'code': 503},
            }], 'usage': {'prompt_tokens': 1, 'completion_tokens': 7, 'total_tokens': 8},
        })

    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(llm_client, 'ChatOpenAI',
                        lambda **kwargs: ChatOpenAI(**kwargs, http_client=client))
    monkeypatch.setattr(external_ai, '_load_managed_provider_profile', lambda provider: {
        'provider': provider, 'endpoint': 'https://openrouter.ai/api/v1',
        'default_model': 'fixture-model', 'secret_reference': 'env:FIXTURE_KEY',
        'allowed_surfaces': ['generation'], 'retention_mode': 'fixture',
        'policy_version': 'fixture-v1', 'approved_by': 'fixture',
        'risk_acceptance_ref': 'fixture', 'is_active': True,
        'review_expires_at': datetime.now() + timedelta(hours=1),
    })

    def record(spec, *, status, latency_ms, error_type=None):
        audits.append((status, error_type, spec.surface))
        return True

    monkeypatch.setattr(external_ai, '_record_external_call', record)
    settings = replace(_llm_settings(), base_url='https://openrouter.ai/api/v1')
    return llm_client.build_llm_adapter(settings, external_ai_settings=replace(
        external_ai.DEFAULT_EXTERNAL_AI_SETTINGS, processing_policy='all_external')), client


@pytest.mark.parametrize('method', ['invoke', 'invoke_once'])
@pytest.mark.parametrize('content', ['503 partial secret-provider-body', '{"answer":"503 secret"}'])
def test_provider_error_is_single_attempt_and_audited_as_failure(monkeypatch, method, content):
    requests, audits = [], []
    adapter, client = adapter_with_completion(monkeypatch, requests, audits, content=content)
    with client, pytest.raises(RuntimeError, match='provider reported a completion error') as error:
        getattr(adapter, method)([HumanMessage(content='fixture')])
    assert str(error.value) == 'provider reported a completion error'
    assert len(requests) == 1
    assert audits == [('started', None, 'generation'),
                      ('error', 'ProviderCompletionError', 'generation')]


@pytest.mark.parametrize('finish_reason', ['stop', 'length', 'tool_calls', None])
def test_other_finish_reasons_keep_content_and_success_audit(monkeypatch, finish_reason):
    requests, audits = [], []
    adapter, client = adapter_with_completion(monkeypatch, requests, audits,
        finish_reason=finish_reason, content='fixture answer')
    with client:
        response = adapter.invoke_once([HumanMessage(content='fixture')])
    assert response.content == 'fixture answer'
    assert response.response_metadata['finish_reason'] == finish_reason
    assert len(requests) == 1
    assert audits == [('started', None, 'generation'), ('success', None, 'generation')]


@pytest.mark.parametrize('response', [
    SimpleNamespace(content='fixture'),
    SimpleNamespace(content='fixture', response_metadata=None),
    SimpleNamespace(content='fixture', response_metadata='error'),
    SimpleNamespace(content='fixture', response_metadata={}),
])
def test_legacy_responses_without_dict_error_metadata_remain_unchanged(monkeypatch, response):
    requests, audits = [], []
    adapter, client = adapter_with_completion(monkeypatch, requests, audits)
    adapter = replace(adapter, client=SimpleNamespace(invoke=lambda *_, **__: response))
    with client:
        assert adapter.invoke_once([HumanMessage(content='fixture')]) is response
    assert audits == [('started', None, 'generation'), ('success', None, 'generation')]


@pytest.mark.parametrize('content', ['503 secret partial answer', '{"answer":"secret"}'])
def test_rag_executor_does_not_publish_content_from_failed_completion(monkeypatch, content):
    requests, audits = [], []
    adapter, client = adapter_with_completion(monkeypatch, requests, audits, content=content)

    def execute(state):
        response = adapter.invoke_once([HumanMessage(content='fixture')])
        return state.prepared((iter((response.content,)), '', (), (), {}))

    with client:
        events = list(DefaultRagExecutor(execute_pipeline=execute).run(
            RagRequest('fixture', AccessScope()), RagInvocation('provider-error', mode='test')))
    assert isinstance(events[-1], RagFailed)
    assert str(events[-1].cause) == 'provider reported a completion error'
    assert not any(isinstance(event, (RagToken, RagCitation, RagCompleted)) for event in events)
    assert len(requests) == 1
    assert audits[-1] == ('error', 'ProviderCompletionError', 'generation')
