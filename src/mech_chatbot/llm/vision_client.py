"""OpenAI-compatible vision adapter built from an immutable settings snapshot."""

from __future__ import annotations

import base64
import io
import json
import threading
import time
from dataclasses import dataclass, replace

from openai import OpenAI
from tenacity import RetryError

from mech_chatbot.config.settings import ExternalAiSettings, VisionSettings
from mech_chatbot.llm.external_ai import (
    DEFAULT_EXTERNAL_AI_SETTINGS,
    audited_external_call,
    normalize_text_result,
)


DEFAULT_VISION_MODEL = "gpt-5.4"
_PLACEHOLDER_KEY = "DIEN_KEY_CUA_BAN_VAO_DAY"
_MISSING_PROVIDER_KEY = "provider API key is not configured"
_DEFAULT_PROVIDER_ENDPOINT = "https://api.proxyllm.eu/v1"


@dataclass(frozen=True, slots=True)
class GPTVisionResponse:
    text: str


def _unwrap_retry_error(exc):
    if isinstance(exc, RetryError):
        try:
            return exc.last_attempt.exception()
        except Exception:
            return exc
    return exc


def classify_vision_error(exc) -> str:
    """Classify an upstream failure for the ingestion recovery policy."""

    root = _unwrap_retry_error(exc)
    msg = str(root).lower()
    code = getattr(root, "status_code", None) or getattr(root, "code", None)

    if "insufficient_quota" in msg or "quota" in msg or "credit" in msg:
        return "quota_exceeded"
    if code == 429 or "rate limit" in msg or "too many requests" in msg:
        return "rate_limit_temporary"
    if isinstance(code, int) and code >= 500:
        return "server_error"
    if (
        "api key" in msg
        or "permission" in msg
        or "unauthorized" in msg
        or "401" in msg
    ):
        return "auth_error"
    return "unknown_error"


def is_retryable_error(exc) -> bool:
    return classify_vision_error(exc) in {"rate_limit_temporary", "server_error"}


def describe_vision_error(exc) -> str:
    """Describe a provider error without including configured credentials."""

    root = _unwrap_retry_error(exc)
    err_type = classify_vision_error(exc)
    code = getattr(root, "status_code", None) or getattr(root, "code", None)
    message = getattr(root, "message", None) or str(root)
    parts = [f"[{err_type.upper()}]", type(root).__name__]
    if code is not None:
        parts.append(f"code={code}")
    if message:
        parts.append(f"message={message}")
    if root is not exc:
        return f"{type(exc).__name__} -> " + ", ".join(parts)
    return ", ".join(parts)


def _validated_settings(settings: VisionSettings) -> VisionSettings:
    api_key = str(settings.api_key or "").strip()
    if not api_key or api_key == _PLACEHOLDER_KEY:
        raise ValueError(_MISSING_PROVIDER_KEY)
    image_format = str(settings.image_format or "jpeg").strip().lower()
    if image_format not in {"jpeg", "png"}:
        raise ValueError("vision image format must be 'jpeg' or 'png'")
    model_name = str(settings.model_name or "").strip()
    if not model_name:
        raise ValueError("vision model is not configured")
    if (
        int(settings.max_edge) < 0
        or not 1 <= int(settings.jpeg_quality) <= 100
        or int(settings.max_output_tokens) <= 0
        or float(settings.timeout_seconds) <= 0
        or float(settings.min_interval_seconds) < 0
    ):
        raise ValueError("vision provider limits are invalid")
    return replace(
        settings,
        api_key=api_key,
        base_url=(
            str(settings.base_url).strip()
            if settings.base_url
            else _DEFAULT_PROVIDER_ENDPOINT
        ),
        model_name=model_name,
        image_format=image_format,
        max_edge=int(settings.max_edge),
        jpeg_quality=int(settings.jpeg_quality),
    )


