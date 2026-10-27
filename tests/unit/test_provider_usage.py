from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, LLMResult

from mech_chatbot.llm import llm_client


pytestmark = pytest.mark.unit


def test_reported_usage_preserves_cost_and_missing_fields():
    response = AIMessage(content="ok", usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                         response_metadata={"token_usage": {"prompt_tokens": 12, "completion_tokens": 3, "cost": 0.004}})
    assert llm_client.provider_usage(response) == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15, "cost_usd": 0.004}
    assert llm_client.provider_usage(AIMessage(content="ok")) == {
        "input_tokens": None, "output_tokens": None, "total_tokens": None, "cost_usd": None}


def test_stream_usage_callback_records_one_call_with_unknown_cost():
    calls = []
    callback = llm_client.ProviderUsageCallback("model", "trace", "generation", calls.append)
    result = LLMResult(generations=[[ChatGeneration(message=AIMessageChunk(
        content="ok", usage_metadata={"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}))]])
    callback.on_llm_end(result)
    assert len(calls) == 1
    assert calls[0]["input_tokens"] == 7
    assert calls[0]["cost_usd"] is None
    assert calls[0]["surface"] == "generation"


def test_stream_adapter_retains_provider_reported_cost():
    import httpx, json
    def transport(_request):
        chunks = [
            {"id":"fixture", "model":"fixture", "choices":[{"index":0,"delta":{"role":"assistant","content":"answer"},"finish_reason":None}]},
            {"id":"fixture", "model":"fixture", "choices":[], "usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15,"cost":.004}},
        ]
        return httpx.Response(200, headers={"content-type":"text/event-stream"},
            text="".join("data: "+json.dumps(chunk)+"\n\n" for chunk in chunks)+"data: [DONE]\n\n")
    records = []
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        model = llm_client.ChatOpenAI(api_key="fixture",model="fixture",http_client=client,stream_usage=True)
        assert "".join(chunk.content for chunk in model.stream("question", config={"callbacks":[
            llm_client.ProviderUsageCallback("fixture", "stream-fixture", "generation", records.append)]})) == "answer"
    assert len(records) == 1
    assert records[0]["cost_usd"] == .004


@pytest.mark.parametrize("bad", [-1, True, "bad", float("inf")])
def test_usage_rejects_invalid_provider_cost(bad):
    result = SimpleNamespace(response_metadata={"token_usage": {"cost": bad}}, usage_metadata=None)
    assert llm_client.provider_usage(result)["cost_usd"] is None


def test_invoke_records_usage_without_changing_response(monkeypatch):
    @contextmanager
    def audited(**_kwargs):
        yield
    monkeypatch.setattr(llm_client, "audited_external_call", audited)
    monkeypatch.setattr(llm_client, "_request_timeout_options", lambda _a: {})
    calls = []
    monkeypatch.setattr(llm_client, "record_provider_usage", lambda response, **kwargs: calls.append((response, kwargs)))
    response = AIMessage(content="ok")
    adapter = SimpleNamespace(client=SimpleNamespace(invoke=lambda *_a, **_k: response),
                              settings=SimpleNamespace(model_name="test-model", base_url="https://example.invalid"),
                              external_ai_settings=llm_client.DEFAULT_EXTERNAL_AI_SETTINGS)
    assert llm_client.gpt_invoke([], adapter=adapter, trace_id="trace") is response
    assert calls[0][1]["surface"] == "generation"


def test_failed_executor_retains_unknown_provider_cost():
    from mech_chatbot.rag.execution import AccessScope, DefaultRagExecutor, RagFailed, RagInvocation, RagRequest
    def fail(state):
        state.budget.record_provider_usage({"model":"test", "surface":"generation", "cost_usd":None})
        raise RuntimeError("provider unavailable")
    events = list(DefaultRagExecutor(execute_pipeline=fail).run(
        RagRequest("question", AccessScope()), RagInvocation(trace_id="usage-failure", mode="test")))
    failure = next(event for event in events if isinstance(event, RagFailed))
    assert failure.diagnostics["provider_usage"][0]["cost_usd"] is None
    assert failure.diagnostics["provider_cost_usd"] is None
    assert failure.diagnostics["provider_usage_complete"] is False


@pytest.mark.parametrize("provider", ["jina", "voyage"])
@pytest.mark.parametrize("fails", [False, True])
def test_rerank_usage_is_retained_without_inventing_cost(monkeypatch, provider, fails):
    from mech_chatbot.rag import rerank
    from mech_chatbot.rag.execution import AccessScope, DefaultRagExecutor, RagInvocation, RagRequest
    @contextmanager
    def allowed(**_kwargs):
        yield
    def response(*_args, **_kwargs):
        if fails:
            raise TimeoutError("provider timeout")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "results":[{"index":0, "relevance_score":.9}], "data":[{"index":0, "relevance_score":.9}],
            "usage":{"total_tokens":42}})
    monkeypatch.setattr(rerank, "audited_external_call", allowed)
    monkeypatch.setattr(rerank.requests, "post", response)
    def pipeline(state):
        function = getattr(rerank, provider + "_rerank_documents")
        function([SimpleNamespace(page_content="source", metadata={})], "question", runtime=SimpleNamespace(
            api_key="fixture", model="rerank-model", endpoint="https://example.invalid/v1"))
        return state.prepared((iter(["answer"]), "", [], [], {}))
    terminal = list(DefaultRagExecutor(execute_pipeline=pipeline).run(
        RagRequest("question", AccessScope()), RagInvocation(trace_id="rerank-usage", mode="test")))[-1]
    record = terminal.diagnostics["provider_usage"][0]
    assert record["surface"] == "reranking"
    assert record["total_tokens"] == (None if fails else 42)
    assert terminal.diagnostics["provider_cost_usd"] is None
    assert terminal.diagnostics["provider_usage_complete"] is False
