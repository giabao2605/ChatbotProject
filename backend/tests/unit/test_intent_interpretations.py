import json
from types import SimpleNamespace

import pytest
from mech_chatbot.rag import intent

pytestmark = pytest.mark.unit


def _extract(question, interpretations):
    calls = []
    def provider(messages, **kwargs):
        calls.append(kwargs['surface'])
        return SimpleNamespace(content=json.dumps({'interpretations': interpretations}, ensure_ascii=False))
    result = intent.extract_search_intent(question, invoke_provider=provider)[-1]
    assert calls == ['intent_routing']
    return result['interpretations']


def test_interpretations_parse_in_existing_intent_call():
    queries = ['Nghỉ phép năm cần thủ tục gì?', 'Nghỉ việc riêng cần thủ tục gì?', 'Thôi việc cần thủ tục gì?']
    assert _extract('Nghỉ cần thủ tục gì?', queries) == queries


@pytest.mark.parametrize('extra', ['Nghỉ cần báo trước 30 ngày?', 'Thủ tục nghỉ theo HR-123?',
    'Nghỉ theo v2 cần thủ tục gì?', 'Nghỉ tại site Hanoi cần thủ tục gì?',
    'Nghỉ ở phòng ban Nhân sự cần thủ tục gì?',
    'Nghỉ tại Hà Nội cần thủ tục gì?', 'Nghỉ của bộ phận Nhân sự cần thủ tục gì?',
    'Nghỉ theo revision A cần thủ tục gì?'])
def test_interpretations_reject_invented_constraints(extra):
    valid = ['Nghỉ phép năm cần thủ tục gì?', 'Nghỉ việc riêng cần thủ tục gì?']
    assert _extract('Nghỉ cần thủ tục gì?', valid + [extra]) == valid


def test_interpretations_bound_and_deduplicate():
    values = ['Nghỉ phép năm?', 'Nghỉ việc riêng?', 'Thôi việc?', 'Nghỉ việc không lương?']
    assert _extract('Nghỉ?', values) == values[:3]
    assert _extract('Nghỉ?', [values[0], values[0]]) == []
    assert _extract('Nghỉ?', 'Nghỉ phép năm?') == []


def test_interpretations_keep_existing_numbers():
    values = ['Nghỉ phép năm trong năm 2026?', 'Nghỉ việc riêng trong năm 2026?']
    assert _extract('Nghỉ trong năm 2026?', values) == values

from dataclasses import replace
from mech_chatbot.rag.phases.routing import _RewriteDecision, _activate_interpretations
from mech_chatbot.rag.phases.retrieval import _compile_plan
from mech_chatbot.rag.phases import generation


def _rewrite(**data):
    return _RewriteDecision(effective_question='Nghỉ cần thủ tục gì?', new_part_ids=(),
        is_inherited=False, is_bom_query=False, intent_data={
            'interpretations':['Nghỉ phép năm cần thủ tục gì?', 'Nghỉ việc riêng cần thủ tục gì?'], **data},
        strict_filter=None, broad_filter=None, rbac_filter=None, skip_hyde_anchor=False)


def test_interpretations_reuse_explicit_branch_plan():
    state = SimpleNamespace(explicit_text_intents=(), invocation=SimpleNamespace(task_routing_contract=None))
    result = _activate_interpretations(_rewrite(), state)
    plan = _compile_plan(SimpleNamespace(effective_question=result.effective_question, trace_id='explicit-intent-test'), state, {}).plan
    assert result.intent_data['interpretations_active']
    assert plan.subqueries == tuple(result.intent_data['interpretations'])
    assert plan.is_complex and len(plan.subqueries) == 2


@pytest.mark.parametrize('data', [{'base_codes':['HR-123']}, {'models':['Model A']},
    {'product_names':['Khung cơ khí']}, {'materials':['inox']}])
def test_interpretations_do_not_override_codes_or_mechanical_picker(data):
    state = SimpleNamespace(explicit_text_intents=(), invocation=SimpleNamespace(task_routing_contract=None))
    result = _activate_interpretations(_rewrite(**data), state)
    assert state.explicit_text_intents == ()
    assert not result.intent_data.get('interpretations_active')


