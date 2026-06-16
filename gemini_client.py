import os
from google import genai
from google.genai import errors as genai_errors

DEFAULT_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
_PLACEHOLDER_KEY = "DIEN_KEY_CUA_BAN_VAO_DAY"

def is_retryable_error(exc) -> bool:
    """Retry khi bi rate-limit (429) hoac loi server (5xx) cua Gemini (google-genai)."""
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        return code == 429 or (isinstance(code, int) and code >= 500)
    return False

class GeminiVisionModel:
    """
    Wrapper giu NGUYEN interface cu `.generate_content(...)` de cac call-site
    (rag_logic / pdf_processor) khong phai doi logic khi migrate sang google-genai.
    """

    def __init__(self, api_key: str, model_name: str = DEFAULT_VISION_MODEL):
        self._client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def generate_content(self, contents):
        # contents co the la str (chi prompt) hoac list [prompt, PIL.Image]
        parts = list(contents) if isinstance(contents, (list, tuple)) else [contents]
        return self._client.models.generate_content(
            model=self.model_name,
            contents=parts,
        )

def build_vision_model(model_name: str = DEFAULT_VISION_MODEL):
    """Tra ve GeminiVisionModel neu co API key hop le, nguoc lai None."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key and api_key != _PLACEHOLDER_KEY:
        return GeminiVisionModel(api_key, model_name)
    return None