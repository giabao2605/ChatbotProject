"""OpenAI-compatible text adapter built from an immutable settings snapshot."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import math
from time import monotonic
from typing import Any

from langchain_openai import ChatOpenAI as _ChatOpenAI
from langchain_core.callbacks import BaseCallbackHandler
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from mech_chatbot.config.logging import log_trace
from mech_chatbot.config.settings import ExternalAiSettings, LlmSettings
from mech_chatbot.llm.external_ai import (
    DEFAULT_EXTERNAL_AI_SETTINGS,
    audited_external_call,
    compatible_provider_name,
    text_byte_count,
    text_char_count,
)
from mech_chatbot.rag.execution import remaining_request_timeout


_MISSING_PROVIDER_KEY = "provider API key is not configured"
_DEFAULT_PROVIDER_ENDPOINT = "https://api.proxyllm.eu/v1"


class ChatOpenAI(_ChatOpenAI):
    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if generation is None or not isinstance(chunk.get("usage"), dict):
            return generation
        # The installed SDK keeps token counts but drops provider-specific streaming cost.
        metadata = {**generation.message.response_metadata, "token_usage": chunk["usage"]}
        return generation.model_copy(update={"message": generation.message.model_copy(update={"response_metadata": metadata})})


def provider_usage(response):
    """Read reported usage only; missing provider fields remain unknown."""
    metadata = getattr(response, "response_metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    raw = metadata.get("token_usage") or metadata.get("usage") or {}
    usage = getattr(response, "usage_metadata", None)
    usage = usage if isinstance(usage, dict) else {}
    generations = getattr(response, "generations", None)
    if isinstance(generations, (list, tuple)):
        output = getattr(response, "llm_output", None)
        output = output if isinstance(output, dict) else {}
        raw = output.get("token_usage") or output.get("usage") or raw
        if generations and isinstance(generations[0], (list, tuple)) and generations[0]:
            message = getattr(generations[0][0], "message", None)
            usage = getattr(message, "usage_metadata", None) or usage
            message_metadata = getattr(message, "response_metadata", None)
            message_metadata = message_metadata if isinstance(message_metadata, dict) else {}
            raw = raw or message_metadata.get("token_usage") or {}
    raw = raw if isinstance(raw, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    def valid(value, *, token=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or value < 0 or (token and int(value) != value):
            return None
        return int(value) if token else float(value)
    return {
        "input_tokens": valid(usage.get("input_tokens", raw.get("prompt_tokens")), token=True),
        "output_tokens": valid(usage.get("output_tokens", raw.get("completion_tokens")), token=True),
        "total_tokens": valid(usage.get("total_tokens", raw.get("total_tokens")), token=True),
        "cost_usd": valid(raw.get("cost")),
    }


def record_provider_usage(response, *, model, trace_id, surface, sink=None):
    record = {"model": model, "surface": surface, **provider_usage(response)}
    from mech_chatbot.rag.execution import current_request_budget
    budget = current_request_budget()
    if budget is not None:
        budget.record_provider_usage(record)
    if sink is not None:
        sink(record)
    log_trace("provider_usage", trace_id, **record)
    return record


class ProviderUsageCallback(BaseCallbackHandler):
    run_inline = True

    def __init__(self, model, trace_id, surface, sink=None):
        self.model, self.trace_id, self.surface, self.sink = model, trace_id, surface, sink

    def on_llm_end(self, response, **kwargs):
        record_provider_usage(response, model=self.model, trace_id=self.trace_id,
                              surface=self.surface, sink=self.sink)

    def on_llm_error(self, error, **kwargs):
        record_provider_usage(None, model=self.model, trace_id=self.trace_id,
                              surface=self.surface, sink=self.sink)


class ProviderCompletionError(RuntimeError):
    """The provider explicitly failed an otherwise successful HTTP response."""


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
        temperature=(
            None if compatible_provider_name(snapshot.base_url) == "openrouter"
            and snapshot.model_name == "openai/gpt-5.6-luna"
            else snapshot.temperature
        ),
        max_tokens=(
            snapshot.max_output_tokens if max_tokens is None else int(max_tokens)
        ),
        timeout=snapshot.timeout_seconds,
        max_retries=0,
        stream_usage=True,
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
        timeout_seconds=None,
        *,
        response_format=None,
    ):
        return gpt_invoke(
            messages,
            surface=surface,
            trace_id=trace_id,
            doc_ids=doc_ids,
            security_levels=security_levels,
            policies=policies,
            retry_counter=retry_counter,
            timeout_seconds=timeout_seconds,
            adapter=self,
            response_format=response_format,
        )

    def invoke_once(
        self,
        messages,
        surface: str = "generation",
        trace_id: str | None = None,
        doc_ids=None,
        security_levels=None,
        policies=None,
        retry_counter=None,
        timeout_seconds=None,
        *,
        response_format=None,
    ):
        return gpt_invoke.retry_with(stop=stop_after_attempt(1))(
            messages,
            surface=surface,
            trace_id=trace_id,
            doc_ids=doc_ids,
            security_levels=security_levels,
            policies=policies,
            retry_counter=retry_counter,
            timeout_seconds=timeout_seconds,
            adapter=self,
            response_format=response_format,
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
        or "service unavailable" in msg
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


def _request_timeout_options(adapter):
    from mech_chatbot.rag.execution import current_request_budget, current_request_checkpoint

    budget = current_request_budget()
    if budget is None:
        return {}
    # The executor binds budget and checkpoint together for the same request.
    checkpoint = current_request_checkpoint()
    if checkpoint is not None:
        checkpoint('provider')
    remaining = budget.deadline_monotonic - monotonic()
    if remaining <= 0:
        budget.deadline_exceeded = True
        raise TimeoutError('RAG request deadline exceeded before provider call')
    return {'timeout': min(remaining, adapter.settings.timeout_seconds) if adapter is not None else remaining}


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
    timeout_seconds=None,
    *,
    adapter: LlmAdapter | None = None,
    response_format=None,
):
    client = _get_runtime_llm() if adapter is None else adapter.client
    configured_timeout = (
        float(adapter.settings.timeout_seconds)
        if adapter is not None
        else 120.0
    )
    if timeout_seconds is not None:
        configured_timeout = min(configured_timeout, float(timeout_seconds))
    effective_timeout = remaining_request_timeout(
        configured_timeout,
        stage="provider generation",
    )
    model = get_llm_model_name(adapter)
    endpoint = get_llm_endpoint(adapter)
    with audited_external_call(
        provider=compatible_provider_name(endpoint),
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
        options = _request_timeout_options(adapter)
        options = {**options, "timeout": min(effective_timeout, options.get("timeout", effective_timeout))}
        if response_format is not None:
            options = {**options, 'response_format': deepcopy(response_format)}
            if compatible_provider_name(endpoint) == 'openrouter':
                options = {**options, 'extra_body': {'provider': {'require_parameters': True}}}
        try:
            response = client.invoke(messages, **options)
        except Exception:
            record_provider_usage(None, model=model, trace_id=trace_id, surface=surface)
            raise
        record_provider_usage(response, model=model, trace_id=trace_id, surface=surface)
        metadata = getattr(response, 'response_metadata', None)
        if isinstance(metadata, dict) and metadata.get('finish_reason') == 'error':
            raise ProviderCompletionError('provider reported a completion error')
        return response


def cohere_invoke(
    messages,
    surface="generation",
    trace_id=None,
    doc_ids=None,
    security_levels=None,
    policies=None,
    retry_counter=None,
    timeout_seconds=None,
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
        timeout_seconds=timeout_seconds,
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