def test_interpretations_preserve_contract_and_version_policy():
    state = SimpleNamespace(explicit_text_intents=(), invocation=SimpleNamespace(task_routing_contract='semantic-text-task-routing-v1'))
    assert _activate_interpretations(_rewrite(), state).intent_data.get('interpretations_active') is None
    state.invocation.task_routing_contract = None
    result = _activate_interpretations(_rewrite(version_policy='include_archived'), state)
    assert result.intent_data['version_policy'] == 'include_archived'


def test_interpretation_instruction_omits_unsupported_and_maps_branch_sources():
    branches = [{'subquery':'Nghỉ phép năm?', 'outcome':'full_answer', 'citations':[{'source_id':'S1'}]},
        {'subquery':'Nghỉ việc riêng?', 'outcome':'insufficient_evidence', 'citations':[]}]
    instruction = generation.interpretation_instruction(branches)
    assert 'Nghỉ phép năm?' in instruction and 'S1' in instruction
    assert 'Nghỉ việc riêng?' not in instruction
    assert 'tiêu đề' in instruction


def test_interpretation_footer_only_after_successful_answer():
    state = SimpleNamespace(generation_outcome=SimpleNamespace(refusal_reason=None))
    text = ''.join(generation._interpretation_stream(iter(['Trả lời có nguồn [S1].']), state))
    assert text.endswith(generation.AMBIGUITY_NOTICE)
    state.generation_outcome.refusal_reason = 'claim_verification_failed'
    assert ''.join(generation._interpretation_stream(iter(['Từ chối.']), state)) == 'Từ chối.'

from .test_pipeline_steps_orchestration import steps, _doc, _Lifecycle, _debug
from .test_rag_phase_feature_orchestration import _run_phase, _retrieval_adapter, _prepared_request


def test_interpretations_never_open_picker_from_retrieved_variants(steps):
    docs = [_doc('Synthetic approved content', domain='mechanical', base_code='P-1', variant_code='A'),
            _doc('Synthetic approved content', domain='mechanical', base_code='P-2', variant_code='B')]
    terminal, result = steps._disambiguate(retrieved_docs=docs, user_question='Nghỉ?', new_part_ids=[],
        intent_data={'interpretations_active': True}, response_language='vi', current_part_ids=[],
        trace_id='interpretation-picker', t_start=0, make_debug_info=_debug, lifecycle=_Lifecycle())
    assert terminal is None and result == docs


