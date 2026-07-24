from __future__ import annotations

from contextlib import nullcontext
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
    client.invoke.assert_called_once_with(["prompt"])


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
