from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import requests

from mech_chatbot.config.settings import ExternalAiSettings
from mech_chatbot.llm import external_ai
from mech_chatbot.config import validate as config_validation
from mech_chatbot.rag import rerank


pytestmark = pytest.mark.unit


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _doc(text):
    return SimpleNamespace(
        page_content=text,
        metadata={"noi_dung_goc": text},
    )


def test_jina_rerank_uses_top_n_and_preserves_ranked_document_identity(
    monkeypatch,
):
    posted = {}
    audit = {}

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted.update(kwargs)
        return _Response(
            {
                "results": [
                    {"index": 2, "relevance_score": 0.93},
                    {"index": 0, "relevance_score": 0.61},
                ]
            }
        )

    @contextmanager
    def fake_audit(**kwargs):
        audit.update(kwargs)
        yield

    monkeypatch.setattr(rerank.requests, "post", fake_post)
    monkeypatch.setattr(rerank, "audited_external_call", fake_audit)
    docs = [_doc("zero"), _doc("one"), _doc("two")]
    runtime = SimpleNamespace(
        api_key="test-key",
        model="jina-reranker-v3",
        endpoint="https://api.jina.ai/v1",
    )

    result = rerank.jina_rerank_documents(
        docs,
        "cau hoi",
        top_n=2,
        runtime=runtime,
    )

    assert result == [docs[2], docs[0]]
    assert result[0] is docs[2]
    assert result[1] is docs[0]
    assert docs[2].metadata["relevance_score"] == 0.93
    assert docs[0].metadata["relevance_score"] == 0.61
    assert posted["url"] == "https://api.jina.ai/v1/rerank"
    assert posted["json"] == {
        "query": "cau hoi",
        "documents": ["zero", "one", "two"],
        "model": "jina-reranker-v3",
        "top_n": 2,
        "return_documents": False,
    }
    assert "top_k" not in posted["json"]
    assert audit["provider"] == "jina"
    assert audit["surface"] == "reranking"


def test_jina_rerank_requires_key_without_leaking_credentials():
    with pytest.raises(RuntimeError) as exc_info:
        rerank.jina_rerank_documents(
            [_doc("text")],
            "cau hoi",
            runtime=SimpleNamespace(
                api_key="",
                model="jina-reranker-v3",
                endpoint="https://api.jina.ai/v1",
            ),
        )

    message = str(exc_info.value)
    assert "JINA_API_KEY" in message
    assert "Authorization" not in message


def test_jina_rerank_uses_runtime_local_policy_settings(monkeypatch):
    calls = []

    monkeypatch.setattr(external_ai, "_record_external_call", lambda spec, *_args, **_kwargs: calls.append(spec) or True)
    monkeypatch.setattr(
        external_ai,
        "_load_managed_provider_profile",
        lambda _provider: None,
    )
    monkeypatch.setattr(
        rerank.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            {"results": [{"index": 0, "relevance_score": 0.8}]}
        ),
    )
    settings = ExternalAiSettings(
        application_environment="development",
        local_development=True,
        processing_policy="all_external",
    )
    profile = external_ai.get_external_ai_provider_profile(
        "jina",
        settings=settings,
    )
    runtime = SimpleNamespace(
        api_key="test-key",
        model="jina-reranker-v3",
        endpoint="https://api.jina.ai/v1",
        profile=profile,
        settings=settings,
    )

    doc = _doc("noi dung")
    doc.metadata["external_processing_policy"] = "all_external"

    result = rerank.jina_rerank_documents(
        [doc],
        "cau hoi",
        top_n=1,
        runtime=runtime,
    )

    assert len(result) == 1
    assert [call.provider for call in calls] == ["jina", "jina"]


def test_jina_failure_metadata_uses_immediate_local_fallback_without_retry():
    response = SimpleNamespace(status_code=429)
    error = requests.HTTPError(
        "429 Client Error: Too Many Requests",
        response=response,
    )

    metadata = rerank.jina_failure_metadata(error)

    assert metadata == {
        "backend": "jina",
        "status": "error",
        "fallback": True,
        "fallback_backend": "local_fusion",
        "fallback_reason": "HTTPError",
        "provider_status_code": 429,
        "retryable": True,
        "retry_attempted": False,
    }


def test_rerank_provider_selection_keeps_voyage_default_and_fails_closed():
    allowed = [
        SimpleNamespace(
            page_content="public",
            metadata={"external_processing_policy": "all_external"},
        )
    ]
    runtime = SimpleNamespace(api_key="configured")

    assert rerank.RerankPolicy(runtime=runtime).select_backend(allowed) == "voyage"
    assert (
        rerank.RerankPolicy(provider="jina", runtime=runtime).select_backend(allowed)
        == "jina"
    )
    assert (
        rerank.RerankPolicy(provider="unsupported", runtime=runtime).select_backend(
            allowed
        )
        == "local_fusion"
    )
    assert (
        rerank.RerankPolicy(provider="jina", runtime=None).select_backend(allowed)
        == "local_fusion"
    )


def test_jina_settings_validation_rejects_unknown_provider_and_masks_key():
    secret = "jina-" + "secret-value"
    errors, _warnings = config_validation.validate_config(
        {
            "RERANK_PROVIDER": "unknown",
            "JINA_API_KEY": secret,
        },
        require_qdrant=False,
        require_llm=False,
        require_sql=False,
        require_embedding=False,
    )
    summary = config_validation.safe_config_summary(
        {
            "RERANK_PROVIDER": "jina",
            "JINA_API_KEY": secret,
        }
    )

    assert any("RERANK_PROVIDER" in error for error in errors)
    assert summary["JINA_API_KEY"].startswith("SET(")
    assert secret not in str(summary)