@pytest.mark.parametrize('supported_count', [0, 1, 2])
def test_missing_referent_routes_through_intent_and_separate_retrieval(monkeypatch, supported_count):
    import time
    from langchain_core.documents import Document
    from mech_chatbot.rag.phases import routing, retrieval, evidence
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.answer_policy import AnswerOutcome
    from mech_chatbot.rag.phases.contracts import PhaseTerminal

    question = 'Thiết bị này sử dụng thế nào?'
    queries = ['Thiết bị sử dụng để vận hành thế nào?', 'Thiết bị sử dụng để bảo trì thế nào?']
    calls = []
    retrieved = []
    monkeypatch.setattr(routing, '_enter_route', lambda *a: routing._RouteSetup(lambda: iter(()), lambda _: []))
    monkeypatch.setattr(routing, 'glossary_expansion_terms', lambda *a, **k: [])
    monkeypatch.setattr(retrieval, 'tokenize_cached', lambda q: q)
    def evaluate(question, context, *, docs, **kwargs):
        return EvidenceDecision(EvidenceState.SUFFICIENT if docs else EvidenceState.INSUFFICIENT,
                                reason='synthetic branch evidence' if docs else 'no evidence')
    monkeypatch.setattr(retrieval, 'evaluate_answerability', evaluate)
    monkeypatch.setattr(evidence, 'evaluate_answerability', evaluate)
    def invoke(messages, **kwargs):
        calls.append(kwargs['surface'])
        assert kwargs['surface'] == 'intent_routing'
        return SimpleNamespace(content=json.dumps({'interpretations':queries}, ensure_ascii=False))
    def retrieve(**kwargs):
        retrieved.append(kwargs['query_to_search'])
        index = queries.index(kwargs['query_to_search'])
        docs = [Document(page_content='Synthetic approved usage evidence.', metadata={
            'doc_id':index+1, 'trang_so':1, 'file_goc':'synthetic.pdf', 'version_no':1})] if index < supported_count else []
        return docs, 5, 'hybrid', time.time(), None
    def pipeline(state):
        decision = routing.route(_prepared_request(question=question), state).decision
        assert decision is not None and decision.intent_data['interpretations_active']
        primary = retrieval.retrieve_primary(decision, state)
        assert len(primary.decomposition_branches) == 2
        assert sum(b['outcome']=='full_answer' for b in primary.decomposition_branches) == supported_count
        policy, *_ = evidence._decide_evidence_policy(decision, 'Synthetic evidence.',
            list(primary.documents), list(primary.decomposition_branches), state)
        assert policy.allows_answer_generation == (supported_count > 0)
        if supported_count:
            monkeypatch.setattr(generation, '_store_conversation_document_refs', lambda *a, **k: None)
            monkeypatch.setattr(generation, '_store_history_summary', lambda *a: None)
            monkeypatch.setattr(generation, '_wrap_semantic_cache', lambda stream, *a: stream)
            stream = generation._decorate_generated_stream(iter(['### Vận hành\nSynthetic answer. [SRC:D1P1]']),
                decision, None, list(primary.documents), list(primary.decomposition_branches), {}, state)
            result = ''.join(stream)
            assert result.endswith(generation.AMBIGUITY_NOTICE)
            assert 'Trả lời được một phần' not in result and 'chưa có đủ' not in result
        return primary
    _run_phase(pipeline, retrieval=_retrieval_adapter(retrieve=retrieve, query_decomposition_enabled=False),
               provider=SimpleNamespace(invoke=invoke))
    assert calls == ['intent_routing'] and retrieved == queries

@pytest.mark.parametrize('accepted_ids', [(2,4), (2,), ()])
def test_verified_interpretations_restore_headings_without_sharing_claims(accepted_ids):
    from mech_chatbot.rag.interpretations import render_verified_interpretations
    from mech_chatbot.rag.claim_verification import verify_answer_claims
    from .test_claim_verification import document, claim, selected_reply
    draft = '### Vận hành thiết bị?\nFirst action. [SRC:D1P1]\n### Bảo trì thiết bị?\nSecond action. [SRC:D1P1]'
    branches = [{'subquery':query, 'outcome':'full_answer', 'citations':[{'source_id':'D1P1'}]}
                for query in ('Vận hành thiết bị?', 'Bảo trì thiết bị?')]
    docs = [document('First action.\nSecond action.')]
    rows = [claim(index, quote='First action.' if index==2 else 'Second action.', subject='unknown')
            for index in accepted_ids]
    checked = verify_answer_claims(draft, docs, question='Thiết bị này thế nào?',
        invoke=lambda prompt, **_kwargs:selected_reply(prompt, {'claims':rows}))
    assert '###' not in checked.answer
    result = render_verified_interpretations(draft, checked, branches)
    assert ('### Vận hành thiết bị?' in result.answer) == (2 in accepted_ids)
    assert ('### Bảo trì thiết bị?' in result.answer) == (4 in accepted_ids)
    assert result.answer.count('First action.') == (1 if 2 in accepted_ids else 0)
    assert result.answer.count('Second action.') == (1 if 4 in accepted_ids else 0)
    assert (result.status == 'insufficient') == (not accepted_ids)


