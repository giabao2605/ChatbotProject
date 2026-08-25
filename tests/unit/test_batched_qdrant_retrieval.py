from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from qdrant_client import models
from qdrant_client.http.exceptions import ResponseHandlingException

from mech_chatbot.rag import pipeline_steps
from mech_chatbot.rag.pipeline_steps import _retrieve, _retrieve_many


pytestmark = pytest.mark.unit


class _DenseEmbeddings:
    def __init__(self):
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return [float(len(self.calls)), 0.5]


class _SparseEmbeddings:
    def __init__(self):
        self.calls = []

    def embed_query(self, text):
        self.calls.append(text)
        return SimpleNamespace(indices=[len(self.calls)], values=[1.0])


class _BatchClient:
    def __init__(self):
        self.calls = []

    def query_batch_points(self, **kwargs):
        self.calls.append(kwargs)
        call_index = len(self.calls)
        return [
            SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id=f"{call_index}-{request_index}",
                        payload={
                            "page_content": f"evidence {request_index}",
                            "metadata": {
                                "doc_id": request_index,
                                "servable": True,
                                "publication_state": "published",
                                "lifecycle_status": "published",
                                "review_status": "approved",
                                "is_current": True,
                            },
                        },
                    )
                ]
            )
            for request_index, _request in enumerate(kwargs["requests"], 1)
        ]

    def query_points(self, **_kwargs):
        raise AssertionError("branch reads must use query_batch_points")


def test_retrieve_many_batches_dense_and_sparse_for_three_strict_branches():
    dense = _DenseEmbeddings()
    sparse = _SparseEmbeddings()
    client = _BatchClient()
    vectorstore = SimpleNamespace(
        embeddings=dense,
        sparse_embeddings=sparse,
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )
    strict_filters = tuple(
        models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.doc_id",
                    match=models.MatchValue(value=index),
                )
            ]
        )
        for index in range(1, 4)
    )
    requests = tuple(
        {
            "new_part_ids": [f"PART-{index}"],
            "strict_filter": strict_filters[index - 1],
            "broad_filter": models.Filter(),
            "is_bom_query": False,
            "query_to_search": f"query {index}",
            "rbac_filter": models.Filter(),
            "trace_id": "batch-trace",
        }
        for index in range(1, 4)
    )

    results = _retrieve_many(
        requests,
        vectorstore=vectorstore,
        client=client,
        collection_name="test-knowledge",
    )

    assert dense.calls == ["query 1", "query 2", "query 3"]
    assert sparse.calls == ["query 1", "query 2", "query 3"]
    assert len(client.calls) == 2
    assert [len(call["requests"]) for call in client.calls] == [3, 3]
    assert all(call["timeout"] == 3 for call in client.calls)
    assert all(
        request.filter == strict_filter
        for call in client.calls
        for request, strict_filter in zip(
            call["requests"],
            strict_filters,
            strict=True,
        )
    )
    assert all(
        result[2] == "strict_exact:explicit_dense_bm25_rrf"
        for result in results
    )
    assert [len(result[0]) for result in results] == [1, 1, 1]


def test_retrieve_many_batches_broad_reads_only_for_bom_or_empty_strict():
    class _SelectiveBatchClient(_BatchClient):
        def query_batch_points(self, **kwargs):
            self.calls.append(kwargs)
            call_index = len(self.calls)
            return [
                SimpleNamespace(
                    points=(
                        []
                        if call_index <= 2 and request_index == 2
                        else [
                            SimpleNamespace(
                                id=f"{call_index}-{request_index}",
                                payload={
                                    "page_content": f"evidence {call_index}.{request_index}",
                                    "metadata": {
                                        "doc_id": f"{call_index}-{request_index}",
                                        "servable": True,
                                        "publication_state": "published",
                                        "lifecycle_status": "published",
                                        "review_status": "approved",
                                        "is_current": True,
                                    },
                                },
                            )
                        ]
                    )
                )
                for request_index, _request in enumerate(kwargs["requests"], 1)
            ]

    client = _SelectiveBatchClient()
    vectorstore = SimpleNamespace(
        embeddings=_DenseEmbeddings(),
        sparse_embeddings=_SparseEmbeddings(),
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )
    requests = tuple(
        {
            "new_part_ids": [f"PART-{index}"],
            "strict_filter": models.Filter(),
            "broad_filter": models.Filter(),
            "is_bom_query": index == 3,
            "query_to_search": f"query {index}",
            "rbac_filter": models.Filter(),
            "trace_id": "batch-trace",
        }
        for index in range(1, 4)
    )

    results = _retrieve_many(
        requests,
        vectorstore=vectorstore,
        client=client,
        collection_name="test-knowledge",
    )

    assert [len(call["requests"]) for call in client.calls] == [3, 3, 2, 2]
    assert results[0][2] == "strict_exact:explicit_dense_bm25_rrf"
    assert results[1][2] == "broad_fallback:explicit_dense_bm25_rrf"
    assert results[2][2] == "broad_fallback:explicit_dense_bm25_rrf"


