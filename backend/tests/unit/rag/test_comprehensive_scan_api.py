"""API/executor transport of committed scan progress, including deadline failure."""
import json

import pytest

from mech_chatbot.rag.execution import DefaultRagExecutor, RequestDeadlineExceeded
from tests.unit.api.test_rag_server_endpoints import rag_client, _replace_runtime, SERVICE_HEADERS


def test_quick_service_sends_final_assessment_without_internal_documents(rag_client):
    group = {'source_id': 'D1P1', 'doc_id': 1, 'version_no': 2, 'related_texts': ['Fact'], 'claims': []}
    def execute(state):
        assert state.request.search_mode == 'quick'
        def stream():
            prepared.diagnostics['generation_metrics'] = {'verification_status': 'partial', 'source_groups': [group]}
            yield 'Fact'
        prepared = state.prepared((stream(), '', (), (), {'internal_only': 'hidden'}))
        return prepared
    _replace_runtime(rag_client, executor=DefaultRagExecutor(execute_pipeline=execute))
    response = rag_client.post('/chat/stream', headers=SERVICE_HEADERS,
        json={'user_id': 7, 'user_question': 'Van A nặng bao nhiêu?'})
    final = json.loads(response.text.strip().split('\n\n')[-1].splitlines()[1][6:])['debug_info']
    assert final['verification_status'] == 'partial'
    assert final['answer_outcome'] == 'partial_answer'
    assert final['generation_metrics']['source_groups'] == [group]
    assert 'internal_only' not in final


@pytest.mark.parametrize('failure', [False, True])
def test_service_sse_preserves_final_scan_progress(rag_client, failure):
    def execute(state):
        assert state.request.search_mode == 'comprehensive'
        assert state.request.access.user_id == 7
        diagnostics = {'comprehensive': {'scan_status': 'pending', 'checked_sources': 0,
            'pending_sources': 2, 'error_sources': 0, 'continuation_cursor': 'initial'}}
        def stream():
            prepared.diagnostics['comprehensive'] = {'scan_status': 'paused' if failure else 'complete',
                'checked_sources': 1 if failure else 2, 'pending_sources': 1 if failure else 0,
                'error_sources': 0, 'continuation_cursor': 'committed', 'turn_complete': True,
                'snapshot_complete': not failure}
            if failure:
                raise RequestDeadlineExceeded('test deadline')
            yield 'Verified result'
        prepared = state.prepared((stream(), '', (), (), diagnostics))
        return prepared
    _replace_runtime(rag_client, executor=DefaultRagExecutor(execute_pipeline=execute))
    response = rag_client.post('/chat/stream', headers=SERVICE_HEADERS,
        json={'user_id': 7, 'user_question': 'Tất cả quy trình'})
    assert response.status_code == 200
    events = [(block.splitlines()[0], json.loads(block.splitlines()[1][6:]))
              for block in response.text.strip().split('\n\n')]
    name, payload = events[-1]
    assert name == ('event: error' if failure else 'event: done')
    progress = payload['search_progress']
    assert progress['continuation_cursor'] == 'committed'
    assert progress['snapshot_complete'] is (not failure)
    assert progress['checked_sources'] == (1 if failure else 2)
    if not failure:
        assert payload['debug_info']['comprehensive'] == progress