def test_verified_interpretations_reject_missing_heading_and_cross_branch_source():
    from mech_chatbot.rag.interpretations import render_verified_interpretations
    from mech_chatbot.rag.claim_verification import Claim, Result
    accepted = Claim('Approved claim. [SRC:D2P1]', ('D2P1',), (), '', '', '', '')
    checked = Result(accepted.text, 'verified', (accepted,), ())
    branches = [{'subquery':'Vận hành?', 'outcome':'full_answer', 'citations':[{'source_id':'D1P1'}]}]
    for draft in (accepted.text, '### Vận hành?\n'+accepted.text):
        result = render_verified_interpretations(draft, checked, branches)
        assert result.status == 'insufficient' and result.claims == ()


def test_identical_claim_under_two_headings_is_not_copied_after_one_rejection():
    from mech_chatbot.rag.interpretations import render_verified_interpretations
    from mech_chatbot.rag.claim_verification import Claim, Result
    accepted = Claim('Approved claim. [SRC:D1P1]', ('D1P1',), (), '', '', '', '')
    checked = Result(accepted.text, 'partial', (accepted,), ())
    branches = [{'subquery':q, 'outcome':'full_answer', 'citations':[{'source_id':'D1P1'}]} for q in ('A?', 'B?')]
    draft = '### A?\n'+accepted.text+'\n### B?\n'+accepted.text
    result = render_verified_interpretations(draft, checked, branches)
    assert result.status == 'insufficient' and result.claims == ()


@pytest.mark.parametrize('question,queries', [
    ('Tôi nghỉ thì được nhận bao nhiêu tiền?', ['Tôi nghỉ phép năm thì được nhận bao nhiêu tiền?', 'Tôi thôi việc thì được nhận bao nhiêu tiền?']),
    ('Máy rung ở mức nào thì vẫn an toàn?', ['Mức rung của máy khi vận hành thế nào thì an toàn?', 'Mức rung của máy tác động lên người thế nào thì an toàn?']),
    ('Bao lâu phải bảo dưỡng thiết bị này một lần?', ['Bao lâu phải bảo dưỡng thiết bị trong vận hành một lần?', 'Bao lâu phải bảo dưỡng thiết bị khi lưu kho một lần?']),
    ('Bao giờ phải hoàn thành nâng cấp hệ thống? Với nguồn đã hết kỳ, nêu rõ kỳ thực hiện theo nội dung văn bản.',
     ['Bao giờ phải hoàn thành nâng cấp hệ thống theo kế hoạch? Với nguồn đã hết kỳ, nêu rõ kỳ thực hiện theo nội dung văn bản.',
      'Bao giờ phải hoàn thành nâng cấp hệ thống theo tiến độ thực tế? Với nguồn đã hết kỳ, nêu rõ kỳ thực hiện theo nội dung văn bản.']),
])
def test_corpus_9_15_16_26_interpretations_route_without_inventing_scope(question, queries):
    assert _extract(question, queries) == queries
    state = SimpleNamespace(explicit_text_intents=(), invocation=SimpleNamespace(task_routing_contract=None))
    result = _activate_interpretations(replace(_rewrite(interpretations=queries), effective_question=question), state)
    assert result.intent_data['interpretations_active'] and state.explicit_text_intents == tuple(queries)


