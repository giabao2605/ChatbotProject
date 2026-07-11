"""
OpenAI-compatible vision client for ProxyLLM GPT-5.4.
"""

import base64
import io
import json
import os
import threading
import time
from dataclasses import dataclass
from tenacity import RetryError
from openai import OpenAI
from mech_chatbot.llm.external_ai import (
    audited_external_call,
    get_provider_runtime,
    normalize_text_result,
)

DEFAULT_VISION_MODEL = os.getenv("GPT_VISION_MODEL_NAME", os.getenv("GPT_MODEL_NAME", "gpt-5.4"))
_PLACEHOLDER_KEY = "DIEN_KEY_CUA_BAN_VAO_DAY"
_GPT_CALL_LOCK = threading.Lock()
_LAST_GPT_CALL_AT = 0.0
_VISION_MODEL_CACHE = None
_VISION_MODEL_SIGNATURE = None


@dataclass
class GPTVisionResponse:
    text: str


def _vision_runtime():
    return get_provider_runtime(
        "proxyllm",
        fallback_endpoint=(
            os.getenv("PROXYLLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.proxyllm.eu/v1"
        ),
        fallback_model=(
            os.getenv("GPT_VISION_MODEL_NAME")
            or os.getenv("GPT_MODEL_NAME")
            or "gpt-5.4"
        ),
        fallback_secret_envs=("PROXYLLM_API_KEY", "OPENAI_API_KEY", "GPT_API_KEY"),
    )


def _get_api_key():
    return _vision_runtime().api_key


def _get_base_url():
    return _vision_runtime().endpoint


def _unwrap_retry_error(exc):
    if isinstance(exc, RetryError):
        try:
            return exc.last_attempt.exception()
        except Exception:
            return exc
    return exc


def classify_vision_error(exc) -> str:
    """Phan loai loi cho GPT/ProxyLLM vision."""
    root = _unwrap_retry_error(exc)
    msg = str(root).lower()
    code = getattr(root, "status_code", None) or getattr(root, "code", None)

    if "insufficient_quota" in msg or "quota" in msg or "credit" in msg:
        return "quota_exceeded"
    if code == 429 or "rate limit" in msg or "too many requests" in msg:
        return "rate_limit_temporary"
    if isinstance(code, int) and code >= 500:
        return "server_error"
    if "api key" in msg or "permission" in msg or "unauthorized" in msg or "401" in msg:
        return "auth_error"
    return "unknown_error"


def is_retryable_error(exc) -> bool:
    err_type = classify_vision_error(exc)
    return err_type in ["rate_limit_temporary", "server_error"]


def describe_vision_error(exc) -> str:
    """Mo ta loi GPT/ProxyLLM vision."""
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


def _throttle_gpt_call():
    """Giam nguy co rate-limit khi re-ingest nhieu ban ve lien tiep."""
    try:
        min_interval = float(os.getenv("GPT_MIN_INTERVAL_SECONDS", "0"))
    except ValueError:
        min_interval = 0.0
    if min_interval <= 0:
        return

    global _LAST_GPT_CALL_AT
    with _GPT_CALL_LOCK:
        now = time.monotonic()
        wait_for = min_interval - (now - _LAST_GPT_CALL_AT)
        if wait_for > 0:
            time.sleep(wait_for)
        _LAST_GPT_CALL_AT = time.monotonic()


def _pil_to_data_url(image):
    """Ma hoa anh trang PDF thanh data URL cho Vision API.

    Cau hinh qua env:
      - GPT_VISION_IMAGE_FORMAT = jpeg (mac dinh) | png. Dung PNG cho ban ve line-art
        de giu net (JPEG gay artifact lam mo net manh / chu nho).
      - GPT_VISION_MAX_EDGE = 0 (mac dinh, giu nguyen) hoac so px canh dai toi da; giup
        kiem soat viec downscale (tranh phu thuoc hoan toan vao downscale phia server).
      - GPT_VISION_JPEG_QUALITY = 85 (chi ap dung khi format=jpeg).
    """
    fmt = os.getenv("GPT_VISION_IMAGE_FORMAT", "jpeg").strip().lower()

    # Optional: gioi han canh dai (0 = giu nguyen).
    try:
        max_edge = int(os.getenv("GPT_VISION_MAX_EDGE", "0"))
    except ValueError:
        max_edge = 0
    if max_edge and hasattr(image, "size"):
        w, h = image.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / float(longest)
            image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))))

    buf = io.BytesIO()
    if fmt == "png":
        if getattr(image, "mode", "RGB") not in ("RGB", "L", "RGBA"):
            image = image.convert("RGB")
        image.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        # Anh render tu PDF thuong la RGB/RGBA; JPEG nhe hon PNG cho API vision.
        if getattr(image, "mode", "RGB") not in ("RGB", "L"):
            image = image.convert("RGB")
        image.save(buf, format="JPEG", quality=int(os.getenv("GPT_VISION_JPEG_QUALITY", "85")), optimize=True)
        mime = "image/jpeg"
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:{mime};base64,{b64}"


class GPTVisionModel:
    """
    Wrapper `.generate_content(...)`.
    contents co the la str hoac list [prompt, PIL.Image].
    Tra ve object co `.text`.
    """

    def __init__(self, api_key: str, model_name: str = DEFAULT_VISION_MODEL, endpoint: str | None = None):
        self._endpoint = endpoint or _get_base_url()
        self._client = OpenAI(api_key=api_key, base_url=self._endpoint)
        self.model_name = model_name

    def generate_content(self, contents):
        _throttle_gpt_call()
        parts = list(contents) if isinstance(contents, (list, tuple)) else [contents]

        user_content = []
        for part in parts:
            # PIL Image
            if hasattr(part, "save") and hasattr(part, "mode"):
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": _pil_to_data_url(part)},
                })
            else:
                user_content.append({"type": "text", "text": str(part)})

        serialized_content = json.dumps(user_content, ensure_ascii=False)
        input_chars = len(serialized_content)
        with audited_external_call(
            provider="proxyllm",
            model=self.model_name,
            endpoint=self._endpoint,
            surface="vision_ocr",
            input_chars=input_chars,
            input_bytes=len(serialized_content.encode("utf-8")),
        ):
            resp = self._client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": user_content}],
                temperature=float(os.getenv("GPT_VISION_TEMPERATURE", "0")),
                max_tokens=int(os.getenv("GPT_VISION_MAX_OUTPUT_TOKENS", "4096")),
                timeout=float(os.getenv("GPT_TIMEOUT_SECONDS", "180")),
            )
        normalized = normalize_text_result(
            resp.choices[0].message,
            provider="proxyllm",
            model=self.model_name,
            kind="vision_extraction",
        )
        text = normalized.text or ""
        return GPTVisionResponse(text=text)


def build_vision_model(model_name: str | None = None):
    """Tra ve GPTVisionModel neu co ProxyLLM/OpenAI API key hop le, nguoc lai None."""
    global _VISION_MODEL_CACHE, _VISION_MODEL_SIGNATURE
    runtime = _vision_runtime()
    api_key = runtime.api_key
    selected_model = model_name or runtime.model or DEFAULT_VISION_MODEL
    signature = (runtime.endpoint, selected_model, api_key)
    with _GPT_CALL_LOCK:
        if _VISION_MODEL_SIGNATURE == signature:
            return _VISION_MODEL_CACHE
        _VISION_MODEL_SIGNATURE = signature
        _VISION_MODEL_CACHE = (
            GPTVisionModel(api_key, selected_model, runtime.endpoint)
            if api_key and api_key != _PLACEHOLDER_KEY else None
        )
        return _VISION_MODEL_CACHE
