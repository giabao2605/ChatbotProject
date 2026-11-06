from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import pytest
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from mech_chatbot.rag import pipeline_steps, retrieval
from mech_chatbot.rag.execution import RequestDeadlineExceeded
from mech_chatbot.rag.retrieval import (
    TracedRetrievalDependency,
    call_with_qdrant_retry,
    run_retrieval_step,
)


pytestmark = pytest.mark.unit


def _timeout():
    return ResponseHandlingException(httpx.ReadTimeout("read timed out"))


def test_retry_once_on_timeout_then_return_second_result():
    seen = []

    def call(timeout):
        seen.append(timeout)
        if len(seen) == 1:
            raise _timeout()
        return "ok"

    assert call_with_qdrant_retry(call, timeout=10, stage="t") == "ok"
    assert seen == [10, 10]


def test_retry_on_connect_error():
    seen = []

    def call(timeout):
        seen.append(timeout)
        if len(seen) == 1:
            raise ResponseHandlingException(httpx.ConnectError("refused"))
        return "ok"

    assert call_with_qdrant_retry(call, timeout=10, stage="t") == "ok"
    assert len(seen) == 2


def test_at_most_one_retry_and_failed_retry_is_marked():
    calls = []

    def call(timeout):
        calls.append(timeout)
        raise _timeout()

    with pytest.raises(ResponseHandlingException) as info:
        call_with_qdrant_retry(call, timeout=10, stage="t")

    assert len(calls) == 2
    assert info.value.qdrant_retry_attempted is True


@pytest.mark.parametrize(
    "error",
    [
        UnexpectedResponse(403, "forbidden", b"", {}),
        UnexpectedResponse(500, "boom", b"", {}),
        ResponseHandlingException(httpx.RemoteProtocolError("server disconnected")),
        ResponseHandlingException(ConnectionResetError("reset")),
        RequestDeadlineExceeded("deadline"),
        KeyboardInterrupt(),
    ],
)
def test_other_errors_are_not_retried(error):
    calls = []

    def call(timeout):
        calls.append(timeout)
        raise error

    with pytest.raises(type(error)):
        call_with_qdrant_retry(call, timeout=10, stage="t")

    assert len(calls) == 1


def test_no_retry_when_deadline_leaves_less_than_five_seconds():
    calls = []

    def call(timeout):
        calls.append(timeout)
        raise _timeout()

    with pytest.raises(ResponseHandlingException) as info:
        call_with_qdrant_retry(
            call, timeout=10, stage="t", deadline_monotonic=time.monotonic() + 3,
        )

    assert len(calls) == 1
    assert not hasattr(info.value, "qdrant_retry_attempted")


def test_no_retry_when_deadline_already_expired():
    calls = []

    def call(timeout):
        calls.append(timeout)
        raise _timeout()

    with pytest.raises(ResponseHandlingException):
        call_with_qdrant_retry(
            call, timeout=10, stage="t", deadline_monotonic=time.monotonic() - 1,
        )

    assert len(calls) == 1


def test_retry_timeout_is_min_of_limit_and_remaining_and_first_is_unchanged():
    calls = []

    def call(timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise _timeout()

    call_with_qdrant_retry(
        call, timeout=10, stage="t", deadline_monotonic=time.monotonic() + 7.5,
    )
    assert calls[0] == 10
    assert 5 <= calls[1] <= 7


def test_retry_result_is_logged_on_the_existing_retrieval_step(monkeypatch):
    events = []
    monkeypatch.setattr(
        retrieval, "log_trace", lambda event, trace_id, **f: events.append((event, f))
    )

    def operation():
        def call(timeout):
            raise _timeout()

        return call_with_qdrant_retry(call, timeout=10, stage="t")

    with pytest.raises(ResponseHandlingException):
        run_retrieval_step(operation, trace_id="tr", step="dense_query", phase="batch")

    assert [e for e, _ in events] == ["retrieval_step"]
    fields = events[0][1]
    assert fields["status"] == "failed"
    assert fields["retry_attempted"] is True
    assert fields["retry_error_type"] == "ResponseHandlingException"
    assert isinstance(fields["retry_latency_ms"], int)
    assert "query" not in fields


def test_step_without_retry_has_no_retry_fields(monkeypatch):
    events = []
    monkeypatch.setattr(
        retrieval, "log_trace", lambda event, trace_id, **f: events.append(f)
    )
    run_retrieval_step(lambda: 1, trace_id="tr", step="s", phase="p")
    assert "retry_attempted" not in events[0]


def test_traced_query_points_retries_with_same_kwargs():
    seen = []

    class _Client:
        def query_points(self, **kwargs):
            seen.append(kwargs)
            if len(seen) == 1:
                raise _timeout()
            return SimpleNamespace(points=[])

    traced = TracedRetrievalDependency(
        _Client(), "query_points", "dense_qdrant_query", None, "general",
    )
    traced.query_points(collection_name="c", timeout=10)

    assert [k["timeout"] for k in seen] == [10, 10]
    assert all(k["collection_name"] == "c" for k in seen)


def test_traced_non_query_points_method_is_not_retried():
    calls = []

    class _Embed:
        def embed_query(self, text):
            calls.append(text)
            raise _timeout()

    traced = TracedRetrievalDependency(_Embed(), "embed_query", "dense_embedding", None, "general")
    with pytest.raises(ResponseHandlingException):
        traced.embed_query("x")

    assert calls == ["x"]


class _StoreFactory:
    """Stands in for QdrantVectorStore: one search is one client.query_points call."""

    def __init__(self, **kwargs):
        self.client = kwargs["client"]

    def similarity_search(self, query, k, filter=None, timeout=None):
        self.client.query_points(timeout=timeout)
        return [SimpleNamespace(page_content="doc", metadata={})]


def _run_single_path(monkeypatch, client, fallback_calls):
    monkeypatch.setattr("langchain_qdrant.QdrantVectorStore", _StoreFactory)
    monkeypatch.setattr(pipeline_steps, "log_trace", lambda *a, **k: None)

    class _Retriever:
        def invoke(self, _query):
            fallback_calls.append(len(client.calls))
            return [SimpleNamespace(page_content="fallback", metadata={})]

    vectorstore = SimpleNamespace(
        embeddings=object(), sparse_embeddings=object(),
        as_retriever=lambda **_k: _Retriever(),
    )
    return pipeline_steps._explicit_hybrid_rrf(
        "q", payload_filter=object(), dense_top_k=5, sparse_top_k=5, result_cap=5,
        vectorstore=vectorstore, client=client, collection_name="c",
    )


def test_single_path_retry_rescues_before_hybrid_fallback(monkeypatch):
    class _Client:
        def __init__(self):
            self.calls = []

        def query_points(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise _timeout()

    client = _Client()
    fallback_calls = []

    _docs, mode = _run_single_path(monkeypatch, client, fallback_calls)

    assert mode == "explicit_dense_bm25_rrf"
    assert fallback_calls == []
    assert len(client.calls) == 3  # dense failed once then retried, sparse once


def test_single_path_falls_back_only_after_retry_also_failed(monkeypatch):
    class _Client:
        def __init__(self):
            self.calls = []

        def query_points(self, **kwargs):
            self.calls.append(kwargs)
            raise _timeout()

    client = _Client()
    fallback_calls = []

    docs, mode = _run_single_path(monkeypatch, client, fallback_calls)

    assert mode == "hybrid_fallback"
    assert fallback_calls == [2]  # dense: first try + one retry, then fallback
    assert docs[0].page_content == "fallback"