@pytest.mark.parametrize("has_heading", [True, False])
def test_generation_verifier_keeps_branch_heading_and_original_question(steps, monkeypatch, has_heading):
    from .test_pipeline_steps_remaining_branches import _plan, _prepare_provider, _doc
    from .test_claim_verification import claim, selected_reply
    from langchain_core.runnables import RunnableLambda
    docs = [_doc('First action.', doc_id=1, trang_so=1)]
    question = 'Thiết bị này sử dụng thế nào? Khi trả lời, nêu rõ nguồn.'
    query = 'Thiết bị vận hành thế nào?'
    draft = (f'### {query}\n' if has_heading else '') + 'First action. [SRC:D1P1]'
    provider = _prepare_provider(steps, monkeypatch, [])
    plan = _plan(steps, provider=provider, docs=docs)
    prompts = []
    plan.runtime.provider_adapter.client = RunnableLambda(lambda prompt, **kw: prompts.append(str(prompt)) or draft)
    plan.runtime.provider_adapter.invoke = lambda messages, **kw: SimpleNamespace(content=selected_reply(
        messages[0].content, {'claims':[claim(2 if has_heading else 1, quote='First action.', subject='unknown')]}))
    plan = replace(plan, turn=replace(plan.turn, user_question=question, effective_question=query),
        evidence=replace(plan.evidence, context_text='First action.', intent_data={
            'interpretations_active':True, 'generation_original_question':True,
            'interpretation_branches':[{'subquery':query, 'outcome':'full_answer', 'citations':[{'source_id':'D1P1'}]}]}))
    metrics = {}
    output = ''.join(steps.generate_answer(plan, metrics=metrics))
    assert question in prompts[0]
    if has_heading:
        assert f'### {query}' in output and 'First action. [SRC:D1P1]' in output
        assert plan.control.outcome.refusal_reason is None
    else:
        assert plan.control.outcome.refusal_reason == 'unsupported_claims'
        state = SimpleNamespace(generation_outcome=plan.control.outcome)
        assert generation.AMBIGUITY_NOTICE not in ''.join(generation._interpretation_stream(iter([output]), state))


def test_interpretations_allow_generic_location_without_inventing_named_location():
    queries = ['Giới hạn rung cho phép tại nơi làm việc', 'Giới hạn rung cho phép của máy']
    assert _extract('Máy rung ở mức nào thì vẫn an toàn?', queries) == queries
    valid = ['Tiền lương khi nghỉ phép năm', 'Tiền lương khi nghỉ việc riêng']
    assert _extract('Tôi nghỉ thì được nhận bao nhiêu tiền?', valid + ['Tiền lương tại Hà Nội']) == valid


@pytest.mark.parametrize('question,blocked', [
    ('Model này có thông số gì?', True),
    ('Bao lâu phải bảo dưỡng thiết bị này một lần?', False),
])
def test_default_preparation_preserves_baseline_missing_referent_scope(question, blocked):
    from mech_chatbot.rag.phases.preparation import _missing_referent_terminal
    refused = []
    context = _prepared_request(question=question)
    state = SimpleNamespace(invocation=SimpleNamespace(task_routing_contract=None), refuse=refused.append)
    result = _missing_referent_terminal(context, state)
    if blocked:
        assert result is not None and result.reason_code == 'missing_referent'
        assert refused == ['clarification_required']
    else:
        assert result is None and refused == []


@pytest.mark.parametrize('values,raw_count,validated_count', [
    (['Nghỉ phép năm?', 'Nghỉ việc riêng?'], 2, 2),
    (['Nghỉ phép năm?', 'Nghỉ việc riêng?', 'Nghỉ theo HR-123?'], 3, 2),
    ([], 0, 0),
    ('PRIVATE_INVALID_VALUE', 0, 0),
])
def test_interpretation_count_trace_contains_only_metadata(monkeypatch, values, raw_count, validated_count):
    events = []
    monkeypatch.setattr(intent, 'log_trace', lambda *args, **kwargs: events.append((args, kwargs)), raising=False)
    result = intent.extract_search_intent('Nghỉ?', trace_id='intent-count-test',
        invoke_provider=lambda *a, **k: SimpleNamespace(content=json.dumps({'interpretations': values}, ensure_ascii=False)))[-1]
    assert len(result['interpretations']) == validated_count
    assert events == [(('intent_interpretations', 'intent-count-test'), {
        'raw_interpretations_count': raw_count,
        'validated_interpretations_count': validated_count,
        'interpretations_extraction_status': 'parsed' if isinstance(values, list) else 'invalid_field',
    })]


def test_interpretation_count_trace_does_not_treat_provider_failure_as_empty_response(monkeypatch):
    events = []
    monkeypatch.setattr(intent, 'log_trace', lambda *args, **kwargs: events.append((args, kwargs)), raising=False)
    def unavailable(*args, **kwargs):
        raise TimeoutError('synthetic timeout')
    intent.extract_search_intent('Nghỉ?', trace_id='intent-timeout-test', invoke_provider=unavailable)
    assert events == [(('intent_interpretations', 'intent-timeout-test'), {
        'raw_interpretations_count': 0, 'validated_interpretations_count': 0,
        'interpretations_extraction_status': 'timeout',
    })]


