"""Assessment history retains only exact, currently authorized source cards."""
import json

import pytest

from mech_chatbot.application.chat_result_history import serialize_history_result, restore_history_result


def test_assessment_roundtrip_rejects_changed_revision_and_never_stores_cursor():
    cards = [{'source_id': 'D1P1', 'doc_id': 1, 'version_no': 2}]
    assessment = {'verification_status': 'partial', 'answer_outcome': 'partial_answer',
        'continuation_cursor': 'private-cursor', 'source_groups': [
            {**cards[0], 'related_texts': ['Fact'], 'claims': []}]}
    payload = serialize_history_result({'answer_assessment': assessment}, cards)
    assert 'cursor' not in payload
    restored = restore_history_result(payload, cards)['answer_assessment']
    assert restored['verification_status'] == 'partial'
    assert restored['source_groups'][0]['related_texts'] == ['Fact']
    with pytest.raises(ValueError, match='Source group unavailable'):
        restore_history_result(payload, [{**cards[0], 'version_no': 3}])
    with pytest.raises(ValueError, match='Source group unavailable'):
        restore_history_result(payload, [])


def test_legacy_assessment_does_not_acquire_verified_label():
    payload = serialize_history_result({'answer_assessment': {'answer_outcome': 'clarification_required'}}, [])
    assert 'verification_status' not in restore_history_result(payload, [])['answer_assessment']
    assert json.loads(payload)['kind'] == 'answer'


def test_history_decorator_exposes_assessment_and_hides_it_after_revocation(monkeypatch):
    from mech_chatbot.api.routers import chat
    cards = [{'source_id': 'D1P1', 'doc_id': 1, 'version_no': 2, 'page_no': 1, 'file_name': 'source.pdf'}]
    payload = serialize_history_result({'answer_assessment': {'verification_status': 'verified',
        'answer_outcome': 'full_answer', 'source_groups': [{**cards[0], 'related_texts': ['Fact']}]}}, cards)
    monkeypatch.setattr(chat, '_sources_for_chat_ids', lambda ids, runtime: {1: cards})
    message = {'role': 'assistant', 'chat_id': 1, 'content': 'Fact [SRC:D1P1]', '_advanced_rag_payload': payload}
    answer = chat._decorate_history_messages([message], None)[0]
    assert answer['verification_status'] == 'verified'
    assert answer['source_groups'][0]['related_texts'] == ['Fact']
    assert '_advanced_rag_payload' not in answer and 'advanced_rag' not in answer
    revoked = chat._decorate_history_messages([{**message, 'sources_redacted': True}], None)[0]
    assert 'verification_status' not in revoked and 'source_groups' not in revoked
