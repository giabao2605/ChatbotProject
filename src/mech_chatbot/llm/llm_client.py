import os
from tenacity import retry, retry_if_exception, wait_exponential, stop_after_attempt
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from mech_chatbot.config.logging import log_trace
from mech_chatbot.llm.external_ai import (
    audited_external_call,
    get_provider_runtime,
    text_byte_count,
    text_char_count,
)

load_dotenv()


def _provider_runtime():
    return get_provider_runtime(
        "proxyllm",
        fallback_endpoint=(
            os.getenv("PROXYLLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.proxyllm.eu/v1"
        ),
        fallback_model=os.getenv("GPT_MODEL_NAME", "gpt-5.4"),
        fallback_secret_envs=("PROXYLLM_API_KEY", "OPENAI_API_KEY", "GPT_API_KEY"),
    )


def _get_api_key():
    """Resolve the key referenced by the managed ProxyLLM profile."""
    return _provider_runtime().api_key


def _get_base_url():
    return _provider_runtime().endpoint


def get_llm_endpoint():
    """Public metadata-only endpoint accessor for the external AI audit."""
    return _get_base_url()


def get_llm_model_name():
    return _provider_runtime().model


def _make_llm(max_tokens=None, runtime=None):
    runtime = runtime or _provider_runtime()
    api_key = runtime.api_key
    if not api_key:
        raise ValueError("Secret reference cua ProxyLLM chua resolve duoc API key")

    return ChatOpenAI(
        model=runtime.model,
        api_key=api_key,
        base_url=runtime.endpoint,
        temperature=float(os.getenv("GPT_TEMPERATURE", "0")),
        max_tokens=max_tokens or int(os.getenv("GPT_MAX_OUTPUT_TOKENS", "4000")),
        timeout=float(os.getenv("GPT_TIMEOUT_SECONDS", "120")),
        max_retries=0,  # retry do tenacity ben duoi xu ly de log ro hon
    )


_runtime_llm = None
_runtime_llm_signature = None


def _get_runtime_llm():
    """Reuse the adapter until a managed provider profile changes."""
    global _runtime_llm, _runtime_llm_signature
    runtime = _provider_runtime()
    signature = (runtime.endpoint, runtime.model, runtime.api_key)
    if _runtime_llm is None or _runtime_llm_signature != signature:
        _runtime_llm = _make_llm(runtime=runtime)
        _runtime_llm_signature = signature
    return _runtime_llm


# Compatibility export for modules initialized before a profile update.
llm = _get_runtime_llm()


def _is_gpt_rate_limit(exc):
    msg = str(exc).lower()
    return (
        "429" in msg
        or "503" in msg
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


# Giu ten cu de cac file khac khong phai sua nhieu.
def _is_cohere_rate_limit(exc):
    return _is_gpt_rate_limit(exc)


def _before_llm_retry(retry_state):
    kwargs = retry_state.kwargs or {}
    counter = kwargs.get("retry_counter")
    if isinstance(counter, dict):
        counter["count"] = int(counter.get("count") or 0) + 1
    error = retry_state.outcome.exception() if retry_state.outcome else None
    log_trace(
        "llm_retry",
        kwargs.get("trace_id"),
        surface=kwargs.get("surface") or "generation",
        attempt=retry_state.attempt_number,
        max_attempts=4,
        error=type(error).__name__ if error else "unknown",
    )


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
):
    with audited_external_call(
        provider="proxyllm",
        model=get_llm_model_name(),
        endpoint=get_llm_endpoint(),
        surface=surface,
        trace_id=trace_id,
        doc_ids=doc_ids,
        security_levels=security_levels,
        policies=policies,
        input_chars=text_char_count(messages),
        input_bytes=text_byte_count(messages),
    ):
        return _get_runtime_llm().invoke(messages)


# Alias tuong thich nguoc: code cu goi cohere_invoke -> nay thuc chat goi GPT-5.4
def cohere_invoke(
    messages,
    surface="generation",
    trace_id=None,
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
    )


def get_gpt_llm():
    return _get_runtime_llm()


# Alias tuong thich nguoc: code cu goi get_cohere_llm -> nay tra GPT-5.4 llm
def get_cohere_llm():
    return _get_runtime_llm()
