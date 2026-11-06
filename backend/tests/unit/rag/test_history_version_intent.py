import concurrent.futures
import json
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient, models

from mech_chatbot.rag import intent

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(('question', 'policy'), [
    ('Quy định cũ về nghỉ phép khác hiện nay thế nào?', 'compare_versions'),
    ('Theo quy định trước đây, mức hưởng bảo hiểm là bao nhiêu?', 'include_archived'),
    ('So sánh quy định năm 2014 và năm 2024 về bảo hiểm.', 'compare_versions'),
    ('AB-120 trước đây yêu cầu dung sai nào?', 'include_archived'),
    ('Tiêu chuẩn hiện hành về dung sai là gì?', 'current_only'),
])
def test_every_document_query_uses_one_existing_intent_call(question, policy):
    calls = []
    def provider(messages, **kwargs):
        calls.append((messages[0].content, kwargs))
        return SimpleNamespace(content=json.dumps({'version_policy':policy}))
    *_, data = intent.extract_search_intent(question, invoke_provider=provider,
        user_roles=['viewer','document_history_reader'], trace_id='history-test')
    assert data['version_policy'] == policy
    assert len(calls) == 1
    assert calls[0][1] == {'surface':'intent_routing','trace_id':'history-test'}
    prompt = calls[0][0]
    assert 'văn bản độc lập' in prompt and 'hết hiệu lực' in prompt
    assert 'không phải' in prompt and 'năm' in prompt


@pytest.mark.parametrize('question', ['Xin chào', 'Cảm ơn bạn'])
def test_pure_chitchat_never_calls_intent_provider(question):
    calls = []
    def provider(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{}')
    *_, data = intent.extract_search_intent(question, invoke_provider=provider)
    assert calls == [] and data['is_chitchat'] is True


@pytest.mark.parametrize('raw', ['bad json', '[]', '{"version_policy":"unknown"}',
    '{"version_policy":["include_archived"]}',
    '{"version_policy":"include_archived","product_names":null}'])
def test_bad_intent_payload_cannot_retain_history_policy(raw):
    *_, data = intent.extract_search_intent('model Alpha trước đây',
        user_roles=['document_history_reader'],
        invoke_provider=lambda *a, **k:SimpleNamespace(content=raw))
    assert data['version_policy'] == 'current_only'


@pytest.mark.parametrize('error', [RuntimeError('provider unavailable'), concurrent.futures.TimeoutError()])
def test_provider_failure_is_current_only(error):
    def provider(*args, **kwargs):
        raise error
    *_, data = intent.extract_search_intent('quy định trước đây về bảo hiểm',
        user_roles=['document_history_reader'], invoke_provider=provider)
    assert data['version_policy'] == 'current_only'


@pytest.mark.parametrize('roles', [None, [], ['admin'], ['viewer']])
def test_history_requires_explicit_capability_even_when_roles_are_empty(roles):
    strict, _, *_, data = intent.extract_search_intent('model Alpha trước đây',
        user_roles=roles, invoke_provider=lambda *a, **k:SimpleNamespace(
            content='{"version_policy":"include_archived"}'))
    assert data['history_access_denied'] is True
    assert data['version_policy'] == 'current_only'
    assert '-2147483648' in strict.model_dump_json()


def test_explicit_version_keeps_deterministic_priority_and_regex_code():
    calls = []
    def provider(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{"version_policy":"include_archived"}')
    *_, data = intent.extract_search_intent('AB-120 v2', user_roles=['document_history_reader'],
        invoke_provider=provider)
    assert data['version_policy'] == 'specific_version' and data['detected_versions'] == [2]
    assert data['base_codes'] == ['AB-120'] and len(calls) == 1


@pytest.mark.parametrize('policy', ['include_archived','compare_versions'])
def test_history_filters_admit_standalone_expired_and_current_documents_without_widening_acl(policy):
    strict, _, *_, data = intent.extract_search_intent('quy định trước đây về bảo hiểm',
        user_department='HR', user_roles=['viewer','document_history_reader'],
        allowed_departments=['HR'], allowed_sites=['HQ'], max_security_level='internal',
        invoke_provider=lambda *a, **k:SimpleNamespace(content=json.dumps({'version_policy':policy})))
    metadata = dict(servable=True,publication_state='published',lifecycle_status='published',
        review_status='approved',is_current=True,effective_status='effective',
        phong_ban_quyen='HR',site='HQ',security_level='internal')
    client = QdrantClient(':memory:')
    try:
        client.create_collection('history',vectors_config=models.VectorParams(size=2,distance=models.Distance.COSINE))
        cases = [{**metadata,'expiry_date':'2020-01-01'},metadata,
            {**metadata,'site':'OTHER'}, {**metadata,'phong_ban_quyen':'Technical'},
            {**metadata,'security_level':'confidential'}, {**metadata,'servable':False}]
        client.upsert('history',points=[models.PointStruct(id=i,vector=[1.,0.],payload={'metadata':m})
            for i,m in enumerate(cases,1)])
        found, _ = client.scroll('history',scroll_filter=strict)
        assert {p.id for p in found} == {1,2}
    finally:
        client.close()


def test_explicit_version_has_priority_over_all_model_keywords():
    *_, data = intent.extract_search_intent('AB-120 v2, so sánh các model',
        user_roles=['document_history_reader'], invoke_provider=lambda *a, **k:
            SimpleNamespace(content='{"version_policy":"all_current_variants"}'))
    assert data['version_policy'] == 'specific_version'
    assert data['detected_versions'] == [2]


def test_model_keywords_do_not_override_llm_time_period_comparison():
    *_, data = intent.extract_search_intent('So sánh các model trước đây với hiện nay',
        user_roles=['document_history_reader'], invoke_provider=lambda *a, **k:
            SimpleNamespace(content='{"version_policy":"compare_versions"}'))
    assert data['version_policy'] == 'compare_versions'