@pytest.mark.parametrize('data,part_ids,explicit,contract,expected', [
    ({}, (), (), None, True),
    ({'base_codes': ['PRIVATE-CODE']}, (), (), None, False),
    ({'models': ['PRIVATE-MODEL']}, (), (), None, False),
    ({'interpretations': []}, (), (), None, False),
    ({}, ('PRIVATE-INHERITED',), (), None, False),
    ({}, (), ('PRIVATE-EXPLICIT',), None, False),
    ({}, (), (), 'semantic-text-task-routing-v1', False),
])
def test_interpretation_activation_trace_respects_routing_guards(monkeypatch, data, part_ids, explicit, contract, expected):
    from mech_chatbot.rag.phases import routing
    events = []
    monkeypatch.setattr(routing, 'log_trace', lambda *args, **kwargs: events.append((args, kwargs)))
    state = SimpleNamespace(trace_id='activation-test', explicit_text_intents=explicit,
                            invocation=SimpleNamespace(task_routing_contract=contract))
    rewrite = replace(_rewrite(**data), new_part_ids=part_ids)
    result = routing._activate_interpretations(rewrite, state)
    assert bool(result.intent_data.get('interpretations_active')) is expected
    assert events == [(('interpretation_activation', 'activation-test'), {
        'validated_interpretations_count': 0 if data.get('interpretations') == [] else 2,
        'interpretations_active': expected,
    })]


@pytest.mark.parametrize('payload,status', [({}, 'missing_field'), ([], 'error')])
def test_interpretation_missing_or_invalid_response_logs_zero_with_status(monkeypatch, payload, status):
    events = []
    monkeypatch.setattr(intent, 'log_trace', lambda *args, **kwargs: events.append((args, kwargs)))
    intent.extract_search_intent('Nghỉ?', trace_id='count-status',
        invoke_provider=lambda *a, **k: SimpleNamespace(content=json.dumps(payload)))
    assert events == [(('intent_interpretations', 'count-status'), {
        'raw_interpretations_count': 0, 'validated_interpretations_count': 0,
        'interpretations_extraction_status': status,
    })]


@pytest.mark.parametrize('reason', ['setup', 'history', 'version', 'chitchat'])
def test_early_routing_terminal_logs_inactive_interpretations_once(monkeypatch, reason):
    from mech_chatbot.rag.phases import routing
    events = []
    terminal = routing.RoutingOutcome(terminal=object(), reason_code='chitchat')
    state = SimpleNamespace(trace_id='early-terminal')
    monkeypatch.setattr(routing, 'log_trace', lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(routing, '_enter_route', lambda *a: terminal if reason == 'setup' else SimpleNamespace(mock_stream=None))
    monkeypatch.setattr(routing, '_rewrite_request', lambda *a: _rewrite(history_access_denied=reason == 'history', is_chitchat=reason == 'chitchat'))
    monkeypatch.setattr(routing, '_needs_version_clarification', lambda *a: reason == 'version')
    monkeypatch.setattr(routing, '_history_access_denied_terminal', lambda *a: terminal)
    monkeypatch.setattr(routing, '_missing_version_terminal', lambda *a: terminal)
    monkeypatch.setattr(routing, '_chitchat_terminal', lambda *a: terminal)
    assert routing.route(object(), state) is terminal
    activation = [event for event in events if event[0][0] == 'interpretation_activation']
    assert activation == [(('interpretation_activation', 'early-terminal'), {
        'validated_interpretations_count': 0 if reason == 'setup' else 2,
        'interpretations_active': False,
    })]
    if reason == 'setup':
        assert events[0] == (('intent_interpretations', 'early-terminal'), {
            'raw_interpretations_count': 0, 'validated_interpretations_count': 0,
            'interpretations_extraction_status': 'not_run',
        })
