"""P0 #6 — Validate cau hinh TAP TRUNG + fail-fast + che secret khi log.

Van de cu: 68 cho doc os.getenv rai rac, nhieu cai co default am tham
-> thieu bien moi truong van chay (sai), khong bao loi som.

Giai phap: goi assert_config_valid() MOT LAN luc khoi dong app. Thieu/ sai
kieu -> raise ConfigError ngay, kem danh sach loi ro rang.

Module nay THUAN (chi dung os + stdlib) -> import nhe, test nhanh, khong keo
Qdrant/SQL/LLM.
"""
import os


class ConfigError(RuntimeError):
    """Nem ra khi cau hinh thieu hoac sai kieu (fail-fast luc khoi dong)."""
    pass


# --- Khai bao bien moi truong ----------------------------------------------
REQUIRED_QDRANT = ["QDRANT_URL", "QDRANT_API_KEY"]
LLM_KEY_ANY = ["PROXYLLM_API_KEY", "OPENAI_API_KEY", "GPT_API_KEY"]
LLM_BASE_ANY = ["PROXYLLM_BASE_URL", "OPENAI_BASE_URL"]
REQUIRED_EMBEDDING = ["EMBEDDING_MODEL", "EMBEDDING_DIM"]

NUMERIC_INT = [
    "EMBEDDING_DIM", "EMBEDDING_CHUNK_SIZE", "EMBEDDING_CHUNK_OVERLAP",
    "MAX_CONCURRENT_RAG", "RAG_SERVER_PORT", "MAX_USER_MSG_LEN", "MAX_BOT_MSG_LEN",
    "GPT_MAX_OUTPUT_TOKENS", "GPT_RERANK_MAX_DOCS", "RERANK_TOP_N_CAP",
    "INTENT_MAX_WORKERS", "METADATA_TEXT_LIMIT", "PDF_RENDER_DPI",
    "GPT_VISION_MAX_OUTPUT_TOKENS", "GPT_VISION_JPEG_QUALITY", "RAG_WORKER_TIMEOUT",
]
NUMERIC_FLOAT = [
    "GPT_TEMPERATURE", "GPT_TIMEOUT_SECONDS", "GPT_MIN_INTERVAL_SECONDS",
    "INTENT_TIMEOUT", "RERANK_SCORE_CUTOFF", "GPT_VISION_TEMPERATURE",
]

# Cac key la BI MAT -> KHONG BAO GIO log gia tri that
SECRET_KEYS = {
    "QDRANT_API_KEY", "PROXYLLM_API_KEY", "OPENAI_API_KEY", "GPT_API_KEY",
    "SQL_PASSWORD",
}

# Cac key dung de in summary (khong bao gom secret value)
_SUMMARY_KEYS = (
    REQUIRED_QDRANT + LLM_KEY_ANY + LLM_BASE_ANY + REQUIRED_EMBEDDING + [
        "SQL_SERVER", "SQL_DATABASE", "SQL_DRIVER", "SQL_USERNAME",
        "SQL_TRUSTED_CONNECTION", "SQL_PASSWORD",
        "QDRANT_COLLECTION", "GPT_MODEL_NAME", "MAX_CONCURRENT_RAG",
        "RAG_SERVER_HOST", "RAG_SERVER_PORT",
    ]
)


def _get(env, key):
    return (env.get(key) or "").strip()


def _is_int(v):
    try:
        int(v)
        return True
    except (TypeError, ValueError):
        return False


def _is_float(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _truthy(v):
    return (v or "").strip().lower() in {"1", "true", "yes"}


def validate_config(env=None, *, require_qdrant=True, require_llm=True,
                    require_sql=True, require_embedding=True):
    """Tra ve (errors, warnings). KHONG raise (de test/ in summary linh hoat).

    env: dict-like (mac dinh os.environ). Truyen dict gia de test.
    """
    env = os.environ if env is None else env
    errors = []
    warnings = []

    if require_qdrant:
        for k in REQUIRED_QDRANT:
            if not _get(env, k):
                errors.append(f"Thieu {k} (bat buoc de ket noi Qdrant vector store)")

    if require_llm:
        if not any(_get(env, k) for k in LLM_KEY_ANY):
            errors.append("Thieu LLM API key: can mot trong " + "/".join(LLM_KEY_ANY))
        if not any(_get(env, k) for k in LLM_BASE_ANY):
            errors.append("Thieu LLM base URL: can mot trong " + "/".join(LLM_BASE_ANY))

    if require_embedding:
        for k in REQUIRED_EMBEDDING:
            if not _get(env, k):
                errors.append(f"Thieu {k} (bat buoc cho embedding)")

    if require_sql:
        if not _get(env, "SQL_SERVER"):
            errors.append("Thieu SQL_SERVER")
        if not _get(env, "SQL_DATABASE"):
            errors.append("Thieu SQL_DATABASE")
        if not _truthy(_get(env, "SQL_TRUSTED_CONNECTION")):
            if not _get(env, "SQL_USERNAME") or not _get(env, "SQL_PASSWORD"):
                errors.append(
                    "SQL_TRUSTED_CONNECTION khong bat -> can ca SQL_USERNAME va SQL_PASSWORD"
                )

    # Kiem tra kieu so (chi kiem khi co dat gia tri)
    for k in NUMERIC_INT:
        v = _get(env, k)
        if v and not _is_int(v):
            errors.append(f"{k}='{v}' phai la so nguyen")
    for k in NUMERIC_FLOAT:
        v = _get(env, k)
        if v and not _is_float(v):
            errors.append(f"{k}='{v}' phai la so thuc")

    return errors, warnings


def assert_config_valid(env=None, **kwargs):
    """Fail-fast: raise ConfigError neu co loi. Goi 1 lan luc khoi dong app."""
    errors, warnings = validate_config(env, **kwargs)
    if errors:
        raise ConfigError(
            "Cau hinh khong hop le (" + str(len(errors)) + " loi):\n  - "
            + "\n  - ".join(errors)
        )
    return warnings


def mask_secret(value):
    """Che secret de log an toan: chi lo 4 ky tu cuoi."""
    if not value:
        return "(trong)"
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


def safe_config_summary(env=None):
    """Tra ve dict mo ta cau hinh AN TOAN cho log: secret bi che, khong lo gia tri."""
    env = os.environ if env is None else env
    out = {}
    for k in _SUMMARY_KEYS:
        v = _get(env, k)
        if k in SECRET_KEYS:
            out[k] = ("SET(" + mask_secret(v) + ")") if v else "MISSING"
        else:
            out[k] = v if v else "(default/empty)"
    return out