def test_retrieve_many_sparse_batch_failure_is_terminal_without_retry(monkeypatch):
    class _SparseFailureClient(_BatchClient):
        def query_batch_points(self, **kwargs):
            if self.calls:
                self.calls.append(kwargs)
                raise RuntimeError("sparse unavailable")
            return super().query_batch_points(**kwargs)

    trace_events = []
    monkeypatch.setattr(
        pipeline_steps,
        "log_trace",
        lambda event, trace_id, **fields: trace_events.append(
            {"event": event, "trace_id": trace_id, **fields}
        ),
    )
    client = _SparseFailureClient()
    vectorstore = SimpleNamespace(
        embeddings=_DenseEmbeddings(),
        sparse_embeddings=_SparseEmbeddings(),
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )

    with pytest.raises(RuntimeError, match="sparse unavailable"):
        _retrieve_many(
            (
                {
                    "new_part_ids": ["PART-1"],
                    "strict_filter": models.Filter(),
                    "broad_filter": models.Filter(),
                    "is_bom_query": False,
                    "query_to_search": "query 1",
                    "rbac_filter": models.Filter(),
                    "trace_id": "sparse-failure-trace",
                },
            ),
            vectorstore=vectorstore,
            client=client,
            collection_name="test-knowledge",
        )

    assert len(client.calls) == 2
    failure = next(
        event
        for event in trace_events
        if event["event"] == "retrieval_batch" and event["status"] == "failed"
    )
    assert failure["error"] == "RuntimeError"
    assert failure["error_source"] is None
    assert failure["batch_stage"] == "sparse_query"
    assert failure["retry_attempted"] is False


def test_retrieve_many_dense_batch_failure_does_not_issue_serial_reads():
    class _DenseFailureClient:
        def __init__(self):
            self.batch_calls = 0
            self.serial_calls = 0

        def query_batch_points(self, **_kwargs):
            self.batch_calls += 1
            raise RuntimeError("batch unavailable")

        def query_points(self, **_kwargs):
            self.serial_calls += 1
            raise AssertionError("failed batches must not be retried serially")

    client = _DenseFailureClient()
    vectorstore = SimpleNamespace(
        embeddings=_DenseEmbeddings(),
        sparse_embeddings=_SparseEmbeddings(),
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )

    with pytest.raises(RuntimeError, match="batch unavailable"):
        _retrieve_many(
            (
                {
                    "new_part_ids": ["PART-1"],
                    "strict_filter": models.Filter(),
                    "broad_filter": models.Filter(),
                    "is_bom_query": False,
                    "query_to_search": "query 1",
                    "rbac_filter": models.Filter(),
                    "trace_id": None,
                },
            ),
            vectorstore=vectorstore,
            client=client,
            collection_name="test-knowledge",
        )

    assert client.batch_calls == 1
    assert client.serial_calls == 0


def test_retrieve_many_dense_batch_failure_traces_sanitized_source_and_stage(
    monkeypatch,
):
    class _DenseWrappedFailureClient:
        def query_batch_points(self, **_kwargs):
            raise ResponseHandlingException(ConnectionResetError("socket reset"))

    trace_events = []
    monkeypatch.setattr(
        pipeline_steps,
        "log_trace",
        lambda event, trace_id, **fields: trace_events.append(
            {"event": event, "trace_id": trace_id, **fields}
        ),
    )
    vectorstore = SimpleNamespace(
        embeddings=_DenseEmbeddings(),
        sparse_embeddings=_SparseEmbeddings(),
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )

    with pytest.raises(ResponseHandlingException):
        _retrieve_many(
            (
                {
                    "new_part_ids": ["PART-1"],
                    "strict_filter": models.Filter(),
                    "broad_filter": models.Filter(),
                    "is_bom_query": False,
                    "query_to_search": "query 1",
                    "rbac_filter": models.Filter(),
                    "trace_id": "wrapped-failure-trace",
                },
            ),
            vectorstore=vectorstore,
            client=_DenseWrappedFailureClient(),
            collection_name="test-knowledge",
        )

    failure = next(
        event
        for event in trace_events
        if event["event"] == "retrieval_batch" and event["status"] == "failed"
    )
    assert failure["error"] == "ResponseHandlingException"
    assert failure["error_source"] == "ConnectionResetError"
    assert failure["batch_stage"] == "dense_query"
    assert failure["retry_attempted"] is False


