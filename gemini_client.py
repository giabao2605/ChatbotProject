"""
OpenAI-compatible vision client for ProxyLLM GPT-5.4.

Giu lai ten file/ham cu (gemini_client.py, build_vision_model, describe_gemini_error)
de cac call-site hien tai trong pdf_processor.py/rag_logic.py khong can doi nhieu.
"""

import base64
import io
import os
import threading
import time
from dataclasses import dataclass
from tenacity import RetryError
from openai import OpenAI

DEFAULT_VISION_MODEL = os.getenv("GPT_VISION_MODEL_NAME", os.getenv("GPT_MODEL_NAME", "gpt-5.4"))
_PLACEHOLDER_KEY = "DIEN_KEY_CUA_BAN_VAO_DAY"
_GPT_CALL_LOCK = threading.Lock()
_LAST_GPT_CALL_AT = 0.0


@dataclass
class GPTVisionResponse:
    text: str


def _get_api_key():
    return (
        os.getenv("PROXYLLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GPT_API_KEY")
    )


def _get_base_url():
    return (
        os.getenv("PROXYLLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or "https://api.proxyllm.eu/v1"
    )


def _unwrap_retry_error(exc):
    if isinstance(exc, RetryError):
        try:
            return exc.last_attempt.exception()
        except Exception:
            return exc
    return exc


def classify_gemini_error(exc) -> str:
    """Ten ham cu, nhung phan loai loi cho GPT/ProxyLLM."""
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
    err_type = classify_gemini_error(exc)
    return err_type in ["rate_limit_temporary", "server_error"]


def describe_gemini_error(exc) -> str:
    """Ten ham cu de code hien tai khong bi vo; noi dung mo ta GPT/ProxyLLM error."""
    root = _unwrap_retry_error(exc)
    err_type = classify_gemini_error(exc)
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
    buf = io.BytesIO()
    # Anh render tu PDF thuong la RGB/RGBA; JPEG nhe hon PNG cho API vision.
    if getattr(image, "mode", "RGB") not in ("RGB", "L"):
        image = image.convert("RGB")
    image.save(buf, format="JPEG", quality=int(os.getenv("GPT_VISION_JPEG_QUALITY", "85")), optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


class GPTVisionModel:
    """
    Wrapper giu interface cu `.generate_content(...)`.
    contents co the la str hoac list [prompt, PIL.Image].
    Tra ve object co `.text` giong Gemini response cu.
    """

    def __init__(self, api_key: str, model_name: str = DEFAULT_VISION_MODEL):
        self._client = OpenAI(api_key=api_key, base_url=_get_base_url())
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

        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": user_content}],
            temperature=float(os.getenv("GPT_VISION_TEMPERATURE", "0")),
            max_tokens=int(os.getenv("GPT_VISION_MAX_OUTPUT_TOKENS", "2500")),
            timeout=float(os.getenv("GPT_TIMEOUT_SECONDS", "180")),
        )
        text = resp.choices[0].message.content or ""
        return GPTVisionResponse(text=text)


def build_vision_model(model_name: str = DEFAULT_VISION_MODEL):
    """Tra ve GPTVisionModel neu co ProxyLLM/OpenAI API key hop le, nguoc lai None."""
    api_key = _get_api_key()
    if api_key and api_key != _PLACEHOLDER_KEY:
        return GPTVisionModel(api_key, model_name)
    return None
