from __future__ import annotations

import base64
from contextlib import nullcontext
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tenacity import Future, RetryError

from mech_chatbot.config.settings import VisionSettings
from mech_chatbot.llm import vision_client


pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class FakeImage:
    size: tuple[int, int] = (200, 100)
    mode: str = "RGB"

    def resize(self, size: tuple[int, int]):
        return FakeImage(size=size, mode=self.mode)

    def convert(self, mode: str):
        return FakeImage(size=self.size, mode=mode)

    def save(self, buffer, *, format: str, **_options):
        width, height = self.size
        buffer.write(f"{format}:{self.mode}:{width}x{height}".encode("ascii"))


class ProviderError(Exception):
    def __init__(self, message: str, *, status_code=None, code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _settings(**overrides) -> VisionSettings:
    values = {
        "api_key": "secret",
        "base_url": "https://vision.example/v1",
        "model_name": "vision-model",
        "image_format": "jpeg",
        "max_edge": 0,
        "jpeg_quality": 85,
        "temperature": 0.0,
        "max_output_tokens": 4096,
        "timeout_seconds": 180.0,
        "min_interval_seconds": 0.0,
    }
    return VisionSettings(**{**values, **overrides})


def _install_openai_boundary(monkeypatch, *, response_text="recognized", error=None):
    if error is None:
        message = SimpleNamespace(content=response_text)
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        create = Mock(return_value=response)
    else:
        create = Mock(side_effect=error)
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
    return constructor, create


@pytest.mark.parametrize(
    ("error", "expected_type", "retryable"),
    [
        (ProviderError("insufficient_quota", status_code=429), "quota_exceeded", False),
        (ProviderError("too many requests", status_code=429), "rate_limit_temporary", True),
        (ProviderError("provider unavailable", status_code=503), "server_error", True),
        (ProviderError("invalid API key", status_code=401), "auth_error", False),
        (ProviderError("bad request", status_code=400), "unknown_error", False),
    ],
)
def test_vision_errors_are_classified_for_caller_recovery(error, expected_type, retryable):
    assert vision_client.classify_vision_error(error) == expected_type
    assert vision_client.is_retryable_error(error) is retryable


def test_retry_error_description_exposes_root_provider_failure():
    attempt = Future(1)
    attempt.set_exception(ProviderError("temporary outage", status_code=503))
    error = RetryError(attempt)

    description = vision_client.describe_vision_error(error)

    assert description == (
        "RetryError -> [SERVER_ERROR], ProviderError, code=503, "
        "message=temporary outage"
    )


def test_direct_error_description_keeps_provider_code_and_message():
    error = ProviderError("access denied", code=403)

    assert vision_client.describe_vision_error(error) == (
        "[UNKNOWN_ERROR], ProviderError, code=403, message=access denied"
    )


def test_generate_content_returns_normalized_text_and_request_settings(monkeypatch):
    constructor, create = _install_openai_boundary(
        monkeypatch,
        response_text="bearing drawing",
    )
    model = vision_client.GPTVisionModel(
        _settings(
            temperature=0.2,
            max_output_tokens=512,
            timeout_seconds=30.0,
        )
    )
    result = model.generate_content("read the drawing")

    assert result == vision_client.GPTVisionResponse(text="bearing drawing")
    constructor.assert_called_once_with(
        api_key="secret",
        base_url="https://vision.example/v1",
    )
    request = create.call_args.kwargs
    assert request == {
        "model": "vision-model",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "read the drawing"}],
            }
        ],
        "temperature": 0.2,
        "max_tokens": 512,
        "timeout": 30.0,
    }


@pytest.mark.parametrize(
    ("image_format", "image_mode", "expected_mime", "expected_bytes"),
    [
        ("jpeg", "RGBA", "image/jpeg", b"JPEG:RGB:100x50"),
        ("png", "P", "image/png", b"PNG:RGB:100x50"),
    ],
)
def test_generate_content_sends_scaled_image_as_data_url(
    monkeypatch,
    image_format,
    image_mode,
    expected_mime,
    expected_bytes,
):
    _, create = _install_openai_boundary(monkeypatch)

    model = vision_client.GPTVisionModel(
        _settings(image_format=image_format, max_edge=100)
    )
    model.generate_content(["extract dimensions", FakeImage(mode=image_mode)])

    content = create.call_args.kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "extract dimensions"}
    data_url = content[1]["image_url"]["url"]
    prefix, encoded = data_url.split(",", maxsplit=1)
    assert prefix == f"data:{expected_mime};base64"
    assert base64.b64decode(encoded) == expected_bytes


def test_generate_content_propagates_provider_failure(monkeypatch):
    provider_error = ProviderError("upstream unavailable", status_code=503)
    _install_openai_boundary(monkeypatch, error=provider_error)
    model = vision_client.GPTVisionModel(_settings())

    with pytest.raises(ProviderError, match="upstream unavailable"):
        model.generate_content("read")


def test_generate_content_respects_positive_throttle_interval(monkeypatch):
    _install_openai_boundary(monkeypatch)
    monotonic = Mock(side_effect=(10.0, 11.0))
    sleep = Mock()
    monkeypatch.setattr(vision_client.time, "monotonic", monotonic)
    monkeypatch.setattr(vision_client.time, "sleep", sleep)
    model = vision_client.GPTVisionModel(_settings(min_interval_seconds=1.0))
    model._last_call_at = 9.5

    model.generate_content("read")

    sleep.assert_called_once_with(0.5)


@pytest.mark.parametrize("api_key", [None, "DIEN_KEY_CUA_BAN_VAO_DAY"])
def test_build_vision_model_rejects_unusable_secret(api_key):
    with pytest.raises(ValueError, match="provider API key is not configured"):
        vision_client.build_vision_model(_settings(api_key=api_key))


def test_build_vision_model_does_not_share_a_process_global_cache(monkeypatch):
    constructor = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(vision_client, "OpenAI", constructor)

    first = vision_client.build_vision_model(_settings())
    second = vision_client.build_vision_model(_settings())

    assert first is not second
    assert constructor.call_count == 2


def test_build_vision_model_uses_model_from_snapshot(monkeypatch):
    constructor = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(vision_client, "OpenAI", constructor)

    model = vision_client.build_vision_model(
        _settings(model_name="explicit-model")
    )

    assert model.model_name == "explicit-model"


def test_build_vision_model_rejects_invalid_image_format_without_secret_leak():
    settings = _settings(image_format="gif")

    with pytest.raises(ValueError, match="vision image format") as error:
        vision_client.build_vision_model(settings)

    assert settings.api_key not in str(error.value)
