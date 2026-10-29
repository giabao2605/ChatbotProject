"""Structured response contracts reach HTTP without bypassing the adapter."""
from contextlib import nullcontext
from dataclasses import replace
import json

import pytest
from langchain_core.messages import HumanMessage
from openai import APIStatusError
from tenacity import RetryError

from mech_chatbot.llm import llm_client
from . import test_provider_request_deadline as transport
from .test_provider_client_composition import _llm_settings


def response_format():
    return {'type': 'json_schema', 'json_schema': {'name': 'fixture', 'strict': True,
        'schema': {'type': 'object', 'properties': {'answer': {'type': 'string'}},
                   'required': ['answer'], 'additionalProperties': False}}}


@pytest.mark.parametrize('method', ['invoke', 'invoke_once'])
@pytest.mark.parametrize('endpoint,is_openrouter', [
    ('https://openrouter.ai/api/v1', True), ('https://api.proxyllm.eu/v1', False),
])
def test_structured_schema_reaches_http_and_preserves_audit(monkeypatch, method, endpoint, is_openrouter):
    requests, audits = [], []
    monkeypatch.setattr(transport, '_llm_settings', lambda: replace(_llm_settings(), base_url=endpoint))
    adapter, client = transport.text_adapter(monkeypatch, requests)
    def audit(**kwargs):
        audits.append(kwargs)
        return nullcontext()
    monkeypatch.setattr(llm_client, 'audited_external_call', audit)
    schema = response_format()
    with client:
        getattr(adapter, method)([HumanMessage(content='fixture')], surface='query_decomposition',
                                trace_id='structured-fixture', response_format=schema)
    assert len(requests) == len(audits) == 1
    body = json.loads(requests[0].content)
    assert body['response_format'] == schema
    assert body.get('provider') == ({'require_parameters': True} if is_openrouter else None)
    assert requests[0].extensions['timeout']['read'] == 17.0
    assert audits[0]['surface'] == 'query_decomposition' and audits[0]['trace_id'] == 'structured-fixture'
    assert audits[0]['provider'] == ('openrouter' if is_openrouter else 'proxyllm')


@pytest.mark.parametrize('endpoint', ['https://openrouter.ai/api/v1', 'https://api.proxyllm.eu/v1'])
@pytest.mark.parametrize('method', ['invoke', 'invoke_once'])
def test_default_calls_do_not_add_format_or_provider_options(monkeypatch, endpoint, method):
    requests = []
    monkeypatch.setattr(transport, '_llm_settings', lambda: replace(_llm_settings(), base_url=endpoint))
    adapter, client = transport.text_adapter(monkeypatch, requests)
    with client:
        getattr(adapter, method)([HumanMessage(content='fixture')])
    body = json.loads(requests[0].content)
    assert 'response_format' not in body and 'provider' not in body


@pytest.mark.parametrize('status', [400, 429])
def test_structured_rejection_is_single_attempt_without_plain_text_fallback(monkeypatch, status):
    requests = []
    monkeypatch.setattr(transport, '_llm_settings', lambda: replace(
        _llm_settings(), base_url='https://openrouter.ai/api/v1'))
    adapter, client = transport.text_adapter(monkeypatch, requests, status=status)
    with client, pytest.raises(RetryError) as rejected:
        adapter.invoke_once([HumanMessage(content='fixture')], response_format=response_format())
    assert isinstance(rejected.value.last_attempt.exception(), APIStatusError)
    assert rejected.value.last_attempt.exception().status_code == status
    assert len(requests) == 1
    assert json.loads(requests[0].content)['provider'] == {'require_parameters': True}


def test_provider_cannot_mutate_caller_owned_schema(monkeypatch):
    requests = []
    adapter, client = transport.text_adapter(monkeypatch, requests)
    original_invoke = type(adapter.client).invoke
    def mutate_schema(self, messages, **kwargs):
        kwargs['response_format']['json_schema']['schema']['properties']['answer']['description'] = 'provider copy'
        return original_invoke(self, messages, **kwargs)
    monkeypatch.setattr(type(adapter.client), 'invoke', mutate_schema)
    schema = response_format()
    with client:
        adapter.invoke_once([HumanMessage(content='fixture')], response_format=schema)
    assert 'description' not in schema['json_schema']['schema']['properties']['answer']
    assert json.loads(requests[0].content)['response_format']['json_schema']['schema']['properties']['answer']['description'] == 'provider copy'


def test_structured_call_keeps_remaining_deadline_after_audit(monkeypatch):
    requests, clock = [], [100.0]
    monkeypatch.setattr('time.monotonic', lambda: clock[0])
    monkeypatch.setattr(llm_client, 'monotonic', lambda: clock[0])
    adapter, client = transport.text_adapter(monkeypatch, requests)
    def audit(**kwargs):
        clock[0] = 109.0
        return nullcontext()
    monkeypatch.setattr(llm_client, 'audited_external_call', audit)
    def execute(state):
        clock[0] = 108.0
        response = adapter.invoke_once([HumanMessage(content='fixture')], response_format=response_format())
        return state.prepared((iter((response.content,)), '', (), (), {}))
    with client:
        result = transport.collect_rag_events(transport.DefaultRagExecutor(execute_pipeline=execute,
            budget_limits=transport.RequestBudgetLimits(deadline_seconds=10)).run(
                transport.RagRequest('fixture', transport.AccessScope()),
                transport.RagInvocation('structured-deadline', mode='test')))
    assert result.outcome == 'answered' and len(requests) == 1
    assert requests[0].extensions['timeout']['read'] == 1.0
    assert 'timeout' not in json.loads(requests[0].content)
