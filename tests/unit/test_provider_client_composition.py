from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mech_chatbot.config.settings import (
    ExternalAiSettings,
    LlmSettings,
    VisionSettings,
)
from mech_chatbot.llm import llm_client, vision_client


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "endpoint, provider",
    [
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://openrouter.ai/api/v1/", "openrouter"),
        ("http://openrouter.ai/api/v1", "proxyllm"),
        ("https://openrouter.ai.evil.example/api/v1", "proxyllm"),
        ("https://openrouter.ai@proxy.example/api/v1", "proxyllm"),
        ("https://openrouter.ai/api/v1/proxy", "proxyllm"),
        ("https://api.proxyllm.eu/v1", "proxyllm"),
    ],
)
def test_llm_audit_identifies_only_openrouter_endpoint(monkeypatch, endpoint, provider):
    audit = Mock(return_value=nullcontext())
    monkeypatch.setattr(llm_client, "ChatOpenAI", Mock(return_value=Mock()))
    monkeypatch.setattr(llm_client, "audited_external_call", audit)
    adapter = llm_client.build_llm_adapter(replace(_llm_settings(), base_url=endpoint))

    adapter.invoke(["prompt"])

    assert audit.call_args.kwargs["provider"] == provider


def test_openrouter_luna_text_omits_temperature(monkeypatch):
    constructor = Mock(return_value=Mock())
    monkeypatch.setattr(llm_client, "ChatOpenAI", constructor)
    llm_client.build_llm_adapter(replace(
        _llm_settings(), base_url="https://openrouter.ai/api/v1", model_name="openai/gpt-5.6-luna"
    ))

    assert constructor.call_args.kwargs["temperature"] is None
    assert constructor.call_args.kwargs["max_tokens"] == 321


@pytest.mark.parametrize("provider", ["openrouter", "proxyllm"])
def test_luna_vision_request_options_and_audit_follow_provider(monkeypatch, provider):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="read"))])
    create = Mock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(vision_client, "OpenAI", Mock(return_value=client))
    audit = Mock(return_value=nullcontext())
    monkeypatch.setattr(vision_client, "audited_external_call", audit)
    normalize = Mock(wraps=vision_client.normalize_text_result)
    monkeypatch.setattr(vision_client, "normalize_text_result", normalize)
    endpoint = "https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.proxyllm.eu/v1"
    model = vision_client.build_vision_model(replace(
        _vision_settings(), base_url=endpoint, model_name="openai/gpt-5.6-luna"
    ))

    assert model.generate_content("read").text == "read"

    request = create.call_args.kwargs
    assert audit.call_args.kwargs["provider"] == provider
    assert normalize.call_args.kwargs["provider"] == provider
    if provider == "openrouter":
        assert request["max_tokens"] == 654
        assert "temperature" not in request
        assert "max_completion_tokens" not in request
    else:
        assert request["max_tokens"] == 654
        assert request["temperature"] == 0.3
        assert "max_completion_tokens" not in request


def _llm_settings() -> LlmSettings:
    return LlmSettings(
        api_key="snapshot-" + "key",
        base_url="https://snapshot.example/v1",
        model_name="snapshot-model",
        temperature=0.25,
        max_output_tokens=321,
        timeout_seconds=17.0,
        min_interval_seconds=0.0,
    )


def _vision_settings() -> VisionSettings:
    return VisionSettings(
        api_key="snapshot-" + "key",
        base_url="https://snapshot.example/v1",
        model_name="snapshot-vision",
        image_format="png",
        max_edge=128,
        jpeg_quality=91,
        temperature=0.3,
        max_output_tokens=654,
        timeout_seconds=19.0,
        min_interval_seconds=0.0,
    )


def _external_ai_settings() -> ExternalAiSettings:
    return ExternalAiSettings(
        application_environment="pilot",
        local_development=False,
        processing_policy="internal_only",
    )


def test_llm_adapter_uses_only_the_explicit_settings_snapshot(monkeypatch):
    response = object()
    client = SimpleNamespace(invoke=Mock(return_value=response))
    constructor = Mock(return_value=client)
    monkeypatch.setattr(llm_client, "ChatOpenAI", constructor)
    monkeypatch.setattr(
        llm_client,
        "audited_external_call",
        lambda **_kwargs: nullcontext(),
    )

    adapter = llm_client.build_llm_adapter(_llm_settings())
    monkeypatch.setenv("GPT_MODEL_NAME", "ambient-model")
    monkeypatch.setenv("GPT_TEMPERATURE", "0.99")

    assert adapter.invoke(["prompt"], surface="generation") is response
    constructor.assert_called_once_with(
        model="snapshot-model",
        api_key="snapshot-" + "key",
        base_url="https://snapshot.example/v1",
        temperature=0.25,
        max_tokens=321,
        timeout=17.0,
        max_retries=0,
    )
    client.invoke.assert_called_once_with(["prompt"], timeout=17.0)


