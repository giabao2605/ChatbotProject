"""V4 sends a structural contract while retaining local semantic validation."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING
from .test_candidate_task_routing import candidate_request, candidate_payload
from .test_semantic_graph_task_routing import (
    bom_store, store, connection, Planner, execute, graph_request, payload,
)


def test_candidate_planner_sends_strict_schema_and_rejects_malformed_envelopes(bom_store):
    _, engine = bom_store
    selected = candidate_request()
    response = candidate_payload(selected.question)
    planner = Planner(response)
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and len(planner.calls) == 1
    response_format = planner.calls[0][1]['response_format']
    assert response_format['type'] == 'json_schema'
    assert response_format['json_schema']['strict'] is True
    schema = response_format['json_schema']['schema']
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(response)
    for invalid in ([], {key: value for key, value in response.items() if key != 'schema'},
                    {**response, 'extra': 'untrusted'}):
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    invalid = deepcopy(response)
    invalid['tasks'][0]['task_id'] = 'invalid task id'
    with pytest.raises(ValidationError):
        validator.validate(invalid)


@pytest.mark.parametrize('fault,expected', [('span_text', 'task_invalid_plan'),
                                           ('unknown_selection', 'task_clarification')])
def test_schema_valid_response_still_requires_semantic_validation(bom_store, fault, expected):
    _, engine = bom_store
    selected = candidate_request()
    response = candidate_payload(selected.question)
    if fault == 'span_text':
        response['tasks'][0]['spans'][0]['text'] = 'A different question'
    else:
        response['tasks'][0]['selection_id'] = 'unavailable'
    planner = Planner(response)
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    Draft202012Validator(planner.calls[0][1]['response_format']['json_schema']['schema']).validate(response)
    assert result.refusal_reason == expected and not result.citations
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('disposition', ['clarify', 'unsupported', 'overflow'])
def test_schema_permits_abstention_and_overflow_without_forcing_available_selections(bom_store, disposition):
    _, engine = bom_store
    selected = candidate_request()
    response = candidate_payload(selected.question, tuple((str(i), 'graph_contains') for i in range(4)))
    response = {**response, 'disposition': disposition,
                'tasks': response['tasks'] if disposition == 'overflow' else [],
                'unresolved': [] if disposition == 'overflow' else [{
                    'spans': [{'text': selected.question}],
                    'reason': 'ambiguous_reference' if disposition == 'clarify' else 'unsupported_task'}]}
    planner = Planner(response)
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    schema = planner.calls[0][1]['response_format']['json_schema']['schema']
    Draft202012Validator(schema).validate(response)
    assert result.observed_task_routing['disposition'] == disposition
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


def test_v3_provider_contract_is_unchanged(bom_store):
    _, engine = bom_store
    selected = graph_request()
    planner = Planner(payload(selected.question))
    result = execute(engine, selected, planner)
    assert result.outcome == 'answered' and len(planner.calls) == 1
    assert 'response_format' not in planner.calls[0][1]


@pytest.mark.parametrize('template,valid_output,foreign_output', [
    ('graph_where_used', 'relations', 'quantity_basis'),
    ('graph_contains', 'coverage', 'sum_a'),
    ('compare_two_scopes', 'delta', 'relations'),
    ('bom_rollup', 'quantity_basis', 'percent_change'),
])
def test_provider_schema_binds_requested_outputs_to_the_task_template(bom_store, template, valid_output, foreign_output):
    _, engine = bom_store
    selected = candidate_request()
    response = candidate_payload(selected.question)
    task = {**response['tasks'][0], 'template': template, 'requested_outputs': [foreign_output]}
    invalid = {**response, 'tasks': [task]}
    planner = Planner(invalid)
    result = execute(engine, selected, planner, version=CANDIDATE_TASK_ROUTING)
    validator = Draft202012Validator(planner.calls[0][1]['response_format']['json_schema']['schema'])
    with pytest.raises(ValidationError):
        validator.validate(invalid)
    validator.validate({**response, 'tasks': [{**task, 'requested_outputs': [valid_output]}]})
    assert result.refusal_reason == 'task_invalid_plan'
    assert result.diagnostics.budget.subqueries == result.diagnostics.budget.calculations == 0


def test_candidate_schema_reaches_real_sdk_http_transport_before_graph_execution(bom_store, monkeypatch):
    from contextlib import nullcontext
    from dataclasses import replace
    import json
    import httpx
    from langchain_openai import ChatOpenAI
    from mech_chatbot.llm import llm_client
    from .test_provider_client_composition import _llm_settings

    _, engine = bom_store
    selected, requests = candidate_request(), []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'id': 'fixture', 'object': 'chat.completion', 'created': 0,
            'model': 'fixture-model', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(candidate_payload(selected.question))}}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        monkeypatch.setattr(llm_client, 'ChatOpenAI', lambda **kwargs: ChatOpenAI(**kwargs, http_client=client))
        monkeypatch.setattr(llm_client, 'audited_external_call', lambda **_: nullcontext())
        adapter = llm_client.build_llm_adapter(replace(_llm_settings(), base_url='https://openrouter.ai/api/v1'))
        result = execute(engine, selected, adapter, version=CANDIDATE_TASK_ROUTING)
    assert result.outcome == 'answered' and result.citations and 'part:p' in result.answer
    assert len(requests) == result.diagnostics.budget.planners == 1
    body = json.loads(requests[0].content)
    assert body['response_format']['json_schema']['strict'] is True
    assert body['provider'] == {'require_parameters': True}
    assert 0 < requests[0].extensions['timeout']['read'] <= adapter.settings.timeout_seconds
