"""OpenAI-compatible text adapter built from an immutable settings snapshot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from langchain_openai import ChatOpenAI
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from mech_chatbot.config.logging import log_trace
from mech_chatbot.config.settings import ExternalAiSettings, LlmSettings
from mech_chatbot.llm.external_ai import (
    DEFAULT_EXTERNAL_AI_SETTINGS,
    audited_external_call,
    text_byte_count,
    text_char_count,
)


_MISSING_PROVIDER_KEY = "provider API key is not configured"
_DEFAULT_PROVIDER_ENDPOINT = "https://api.proxyllm.eu/v1"


def _validated_settings(settings: LlmSettings) -> LlmSettings:
    api_key = str(settings.api_key or "").strip()
    if not api_key or api_key == "DIEN_KEY_CUA_BAN_VAO_DAY":
        raise ValueError(_MISSING_PROVIDER_KEY)
    model_name = str(settings.model_name or "").strip()
    if not model_name:
        raise ValueError("provider model is not configured")
    if int(settings.max_output_tokens) <= 0 or float(settings.timeout_seconds) <= 0:
        raise ValueError("provider limits must be positive")
    return replace(
        settings,
        api_key=api_key,
        base_url=(
            str(settings.base_url).strip()
            if settings.base_url
            else _DEFAULT_PROVIDER_ENDPOINT
        ),
        model_name=model_name,
    )


def _make_llm(settings: LlmSettings, max_tokens: int | None = None) -> ChatOpenAI:
    snapshot = _validated_settings(settings)
    return ChatOpenAI(
        model=snapshot.model_name,
        api_key=snapshot.api_key,
        base_url=snapshot.base_url,
        temperature=snapshot.temperature,
        max_tokens=(
            snapshot.max_output_tokens if max_tokens is None else int(max_tokens)
        ),
        timeout=snapshot.timeout_seconds,
        max_retries=0,
    )


@dataclass(frozen=True, slots=True)
class LlmAdapter:
    """Text-generation adapter whose behavior is fixed at construction time."""

    settings: LlmSettings
    external_ai_settings: ExternalAiSettings
    client: Any

    def invoke(
        self,
        messages,
        surface: str = "generation",
        trace_id: str | None = None,
        doc_ids=None,
        security_levels=None,
        policies=None,
        retry_counter=None,
    ):
        return gpt_invoke(
            messages,
            surface=surface,
            trace_id=trace_id,
            doc_ids=doc_ids,
            security_levels=security_levels,
            policies=policies,
            retry_counter=retry_counter,
            adapter=self,
        )


def build_llm_adapter(
    settings: LlmSettings,
    *,
    external_ai_settings: ExternalAiSettings = DEFAULT_EXTERNAL_AI_SETTINGS,
) -> LlmAdapter:
    """Build one process-owned adapter without reading ambient environment."""

    snapshot = _validated_settings(settings)
    return LlmAdapter(
        settings=snapshot,
        external_ai_settings=external_ai_settings,
        client=_make_llm(snapshot),
    )


def _is_gpt_rate_limit(exc):
    msg = str(exc).lower()
    return (
        "429" in msg
        or "502" in msg
        or "503" in msg
        or "bad gateway" in msg
        or "too many requests" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "no_capacity" in msg
        or "service_unavailable" in msg
        or "overloaded" in msg
        or "quá tải" in msg
        or "temporarily unavailable" in msg
        or "timeout" in msg
    )


def _is_cohere_rate_limit(exc):
    return _is_gpt_rate_limit(exc)


def _before_llm_retry(retry_state):
    kwargs = retry_state.kwargs or {}
    counter = kwargs.get("retry_counter")
    if counter is None:
        try:
            from mech_chatbot.rag.execution import current_request_budget

            counter = current_request_budget()
        except Exception:
            counter = None
    consume_retry = getattr(counter, "consume_provider_retry", None)
    if callable(consume_retry):
        consume_retry()
    elif isinstance(counter, dict):
        counter["count"] = int(counter.get("count") or 0) + 1
    error = retry_state.outcome.exception() if retry_state.outcome else None
    log_trace(
        "llm_retry",
        kwargs.get("trace_id"),
        surface=kwargs.get("surface") or "generation",
        attempt=retry_state.attempt_number,
        max_attempts=(
            int(counter.limits.provider_retries) + 1
            if hasattr(counter, "limits")
            else 4
        ),
        error=type(error).__name__ if error else "unknown",
    )


def _get_runtime_llm(adapter: LlmAdapter | None = None):
    """Compatibility seam requiring an explicitly composed adapter."""

    if adapter is None:
        raise RuntimeError("LLM adapter is not configured for this process")
    return adapter.client


@retry(
    retry=retry_if_exception(_is_gpt_rate_limit),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    before_sleep=_before_llm_retry,
)
def gpt_invoke(
    messages,
    surface="generation",
    trace_id=None,
    doc_ids=None,
    security_levels=None,
    policies=None,
    retry_counter=None,
    *,
    adapter: LlmAdapter | None = None,
):
    client = _get_runtime_llm() if adapter is None else adapter.client
    model = get_llm_model_name(adapter)
    endpoint = get_llm_endpoint(adapter)
    with audited_external_call(
        provider="proxyllm",
        model=model,
        endpoint=endpoint,
        surface=surface,
        trace_id=trace_id,
        doc_ids=doc_ids,
        security_levels=security_levels,
        policies=policies,
        input_chars=text_char_count(messages),
        input_bytes=text_byte_count(messages),
        settings=(
            adapter.external_ai_settings
            if adapter is not None
            else DEFAULT_EXTERNAL_AI_SETTINGS
        ),
    ):
        return client.invoke(messages)


def cohere_invoke(
    messages,
    surface="generation",
    trace_id=None,
    doc_ids=None,
    security_levels=None,
    policies=None,
    retry_counter=None,
    *,
    adapter: LlmAdapter | None = None,
):
    return gpt_invoke(
        messages,
        surface=surface,
        trace_id=trace_id,
        doc_ids=doc_ids,
        security_levels=security_levels,
        policies=policies,
        retry_counter=retry_counter,
        adapter=adapter,
    )


def get_llm_endpoint(adapter: LlmAdapter | None = None):
    if adapter is None:
        # Metadata-only compatibility default. Provider calls still require an
        # explicitly composed adapter through ``_get_runtime_llm``.
        return _DEFAULT_PROVIDER_ENDPOINT
    return adapter.settings.base_url


def get_llm_model_name(adapter: LlmAdapter | None = None):
    if adapter is None:
        return "gpt-5.4"
    return adapter.settings.model_name


def get_gpt_llm(adapter: LlmAdapter | None = None):
    return _get_runtime_llm(adapter)


def get_cohere_llm(adapter: LlmAdapter | None = None):
    return _get_runtime_llm(adapter)


__all__ = [
    "LlmAdapter",
    "_is_cohere_rate_limit",
    "_is_gpt_rate_limit",
    "build_llm_adapter",
    "cohere_invoke",
    "get_cohere_llm",
    "get_gpt_llm",
    "get_llm_endpoint",
    "get_llm_model_name",
    "gpt_invoke",
]