def _pil_to_data_url(image, settings: VisionSettings):
    fmt = settings.image_format
    max_edge = settings.max_edge
    if max_edge and hasattr(image, "size"):
        width, height = image.size
        longest = max(width, height)
        if longest > max_edge:
            scale = max_edge / float(longest)
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale)))
            )

    buffer = io.BytesIO()
    if fmt == "png":
        if getattr(image, "mode", "RGB") not in ("RGB", "L", "RGBA"):
            image = image.convert("RGB")
        image.save(buffer, format="PNG", optimize=True)
        mime = "image/png"
    else:
        if getattr(image, "mode", "RGB") not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(
            buffer,
            format="JPEG",
            quality=settings.jpeg_quality,
            optimize=True,
        )
        mime = "image/jpeg"
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class GPTVisionModel:
    """Vision adapter with request options fixed at construction time."""

    def __init__(
        self,
        settings_or_api_key: VisionSettings | str,
        model_name: str = DEFAULT_VISION_MODEL,
        endpoint: str | None = None,
        *,
        image_format: str = "jpeg",
        max_edge: int = 0,
        jpeg_quality: int = 85,
        temperature: float = 0.0,
        max_output_tokens: int = 4096,
        timeout_seconds: float = 120.0,
        min_interval_seconds: float = 0.0,
        external_ai_settings: ExternalAiSettings = DEFAULT_EXTERNAL_AI_SETTINGS,
    ):
        if isinstance(settings_or_api_key, VisionSettings):
            settings = _validated_settings(settings_or_api_key)
            min_interval_seconds = float(settings_or_api_key.min_interval_seconds)
        else:
            settings = _validated_settings(
                VisionSettings(
                    api_key=settings_or_api_key,
                    base_url=endpoint,
                    model_name=model_name,
                    image_format=image_format,
                    max_edge=max_edge,
                    jpeg_quality=jpeg_quality,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    timeout_seconds=timeout_seconds,
                    min_interval_seconds=min_interval_seconds,
                )
            )
        self.settings = settings
        self.external_ai_settings = external_ai_settings
        self.model_name = settings.model_name
        self._endpoint = settings.base_url
        self._client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
        self._min_interval_seconds = max(0.0, min_interval_seconds)
        self._call_lock = threading.Lock()
        self._last_call_at = 0.0

    def _throttle(self) -> None:
        if self._min_interval_seconds <= 0:
            return
        with self._call_lock:
            now = time.monotonic()
            wait_for = self._min_interval_seconds - (now - self._last_call_at)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_call_at = time.monotonic()

    def generate_content(self, contents):
        self._throttle()
        parts = list(contents) if isinstance(contents, (list, tuple)) else [contents]
        user_content = []
        for part in parts:
            if hasattr(part, "save") and hasattr(part, "mode"):
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": _pil_to_data_url(part, self.settings),
                        },
                    }
                )
            else:
                user_content.append({"type": "text", "text": str(part)})

        serialized_content = json.dumps(user_content, ensure_ascii=False)
        with audited_external_call(
            provider="proxyllm",
            model=self.model_name,
            endpoint=self._endpoint,
            surface="vision_ocr",
            input_chars=len(serialized_content),
            input_bytes=len(serialized_content.encode("utf-8")),
            settings=self.external_ai_settings,
        ):
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": user_content}],
                temperature=self.settings.temperature,
                max_tokens=self.settings.max_output_tokens,
                timeout=self.settings.timeout_seconds,
            )
        normalized = normalize_text_result(
            response.choices[0].message,
            provider="proxyllm",
            model=self.model_name,
            kind="vision_extraction",
        )
        return GPTVisionResponse(text=normalized.text or "")


def build_vision_model(
    settings: VisionSettings,
    *,
    external_ai_settings: ExternalAiSettings = DEFAULT_EXTERNAL_AI_SETTINGS,
) -> GPTVisionModel:
    """Build one process-owned model without a cache or ambient environment."""

    return GPTVisionModel(
        settings,
        external_ai_settings=external_ai_settings,
    )


__all__ = [
    "DEFAULT_VISION_MODEL",
    "GPTVisionModel",
    "GPTVisionResponse",
    "build_vision_model",
    "classify_vision_error",
    "describe_vision_error",
    "is_retryable_error",
]
