"""Scan -> GD5 generation/verifier -> committed SQL -> resumed evidence."""
from functools import partial
import json
import sys

from langchain_core.documents import Document

from mech_chatbot.rag.comprehensive import decode_cursor
from mech_chatbot.rag.comprehensive_execution import execute_comprehensive
from mech_chatbot.rag.execution import (
    AccessScope, DefaultRagExecutor, RagCompleted, RagInvocation, RagRequest, RagToken,
)
from .test_gd6_comprehensive import store
from .test_strict_stream_guard import _FakeChain, _load_pipeline_steps_without_rag_bootstrap, _prepare


def test_verified_scan_commits_only_checked_passages_and_replays_without_provider(store, monkeypatch):
    repository, _, auth, catalog = store
    catalog[:] = [{**catalog[0], 'PageCount':1, 'ExtractionComplete':True, 'SnapshotID':'snap-1'}]
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    monkeypatch.setitem(sys.modules, 'mech_chatbot.rag.pipeline_steps', module)
    source = 'Valve A weighs 20 kg.'
    answer = source + ' [SRC:D1P1]'
    runtime = _prepare(module, monkeypatch, _FakeChain([answer]), real_verification=True)
    monkeypatch.setattr(module, 'has_unsupported_numbers', lambda *a, **k:False)
    verification_calls = []
    def verify(*args, **kwargs):
        verification_calls.append(kwargs)
        payload = json.loads(args[0][0].content.split('\nDATA\n',1)[1])
        line_id = next(iter(payload['passages']['D1P1'][0]['lines']))
        selection = {'source_id':'D1P1','start_id':line_id,'end_id':line_id}
        return json.dumps({'claims':[{'id':1, 'supported':True, 'sources':['D1P1'],
            'quotes':[selection], 'subject':'Valve A',
            'condition':'unknown','time':'unknown','relationship':'unknown'}],
            'coverage':{'D1P1':[selection]}})
    runtime.provider_adapter.invoke_once = verify
    document = Document(page_content=source, metadata={'doc_id':1,'version_no':1,'trang_so':1,
        'snapshot_id':'snap-1','source_hash':'a'*64, 'security_level':'public',
        'external_processing_policy':'all_external'})
    executor = DefaultRagExecutor(
        execute_pipeline=partial(execute_comprehensive, store=repository, page_loader=lambda *a:[document]),
        retrieval_adapter=runtime.retrieval_adapter, provider_adapter=runtime.provider_adapter)
    access = AccessScope(user_id=7,username='u',allowed_sites=('HN',))
    request = RagRequest(auth['question'],access,search_mode='comprehensive',response_language='en')
    events = list(executor.run(request,RagInvocation(trace_id='gd6-verification',mode='test')))
    assert isinstance(events[-1],RagCompleted), events[-1]
    assert source in ''.join(e.text for e in events if isinstance(e,RagToken))
    final = events[-1].diagnostics['comprehensive']
    assert final['snapshot_complete'] and final['checked_sources'] == 1
    binding = decode_cursor(final['continuation_cursor'],repository.cursor_secret)
    from dataclasses import asdict, replace
    saved = repository.get_results(binding,user_id=7,question=request.question,access_scope=asdict(access))
    assert saved.results[0]['metadata']['snapshot_id'] == 'snap-1'
    assert saved.results[0]['metadata']['source_hash'] == 'a'*64
    assert saved.results[0]['metadata']['chunk_id'] == 'page-1-chars-0-21'
    resumed = list(executor.run(replace(request,continuation_cursor=final['continuation_cursor']),
        RagInvocation(trace_id='gd6-replay',mode='test')))
    assert isinstance(resumed[-1],RagCompleted)
    assert len(verification_calls) == 1
    assert resumed[-1].diagnostics['verification_status'] == 'verified'
    groups = resumed[-1].diagnostics['generation_metrics']['source_groups']
    assert groups[0]['doc_id'] == 1 and groups[0]['version_no'] == 1
    assert groups[0]['claims'][0]['subject'] == 'Valve A'