def test_retrieve_many_expired_deadline_stops_before_qdrant_traffic():
    client = _BatchClient()
    vectorstore = SimpleNamespace(
        embeddings=_DenseEmbeddings(),
        sparse_embeddings=_SparseEmbeddings(),
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )

    with pytest.raises(TimeoutError, match="deadline"):
        _retrieve_many(
            (
                {
                    "new_part_ids": ["PART-1"],
                    "strict_filter": models.Filter(),
                    "broad_filter": models.Filter(),
                    "is_bom_query": False,
                    "query_to_search": "query 1",
                    "rbac_filter": models.Filter(),
                    "trace_id": None,
                },
            ),
            vectorstore=vectorstore,
            client=client,
            collection_name="test-knowledge",
            deadline_monotonic=time.monotonic(),
        )

    assert client.calls == []


def test_retrieve_many_deadline_after_initial_batch_prevents_broad_traffic(
    monkeypatch,
):
    client = _BatchClient()
    vectorstore = SimpleNamespace(
        embeddings=_DenseEmbeddings(),
        sparse_embeddings=_SparseEmbeddings(),
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )
    ticks = iter((0.0, 0.0, 5.0))
    monkeypatch.setattr(
        pipeline_steps.time,
        "monotonic",
        lambda: next(ticks),
    )

    with pytest.raises(TimeoutError, match="deadline"):
        _retrieve_many(
            (
                {
                    "new_part_ids": ["PART-1"],
                    "strict_filter": models.Filter(),
                    "broad_filter": models.Filter(),
                    "is_bom_query": True,
                    "query_to_search": "query 1",
                    "rbac_filter": models.Filter(),
                    "trace_id": None,
                },
            ),
            vectorstore=vectorstore,
            client=client,
            collection_name="test-knowledge",
            deadline_monotonic=4.0,
        )

    assert len(client.calls) == 2


@pytest.mark.parametrize(
    ("new_part_ids", "is_bom_query", "documents_by_filter"),
    [
        (["PART-1"], False, {"strict": ("strict",), "broad": ("broad",)}),
        (["PART-1"], False, {"strict": (), "broad": ("broad",)}),
        (["PART-1"], True, {"strict": ("strict",), "broad": ("broad",)}),
        ([], False, {"general": ("general",)}),
    ],
)
def test_retrieve_many_preserves_single_retrieval_policy(
    monkeypatch,
    new_part_ids,
    is_bom_query,
    documents_by_filter,
):
    def _documents(payload_filter):
        return [
            Document(
                page_content=text,
                metadata={
                    "doc_id": text,
                    "servable": True,
                    "publication_state": "published",
                    "lifecycle_status": "published",
                    "review_status": "approved",
                    "is_current": True,
                },
            )
            for text in documents_by_filter.get(payload_filter, ())
        ]

    def _one(_query, payload_filter, *_args, **_kwargs):
        return _documents(payload_filter), "explicit_dense_bm25_rrf"

    def _many(requests, **_kwargs):
        return tuple(
            (
                _documents(request.payload_filter),
                "explicit_dense_bm25_rrf",
            )
            for request in requests
        )

    monkeypatch.setattr(pipeline_steps, "_explicit_hybrid_rrf", _one)
    monkeypatch.setattr(pipeline_steps, "_explicit_hybrid_rrf_batch", _many)
    monkeypatch.setattr(
        pipeline_steps,
        "current_published_filter",
        lambda _rbac: "general",
    )
    request = {
        "new_part_ids": new_part_ids,
        "strict_filter": "strict",
        "broad_filter": "broad",
        "is_bom_query": is_bom_query,
        "query_to_search": "query",
        "rbac_filter": object(),
        "trace_id": None,
    }

    direct = _retrieve(
        **request,
        vectorstore=object(),
        client=object(),
        collection_name="test-knowledge",
    )
    batched = _retrieve_many(
        (request,),
        vectorstore=object(),
        client=object(),
        collection_name="test-knowledge",
    )[0]

    assert [document.page_content for document in batched[0]] == [
        document.page_content for document in direct[0]
    ]
    assert batched[1:3] == direct[1:3]
    assert batched[4] == direct[4]
