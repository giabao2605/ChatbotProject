from types import SimpleNamespace

import pytest

from mech_chatbot.rag import intent
from tests.unit.rag.test_pipeline_steps_orchestration import steps

pytestmark = pytest.mark.unit


def test_intent_payload_contains_question_but_not_actor_or_pinned_document_data():
    calls = []
    def provider(messages, **kwargs):
        calls.append(messages[0].content)
        return SimpleNamespace(content='{}')
    intent.extract_search_intent('CURRENT_QUESTION', current_part_ids=['PINNED_PRIVATE_LABEL'],
        user_department='PRIVATE_DEPARTMENT', allowed_sites=['PRIVATE_SITE'],
        invoke_provider=provider)
    assert len(calls) == 1 and 'CURRENT_QUESTION' in calls[0]
    assert all(value not in calls[0] for value in
        ['PINNED_PRIVATE_LABEL','PRIVATE_DEPARTMENT','PRIVATE_SITE'])


@pytest.mark.parametrize('pinned_field', ['current_part_ids','active_doc_refs'])
def test_context_payload_only_contains_user_turns_and_document_labels(pinned_field):
    calls = []
    def provider(messages, **kwargs):
        calls.append(messages[0].content)
        return SimpleNamespace(content='{"context_action":"continue","standalone_question":"CURRENT_QUESTION"}')
    labels = ['PINNED_CODE', {'base_code':'KNOWN_CODE', 'file_goc':'KNOWN_FILE.pdf', 'label':'KNOWN_LABEL',
        'summary':'SUMMARY_SECRET', 'text':'DOC_CONTENT_SECRET',
        'metadata':{'file_goc':'NESTED_SECRET'}, 'title':{'content':'OBJECT_SECRET'}},
        {'base_code':{'text':'CODE_OBJECT_SECRET'}}, SimpleNamespace(label='REF_OBJECT_SECRET')]
    history = [
        {'role':'user','content':'USER_TURN'},
        {'role':'Khach','content':'CUSTOMER_TURN'},
        {'role':'assistant','content':'BOT_SECRET'},
        {'role':'tool','content':'TOOL_SECRET'},
        {'role':'system','content':'SYSTEM_SECRET'},
        {'role':'user','content':{'text':'USER_OBJECT_SECRET'}},
    ]
    intent.analyze_context('CURRENT_QUESTION', chat_history=history, invoke_provider=provider,
        **{pinned_field:labels})
    assert len(calls) == 1
    prompt = calls[0]
    for label in ['CURRENT_QUESTION','USER_TURN','CUSTOMER_TURN','PINNED_CODE','KNOWN_CODE','KNOWN_FILE.pdf','KNOWN_LABEL']:
        assert label in prompt
    for secret in ['BOT_SECRET','TOOL_SECRET','SYSTEM_SECRET','SUMMARY_SECRET','DOC_CONTENT_SECRET',
        'NESTED_SECRET','OBJECT_SECRET','CODE_OBJECT_SECRET','REF_OBJECT_SECRET','USER_OBJECT_SECRET']:
        assert secret not in prompt


@pytest.mark.parametrize('surface', ['intent_routing','query_disambiguation'])
def test_question_scope_is_entered_in_provider_thread_without_clearing_documents(monkeypatch, surface):
    from contextlib import contextmanager
    from contextvars import ContextVar
    from threading import get_ident
    from mech_chatbot.llm import external_ai

    active = ContextVar('test_question_scope', default=False)
    calls = []
    @contextmanager
    def question_scope():
        token = active.set(True)
        try:
            yield
        finally:
            active.reset(token)
    monkeypatch.setattr(intent, 'question_only_processing_context', question_scope, raising=False)
    def provider(messages, **kwargs):
        calls.append((active.get(),get_ident(),external_ai._DOC_IDS.get(),
            external_ai._SECURITY_LEVELS.get(),external_ai._POLICIES.get()))
        return SimpleNamespace(content='{}')
    main_thread = get_ident()
    with external_ai.external_document_context(doc_ids=[12], security_levels=['confidential'], policies=['internal_only']):
        if surface == 'intent_routing':
            intent.extract_search_intent('Current question?', invoke_provider=provider)
        else:
            intent.analyze_context('Current question?', chat_history=[{'role':'user','content':'Earlier question?'}],
                current_part_ids=['LABEL'], invoke_provider=provider)
        assert external_ai._DOC_IDS.get() == (12,)
    assert len(calls) == 1
    marked, thread, docs, levels, policies = calls[0]
    assert marked is True and thread != main_thread
    assert docs == (12,) and levels == ('confidential',) and policies == ('internal_only',)
    assert active.get() is False