def test_llm_adapter_bounds_provider_timeout_by_request_budget(monkeypatch):
    client = SimpleNamespace(invoke=Mock(return_value=object()))
    monkeypatch.setattr(llm_client, "ChatOpenAI", Mock(return_value=client))
    monkeypatch.setattr(
        llm_client,
        "audited_external_call",
        lambda **_kwargs: nullcontext(),
    )
    remaining = Mock(return_value=4.25)
    monkeypatch.setattr(llm_client, "remaining_request_timeout", remaining)

    adapter = llm_client.build_llm_adapter(_llm_settings())
    adapter.invoke(["prompt"], timeout_seconds=6.0)

    remaining.assert_called_once_with(6.0, stage="provider generation")
    client.invoke.assert_called_once_with(["prompt"], timeout=4.25)


def test_provider_builders_preserve_proxy_endpoint_default(monkeypatch):
    llm_constructor = Mock(return_value=SimpleNamespace())
    vision_constructor = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(llm_client, "ChatOpenAI", llm_constructor)
    monkeypatch.setattr(vision_client, "OpenAI", vision_constructor)

    llm_client.build_llm_adapter(
        _llm_settings().__class__(
            **{
                field: (None if field == "base_url" else getattr(_llm_settings(), field))
                for field in _llm_settings().__dataclass_fields__
            }
        )
    )
    vision_client.build_vision_model(
        _vision_settings().__class__(
            **{
                field: (
                    None
                    if field == "base_url"
                    else getattr(_vision_settings(), field)
                )
                for field in _vision_settings().__dataclass_fields__
            }
        )
    )

    assert llm_constructor.call_args.kwargs["base_url"] == "https://api.proxyllm.eu/v1"
    assert vision_constructor.call_args.kwargs["base_url"] == "https://api.proxyllm.eu/v1"


def test_vision_model_uses_only_the_explicit_settings_snapshot(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="recognized"))]
    )
    create = Mock(return_value=response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    constructor = Mock(return_value=client)
    monkeypatch.setattr(vision_client, "OpenAI", constructor)
    monkeypatch.setattr(
        vision_client,
        "audited_external_call",
        lambda **_kwargs: nullcontext(),
    )

    model = vision_client.build_vision_model(_vision_settings())
    monkeypatch.setenv("GPT_VISION_MODEL_NAME", "ambient-vision")
    monkeypatch.setenv("GPT_VISION_TEMPERATURE", "0.99")

    assert model is not None
    assert model.generate_content("read").text == "recognized"
    constructor.assert_called_once_with(
        api_key="snapshot-" + "key",
        base_url="https://snapshot.example/v1",
    )
    assert create.call_args.kwargs["model"] == "snapshot-vision"
    assert create.call_args.kwargs["temperature"] == 0.3
    assert create.call_args.kwargs["max_tokens"] == 654
    assert create.call_args.kwargs["timeout"] == 19.0


def test_llm_adapter_carries_process_external_ai_policy(monkeypatch):
    audit = Mock(return_value=nullcontext())
    monkeypatch.setattr(llm_client, "ChatOpenAI", Mock(return_value=Mock()))
    monkeypatch.setattr(llm_client, "audited_external_call", audit)
    policy = _external_ai_settings()

    adapter = llm_client.build_llm_adapter(
        _llm_settings(),
        external_ai_settings=policy,
    )
    adapter.invoke(["prompt"])

    assert audit.call_args.kwargs["settings"] is policy


def test_vision_adapter_carries_process_external_ai_policy(monkeypatch):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="recognized"))]
    )
    create = Mock(return_value=response)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    audit = Mock(return_value=nullcontext())
    monkeypatch.setattr(vision_client, "OpenAI", Mock(return_value=client))
    monkeypatch.setattr(vision_client, "audited_external_call", audit)
    policy = _external_ai_settings()

    model = vision_client.build_vision_model(
        _vision_settings(),
        external_ai_settings=policy,
    )
    model.generate_content("read")

    assert audit.call_args.kwargs["settings"] is policy


@pytest.mark.parametrize(
    "builder, settings",
    [
        (llm_client.build_llm_adapter, _llm_settings()),
        (vision_client.build_vision_model, _vision_settings()),
    ],
)
def test_provider_builders_fail_with_sanitized_missing_key(builder, settings):
    missing_key = settings.__class__(
        **{
            field: getattr(settings, field)
            for field in settings.__dataclass_fields__
            if field != "api_key"
        },
        api_key=None,
    )

    with pytest.raises(ValueError, match="provider API key is not configured") as error:
        builder(missing_key)

    assert ("snapshot-" + "key") not in str(error.value)


@pytest.mark.parametrize("key", ["new-router-key", ""])
def test_openrouter_selection_shares_key_without_legacy_fallback(key):
    from mech_chatbot.config.settings import Settings
    settings = Settings.from_env({
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY": key,
        "PROXYLLM_API_KEY": "old-proxy-key",
        "PROXYLLM_BASE_URL": "https://api.proxyllm.eu/v1",
        "OPENAI_API_KEY": "old-openai-key",
    })
    text = LlmSettings.from_settings(settings)
    vision = VisionSettings.from_settings(settings)
    assert text.api_key == vision.api_key == (key or None)
    assert text.base_url == vision.base_url == "https://openrouter.ai/api/v1"
