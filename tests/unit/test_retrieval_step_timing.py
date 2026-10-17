"""Observe the real LangChain adapter via the public RAG executor."""
import json
import logging
from functools import partial
from types import SimpleNamespace

import pytest
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import models

from .test_rag_pipeline_public_characterization import offline_pipeline, _document, _run, _trace_events, _answer

pytestmark = pytest.mark.unit


def test_embedding_and_qdrant_timings_are_separate_without_changing_results_or_requests(offline_pipeline, monkeypatch, caplog):
    from mech_chatbot.rag import retrieval, pipeline_steps
    documents, _, executor, runtime = offline_pipeline
    source = _document('Quy trình này áp dụng cho nhân viên chính thức. '
                       'Tài liệu không ghi chi phí của quy trình nghỉ phép.')
    requests = []
    clock = [0.0]
    monkeypatch.setattr(retrieval, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr('langchain_qdrant.QdrantVectorStore', QdrantVectorStore)
    class Dense(Embeddings):
        def embed_documents(self, texts): pytest.fail('query must not embed the corpus')
        def embed_query(self, text):
            clock[0] += .125
            return [1.0, 0.0]
    class Sparse:
        def embed_query(self, text):
            clock[0] += .5
            return models.SparseVector(indices=[7],values=[.25])
    class Client:
        def query_points(self, **kwargs):
            requests.append(kwargs)
            clock[0] += .75 if kwargs['using']=='sparse' else .25
            return SimpleNamespace(points=[models.ScoredPoint(id=17,version=1,score=.9,
                payload={'page_content':source.page_content,'metadata':dict(source.metadata)})])
    dense, sparse, client = Dense(), Sparse(), Client()
    runtime.vectorstore = SimpleNamespace(embeddings=dense,sparse_embeddings=sparse)
    runtime.client = client
    runtime.retrieve = partial(pipeline_steps._retrieve,vectorstore=runtime.vectorstore,client=client,
                               collection_name='fixture-timing')
    with caplog.at_level(logging.INFO,logger='RagTrace'):
        events = _run('Chi phí của quy trình nghỉ phép là bao nhiêu?',trace_id='query-timing',executor=executor)
    assert events[-1].outcome=='answered' and '[SRC:D17P2]' in _answer(events)
    steps = [e for e in _trace_events(caplog,'query-timing') if e.get('event')=='retrieval_step']
    parts = {e['step']:e['latency_ms'] for e in steps}
    assert {key:parts[key] for key in ('dense_embedding','dense_qdrant_query','sparse_embedding','sparse_qdrant_query')} == {
        'dense_embedding':125,'dense_qdrant_query':250,'sparse_embedding':500,'sparse_qdrant_query':750}
    assert len(requests)==2 and [r['timeout'] for r in requests]==[3,3]
    assert [r['using'] for r in requests]==['','sparse']
    assert requests[0]['query']==[1.0,0.0] and requests[1]['query'].indices==[7]
    assert all(r['limit']==5 and r['with_payload'] is True and r['with_vectors'] is False for r in requests)
    assert requests[0]['query_filter']==requests[1]['query_filter']
    stack = [requests[0]['query_filter'].model_dump()]
    conditions = []
    while stack:
        node = stack.pop()
        if isinstance(node,dict):
            if node.get('key'):
                conditions.append((node['key'],node.get('match')))
            stack.extend(node.values())
        elif isinstance(node,list):
            stack.extend(node)
    assert ('metadata.phong_ban_quyen',{'any':['HR','CHUNG']}) in conditions
    assert ('metadata.site',{'any':['HCM']}) in conditions
    for key,value in [('servable',True),('publication_state','published'),('review_status','approved'),('is_current',True)]:
        assert ('metadata.'+key,{'value':value}) in conditions
    assert all(e['execution_context']=='test' for e in steps)
    assert 'quy trình nghỉ phép' not in json.dumps(steps,ensure_ascii=False)
    assert runtime.vectorstore.embeddings is dense and runtime.vectorstore.sparse_embeddings is sparse
    assert runtime.client is client


@pytest.mark.parametrize('stage',['dense_embedding','dense_qdrant_query','sparse_embedding','sparse_qdrant_query'])
def test_cancel_between_native_adapter_steps_prevents_the_next_sdk_call(offline_pipeline,monkeypatch,caplog,stage):
    import threading
    from mech_chatbot.rag.execution import AccessScope, RagRequest, RagInvocation, RagCancelled
    from mech_chatbot.rag import pipeline_steps
    _, _, executor, runtime = offline_pipeline
    cancellation = threading.Event()
    calls = []
    def record(step):
        calls.append(step)
        if step==stage: cancellation.set()
    class Dense(Embeddings):
        def embed_documents(self,texts): pytest.fail('not a corpus ingestion')
        def embed_query(self,text):
            record('dense_embedding')
            return [1.0,0.0]
    class Sparse:
        def embed_query(self,text):
            record('sparse_embedding')
            return models.SparseVector(indices=[7],values=[.25])
    class Client:
        def query_points(self,**kwargs):
            record('sparse_qdrant_query' if kwargs['using']=='sparse' else 'dense_qdrant_query')
            assert kwargs['timeout']==3
            return SimpleNamespace(points=[])
    runtime.vectorstore=SimpleNamespace(embeddings=Dense(),sparse_embeddings=Sparse())
    runtime.client=Client()
    runtime.retrieve=partial(pipeline_steps._retrieve,vectorstore=runtime.vectorstore,client=runtime.client,
                             collection_name='fixture-timing')
    monkeypatch.setattr('langchain_qdrant.QdrantVectorStore',QdrantVectorStore)
    with caplog.at_level(logging.INFO,logger='RagTrace'):
        events=list(executor.run(RagRequest('Quy trình nghỉ phép có những bước nào?',AccessScope()),
                                RagInvocation('timing-cancel',mode='test'),cancellation))
    assert isinstance(events[-1],RagCancelled)
    order=['dense_embedding','dense_qdrant_query','sparse_embedding','sparse_qdrant_query']
    assert calls==order[:order.index(stage)+1]
    steps=[e for e in _trace_events(caplog,'timing-cancel') if e.get('event')=='retrieval_step']
    failed=next(e for e in steps if e['step']==stage)
    assert failed['status']=='failed' and failed['error_type']=='ExternalAICallCancelled'


def test_native_sparse_rpc_failure_is_attributed_without_retry_or_payload_disclosure(offline_pipeline,monkeypatch,caplog):
    from mech_chatbot.rag import pipeline_steps
    _, _, executor, runtime = offline_pipeline
    source=_document('Quy trình này áp dụng cho nhân viên chính thức. '
                     'Tài liệu không ghi chi phí của quy trình nghỉ phép.')
    calls=[]
    class Dense(Embeddings):
        def embed_documents(self,texts): pytest.fail('not a corpus ingestion')
        def embed_query(self,text): return [1.,0.]
    class Sparse:
        def embed_query(self,text): return models.SparseVector(indices=[7],values=[.25])
    class Client:
        def query_points(self,**kwargs):
            calls.append(kwargs['using'])
            assert kwargs['timeout']==3
            if kwargs['using']=='sparse': raise TimeoutError('restricted source text and URL')
            return SimpleNamespace(points=[models.ScoredPoint(id=17,version=1,score=.9,
                payload={'page_content':source.page_content,'metadata':dict(source.metadata)})])
    runtime.client=Client()
    runtime.vectorstore=SimpleNamespace(embeddings=Dense(),sparse_embeddings=Sparse())
    runtime.retrieve=partial(pipeline_steps._retrieve,vectorstore=runtime.vectorstore,client=runtime.client,
                             collection_name='fixture-timing')
    monkeypatch.setattr('langchain_qdrant.QdrantVectorStore',QdrantVectorStore)
    with caplog.at_level(logging.INFO,logger='RagTrace'):
        events=_run('Chi phí của quy trình nghỉ phép là bao nhiêu?',trace_id='sparse-rpc-error',executor=executor)
    assert events[-1].outcome=='answered' and '[SRC:D17P2]' in _answer(events)
    assert calls==['','sparse']
    steps=[e for e in _trace_events(caplog,'sparse-rpc-error') if e.get('event')=='retrieval_step']
    failed=next(e for e in steps if e['step']=='sparse_qdrant_query')
    assert failed['status']=='failed' and failed['error_type']=='TimeoutError'
    assert not any(e['step']=='hybrid_fallback' for e in steps)
    assert 'restricted source text' not in json.dumps(steps)


@pytest.fixture
def native_hybrid_fallback(offline_pipeline, monkeypatch, request):
    from langchain_qdrant import RetrievalMode
    from mech_chatbot.rag import retrieval, pipeline_steps
    _, _, executor, runtime = offline_pipeline
    source = _document('Quy trình này áp dụng cho nhân viên chính thức. '
                       'Tài liệu không ghi chi phí của quy trình nghỉ phép.')
    calls, requests, clock = [], [], [0.0]
    control = SimpleNamespace(fail_at=None, cancel_at=None, cancellation=None)
    monkeypatch.setattr(retrieval, 'perf_counter', lambda: clock[0])
    def record(stage, seconds):
        calls.append(stage)
        clock[0] += seconds
        if control.cancel_at == stage:
            control.cancellation.set()
        if control.fail_at == stage:
            raise TimeoutError('restricted fallback query and URL')
    class Dense(Embeddings):
        def embed_documents(self, texts): pytest.fail('query must not embed the corpus')
        def embed_query(self, text):
            record('hybrid_dense_embedding', .125)
            return [1.0, 0.0]
    class Sparse:
        def embed_query(self, text):
            record('hybrid_sparse_embedding', .5)
            return models.SparseVector(indices=[7], values=[.25])
    class Client:
        def query_points(self, **kwargs):
            requests.append(kwargs)
            record('hybrid_qdrant_query', .75)
            return SimpleNamespace(points=[models.ScoredPoint(id=17, version=1, score=.9,
                payload={'custom_text':source.page_content, 'custom_metadata':dict(source.metadata)})])
    dense, sparse, client = Dense(), Sparse(), Client()
    mode = RetrievalMode(getattr(request, 'param', 'hybrid'))
    original = QdrantVectorStore(client=client, collection_name='original-collection',
        embedding=None if mode == RetrievalMode.SPARSE else dense,
        sparse_embedding=None if mode == RetrievalMode.DENSE else sparse, retrieval_mode=mode,
        vector_name='custom-dense', sparse_vector_name='custom-sparse',
        content_payload_key='custom_text', metadata_payload_key='custom_metadata',
        validate_embeddings=False, validate_collection_config=False)
    runtime.vectorstore, runtime.client = original, client
    runtime.retrieve = partial(pipeline_steps._retrieve, vectorstore=original, client=client,
                               collection_name='explicit-collection')
    def unsupported_explicit_adapter(**kwargs):
        raise RuntimeError('Explicit adapter unavailable')
    monkeypatch.setattr('langchain_qdrant.QdrantVectorStore', unsupported_explicit_adapter)
    return SimpleNamespace(executor=executor, original=original, dense=dense, sparse=sparse,
        client=client, calls=calls, requests=requests, control=control)


def test_native_hybrid_fallback_reports_each_step_and_preserves_configuration(native_hybrid_fallback, caplog):
    native = native_hybrid_fallback
    with caplog.at_level(logging.INFO, logger='RagTrace'):
        events = _run('Chi phí của quy trình nghỉ phép là bao nhiêu?',
                      trace_id='native-hybrid-fallback', executor=native.executor)
    assert events[-1].outcome == 'answered' and '[SRC:D17P2]' in _answer(events)
    steps = [e for e in _trace_events(caplog, 'native-hybrid-fallback') if e.get('event') == 'retrieval_step']
    parts = {e['step']:e['latency_ms'] for e in steps}
    assert {key:parts[key] for key in ('hybrid_dense_embedding','hybrid_sparse_embedding','hybrid_qdrant_query')} == {
        'hybrid_dense_embedding':125, 'hybrid_sparse_embedding':500, 'hybrid_qdrant_query':750}
    assert native.calls == ['hybrid_dense_embedding','hybrid_sparse_embedding','hybrid_qdrant_query']
    assert len(native.requests) == 1
    request = native.requests[0]
    assert request['collection_name'] == 'original-collection' and request['timeout'] == 3
    assert request['limit'] == 5 and request['with_payload'] is True and request['with_vectors'] is False
    assert request['query'].fusion == models.Fusion.RRF
    dense, sparse = request['prefetch']
    assert dense.using == 'custom-dense' and dense.query == [1.0, 0.0]
    assert sparse.using == 'custom-sparse' and sparse.query.indices == [7]
    assert dense.filter == sparse.filter == request['query_filter'] and dense.limit == sparse.limit == 5
    assert native.original.embeddings is native.dense and native.original.sparse_embeddings is native.sparse
    assert native.original.client is native.client
    assert all(e['execution_context'] == 'test' for e in steps)
    assert 'quy trình nghỉ phép' not in json.dumps(steps, ensure_ascii=False)


@pytest.mark.parametrize('stage', ['hybrid_dense_embedding','hybrid_sparse_embedding','hybrid_qdrant_query'])
def test_native_hybrid_fallback_cancellation_stops_before_next_step(native_hybrid_fallback, caplog, stage):
    import threading
    from mech_chatbot.rag.execution import AccessScope, RagRequest, RagInvocation, RagCancelled
    native = native_hybrid_fallback
    native.control.cancel_at, native.control.cancellation = stage, threading.Event()
    with caplog.at_level(logging.INFO, logger='RagTrace'):
        events = list(native.executor.run(RagRequest('Quy trình nghỉ phép có những bước nào?', AccessScope()),
            RagInvocation('hybrid-fallback-cancel', mode='test'), native.control.cancellation))
    assert isinstance(events[-1], RagCancelled)
    order = ['hybrid_dense_embedding','hybrid_sparse_embedding','hybrid_qdrant_query']
    assert native.calls == order[:order.index(stage)+1]
    steps = [e for e in _trace_events(caplog, 'hybrid-fallback-cancel') if e.get('event') == 'retrieval_step']
    failed = next(e for e in steps if e['step'] == stage)
    assert failed['status'] == 'failed' and failed['error_type'] == 'ExternalAICallCancelled'
    assert failed['attempted'] is True
    assert not _answer(events)


@pytest.mark.parametrize('stage', ['hybrid_dense_embedding','hybrid_sparse_embedding','hybrid_qdrant_query'])
def test_native_hybrid_fallback_failure_is_attributed_without_retry_or_payload(native_hybrid_fallback, caplog, stage):
    from mech_chatbot.rag.execution import RagFailed
    native = native_hybrid_fallback
    native.control.fail_at = stage
    with caplog.at_level(logging.INFO, logger='RagTrace'):
        events = _run('Chi phí của quy trình nghỉ phép là bao nhiêu?',
                      trace_id='hybrid-fallback-error', executor=native.executor)
    assert isinstance(events[-1], RagFailed) and events[-1].code == 'TimeoutError'
    order = ['hybrid_dense_embedding','hybrid_sparse_embedding','hybrid_qdrant_query']
    assert native.calls == order[:order.index(stage)+1]
    steps = [e for e in _trace_events(caplog, 'hybrid-fallback-error') if e.get('event') == 'retrieval_step']
    failed = next(e for e in steps if e['step'] == stage)
    assert failed['status'] == 'failed' and failed['error_type'] == 'TimeoutError'
    assert failed['attempted'] is True and events[-1].diagnostics.budget.provider_retries == 0
    assert 'restricted fallback query' not in json.dumps(steps)


@pytest.mark.parametrize('native_hybrid_fallback', ['dense','sparse'], indirect=True)
def test_native_single_mode_fallback_preserves_absent_embedding(native_hybrid_fallback, caplog):
    from langchain_qdrant import RetrievalMode
    native = native_hybrid_fallback
    mode = native.original.retrieval_mode
    with caplog.at_level(logging.INFO, logger='RagTrace'):
        events = _run('Chi phí của quy trình nghỉ phép là bao nhiêu?',
                      trace_id='native-single-fallback', executor=native.executor)
    assert events[-1].outcome == 'answered' and '[SRC:D17P2]' in _answer(events)
    embedding = 'hybrid_dense_embedding' if mode == RetrievalMode.DENSE else 'hybrid_sparse_embedding'
    assert native.calls == [embedding, 'hybrid_qdrant_query'] and len(native.requests) == 1
    assert native.requests[0]['using'] == ('custom-dense' if mode == RetrievalMode.DENSE else 'custom-sparse')
    assert 'prefetch' not in native.requests[0] and native.requests[0]['timeout'] == 3
    if mode == RetrievalMode.DENSE:
        assert native.original.embeddings is native.dense
        with pytest.raises(ValueError, match='Sparse embeddings are `None`'):
            native.original.sparse_embeddings
    else:
        assert native.original.embeddings is None and native.original.sparse_embeddings is native.sparse
    steps = [e['step'] for e in _trace_events(caplog, 'native-single-fallback')
             if e.get('event') == 'retrieval_step' and e.get('phase') == 'general']
    assert steps == [embedding, 'hybrid_qdrant_query', 'hybrid_fallback']