@pytest.mark.parametrize('surface', ['intent_routing','query_disambiguation'])
@pytest.mark.parametrize('document_scope', [False,True])
def test_safe_provider_payload_obeys_real_external_policy(surface, document_scope):
    from contextlib import nullcontext
    from datetime import datetime, timedelta
    from mech_chatbot.config.settings import ExternalAiSettings
    from mech_chatbot.llm import external_ai

    profile = external_ai.ExternalAIProviderProfile(provider='openrouter',
        endpoint='https://openrouter.ai/api/v1', default_model='test-model',
        secret_reference='env:TEST_API_KEY', allowed_surfaces=(surface,),
        retention_mode='test-retention', policy_version='test-v1', approved_by='owner',
        risk_acceptance_ref='test:approved', review_expires_at=datetime.now()+timedelta(days=1))
    calls, allowed, denied = [], [], []
    def provider(messages, **kwargs):
        calls.append(kwargs['surface'])
        try:
            allowed.append(external_ai.make_external_call_spec(provider='openrouter',model='test-model',
                surface=kwargs['surface'],profile=profile,
                settings=ExternalAiSettings(application_environment='production',local_development=False,processing_policy='internal_only'),
                input_chars=len(messages[0].content)))
        except external_ai.ExternalProcessingDenied:
            denied.append(True)
            raise
        return SimpleNamespace(content='{}')
    context = external_ai.external_document_context(policies=['internal_only']) if document_scope else nullcontext()
    with context:
        if surface == 'intent_routing':
            intent.extract_search_intent('Current question?',invoke_provider=provider)
        else:
            intent.analyze_context('Current question?',chat_history=[{'role':'user','content':'Earlier question?'}],
                current_part_ids=['LABEL'],invoke_provider=provider)
    assert calls == [surface]
    if document_scope:
        assert denied == [True] and allowed == []
    else:
        assert denied == [] and len(allowed) == 1
        assert allowed[0].policy_exception_reason == 'question-only payload, owner decision 2026-11-05'
        assert allowed[0].doc_ids == () and allowed[0].security_levels == ()


def test_selected_candidate_description_stays_local_to_retrieval(monkeypatch, steps):
    import time
    question = 'chọn 1'
    candidate = {'key':'candidate-one','product_name':'PRIVATE_PRODUCT',
                 'dimensions':'PRIVATE_DIMENSIONS','materials':'PRIVATE_MATERIAL'}
    monkeypatch.setattr(steps, 'analyze_context', lambda *a, **k:
        {'context_action':'continue','standalone_question':question,'llm_resolved':False})
    calls = []
    def provider(messages, **kwargs):
        calls.append((messages[0].content,kwargs['surface']))
        return SimpleNamespace(content='{}')
    result = steps._rewrite_and_anchor(user_question=question,chat_history=[],current_part_ids=[],
        conversation_context={'pending_candidates':[candidate]},user_department='HR',
        user_roles=['viewer'],allowed_departments=['HR'],max_security_level='internal',
        allowed_sites=['HQ'],trace_id='candidate-payload',t_intent=time.time(),invoke_provider=provider,
        runtime=SimpleNamespace(conversation_state_enabled=True))
    assert result[0] == 'PRIVATE_PRODUCT PRIVATE_DIMENSIONS PRIVATE_MATERIAL'
    assert len(calls) == 1 and calls[0][1] == 'intent_routing'
    assert question in calls[0][0]
    assert all(value not in calls[0][0] for value in candidate.values())
