"""Cau hinh dung chung (tap trung, co kieu).

P2.2 — Pydantic Settings (CONG DON, AN TOAN):
- Truoc day cau hinh doc rai rac qua ~91 loi goi `os.getenv` trong 24 file,
  nhieu cho co default am tham -> kho biet co bao nhieu bien, kieu gi, default gi.
- Buoc nay THEM mot nguon su that co kieu: lop `Settings` (Pydantic v2) gom
  TAT CA bien moi truong dang dung, kem default + kieu, va singleton `settings`.
- **Khong xoa** cac `os.getenv` cu o cac module khac trong buoc nay (cong don,
  khong doi hanh vi). Cac module co the chuyen dan sang doc tu `settings` sau.
- `Settings.from_env()` tai hien CHINH XAC default + quy tac parse cua tung bien
  (int/float/bool voi dung tap gia tri "truthy" nhu code goc). Parser khoan dung
  (loi parse -> ve default) de KHONG tao them che do loi moi khi khoi dong.

QDRANT_COLLECTION: ten collection vector store dung CHUNG cho moi phong ban.
- Day chi la dinh danh NOI BO (nguoi dung khong nhin thay tren UI).
- Khong gan voi 'co khi' — moi phong ban deu luu chung o day, phan biet bang
  payload (domain / security_level / phong_ban) chu khong phai bang ten collection.
- Mac dinh giu ten cu 'TaiLieuKyThuat_v2' de KHONG lam mat du lieu da nap.
- Muon doi sang ten trung tinh (vd 'KnowledgeBase_v2'): dat bien moi truong
  QDRANT_COLLECTION trong .env, roi chay scripts/migrate_qdrant_collection.py
  de di tru toan bo vector sang ten moi (Qdrant khong ho tro rename truc tiep).
"""
from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


# ---------------------------------------------------------------------------
# Helper parse — tai hien dung quy tac cua code goc
# ---------------------------------------------------------------------------

def _str(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool, truthy) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in truthy


def _first(*names: str, default: Optional[str] = None) -> Optional[str]:
    """Tra ve gia tri dau tien khac None/rong trong chuoi ten (chained fallback)."""
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return default


# Cac tap "truthy" khac nhau ton tai trong code goc — giu nguyen tung cai
_TRUTHY_3 = frozenset({"1", "true", "yes"})             # SQL_TRUSTED_CONNECTION
_TRUTHY_4 = frozenset({"1", "true", "yes", "on"})       # VISION_CACHE_ENABLED
_TRUTHY_5 = frozenset({"1", "true", "yes", "y", "on"})  # env_bool / rerank / cache


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    """Nguon su that co kieu cho toan bo cau hinh runtime.

    Dung `Settings.from_env()` (hoac singleton `settings`) de nap tu moi truong.
    Cac ghi chu ben canh chi ra file/hanh vi goc de doi chieu khi migrate.
    """

    model_config = {"frozen": True}

    # --- SQL Server (db/engine.py) ----------------------------------------
    SQL_SERVER: str = r"localhost\SQLEXPRESS"
    SQL_DATABASE: str = "Mech_Chatbot_DB"
    SQL_DRIVER: str = "ODBC Driver 17 for SQL Server"
    SQL_USERNAME: Optional[str] = None
    SQL_PASSWORD: Optional[str] = None
    SQL_TRUSTED_CONNECTION: bool = True  # goc: os.getenv(...,"yes") in {1,true,yes}

    # --- Qdrant (config/settings, db/repositories/qdrant, rag/bootstrap) ---
    QDRANT_COLLECTION: str = "TaiLieuKyThuat_v2"
    QDRANT_URL: Optional[str] = None
    QDRANT_API_KEY: Optional[str] = None

    # --- Embedding (ingestion/pdf/config, rag/bootstrap, ui/pages/settings) -
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_DIM: int = 1024
    EMBEDDING_CHUNK_SIZE: int = 220
    EMBEDDING_CHUNK_OVERLAP: int = 40

    # --- LLM (llm/llm_client) --------------------------------------------
    # goc: PROXYLLM_API_KEY or OPENAI_API_KEY or GPT_API_KEY
    LLM_API_KEY: Optional[str] = None
    # goc: PROXYLLM_BASE_URL or OPENAI_BASE_URL
    LLM_BASE_URL: Optional[str] = None
    GPT_MODEL_NAME: str = "gpt-5.4"
    GPT_TEMPERATURE: float = 0.0
    GPT_MAX_OUTPUT_TOKENS: int = 4000
    GPT_TIMEOUT_SECONDS: float = 120.0  # llm_client=120; vision_client fallback=180
    GPT_MIN_INTERVAL_SECONDS: float = 0.0

    # --- Vision (llm/vision_client, ingestion/pdf/vision) -----------------
    GPT_VISION_MODEL_NAME: str = "gpt-5.4"  # goc: GPT_VISION_MODEL_NAME or GPT_MODEL_NAME or gpt-5.4
    GPT_VISION_IMAGE_FORMAT: str = "jpeg"
    GPT_VISION_MAX_EDGE: int = 0
    GPT_VISION_JPEG_QUALITY: int = 85
    GPT_VISION_TEMPERATURE: float = 0.0
    GPT_VISION_MAX_OUTPUT_TOKENS: int = 4096
    INGEST_VISION_PREWARM_WORKERS: int = 1

    # --- RAG core (rag/*) -------------------------------------------------
    RERANK_PER_PART: int = 10
    RERANK_TOP_N_CAP: int = 40
    VOYAGE_API_KEY: Optional[str] = None
    VOYAGE_RERANK_MODEL: str = "rerank-2.5-lite"
    VOYAGE_RERANK_TIMEOUT_SECONDS: float = 15.0
    USE_VOYAGE_RERANK: bool = True
    STRICT_ANSWER_MODE: bool = True
    STRICT_REALTIME_STREAMING: bool = False
    INTENT_MAX_WORKERS: int = 8
    INTENT_TIMEOUT: float = 6.0
    CONTEXT_TIMEOUT: float = 5.0
    HISTORY_BUDGET: int = 4000
    SEMANTIC_CACHE_ENABLED: bool = True
    SEMANTIC_CACHE_SIM_THRESHOLD: float = 0.93
    SEMANTIC_CACHE_TTL_HOURS: float = 24.0
    RAG_GROUNDED_MATH_ENABLED: bool = False
    RAG_LATE_INTERACTION_ENABLED: bool = False
    RAG_LATE_ENCODER_READY: bool = False
    RAG_QUERY_DECOMPOSITION_ENABLED: bool = False
    RAG_GRAPH_RETRIEVAL_ENABLED: bool = False
    RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED: bool = False
    RAG_PLANNER_VERSION: str = "planner-v1"
    RAG_LATE_INDEX_VERSION: str = "late-v2"
    RAG_GRAPH_SERVING_EPOCH: str = "graph-v1"
    RAG_COMMUNITY_SERVING_EPOCH: str = "community-v1"
    GLOSSARY_CACHE_TTL: float = 60.0
    LLM_ROUTER_MIN_CONFIDENCE: float = 0.5

    # --- Ingestion (ingestion/*) -----------------------------------------
    LLM_METADATA_MODE: str = "missing_only"
    STRICT_INGEST_REQUIRE_VISION: bool = False
    ROLLBACK_ON_INGEST_ERROR: bool = True
    PDF_RENDER_DPI: int = 300
    METADATA_TEXT_LIMIT: int = 20000
    VISION_CACHE_ENABLED: bool = True
    VISION_CACHE_DIR: Optional[str] = None  # default that thu tinh o vision_cache.py

    # --- Server / Workers / App (api, workers, ui) ------------------------
    MAX_CONCURRENT_RAG: int = 2
    RAG_SERVER_PORT: int = 8100
    RAG_SERVER_HOST: str = "0.0.0.0"
    RAG_SERVER_URL: str = ""
    RAG_WORKER_TIMEOUT: int = 240
    MAX_USER_MSG_LEN: int = 20000
    MAX_BOT_MSG_LEN: int = 200000
    CATALOG_CACHE_TTL: float = 60.0

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "Settings":
        """Nap tu os.environ, tai hien dung default + quy tac parse cua code goc."""
        return cls(
            # SQL
            SQL_SERVER=_str("SQL_SERVER", r"localhost\SQLEXPRESS"),
            SQL_DATABASE=_str("SQL_DATABASE", "Mech_Chatbot_DB"),
            SQL_DRIVER=_str("SQL_DRIVER", "ODBC Driver 17 for SQL Server"),
            SQL_USERNAME=_str("SQL_USERNAME"),
            SQL_PASSWORD=_str("SQL_PASSWORD"),
            SQL_TRUSTED_CONNECTION=_bool("SQL_TRUSTED_CONNECTION", True, _TRUTHY_3),
            # Qdrant
            QDRANT_COLLECTION=_str("QDRANT_COLLECTION", "TaiLieuKyThuat_v2"),
            QDRANT_URL=_str("QDRANT_URL"),
            QDRANT_API_KEY=_str("QDRANT_API_KEY"),
            # Embedding
            EMBEDDING_MODEL=_str("EMBEDDING_MODEL", "BAAI/bge-m3"),
            EMBEDDING_DEVICE=_str("EMBEDDING_DEVICE", "cpu"),
            EMBEDDING_DIM=_int("EMBEDDING_DIM", 1024),
            EMBEDDING_CHUNK_SIZE=_int("EMBEDDING_CHUNK_SIZE", 220),
            EMBEDDING_CHUNK_OVERLAP=_int("EMBEDDING_CHUNK_OVERLAP", 40),
            # LLM
            LLM_API_KEY=_first("PROXYLLM_API_KEY", "OPENAI_API_KEY", "GPT_API_KEY"),
            LLM_BASE_URL=_first("PROXYLLM_BASE_URL", "OPENAI_BASE_URL"),
            GPT_MODEL_NAME=_str("GPT_MODEL_NAME", "gpt-5.4"),
            GPT_TEMPERATURE=_float("GPT_TEMPERATURE", 0.0),
            GPT_MAX_OUTPUT_TOKENS=_int("GPT_MAX_OUTPUT_TOKENS", 4000),
            GPT_TIMEOUT_SECONDS=_float("GPT_TIMEOUT_SECONDS", 120.0),
            GPT_MIN_INTERVAL_SECONDS=_float("GPT_MIN_INTERVAL_SECONDS", 0.0),
            # Vision
            GPT_VISION_MODEL_NAME=_first(
                "GPT_VISION_MODEL_NAME", "GPT_MODEL_NAME", default="gpt-5.4"
            ),
            GPT_VISION_IMAGE_FORMAT=_str("GPT_VISION_IMAGE_FORMAT", "jpeg"),
            GPT_VISION_MAX_EDGE=_int("GPT_VISION_MAX_EDGE", 0),
            GPT_VISION_JPEG_QUALITY=_int("GPT_VISION_JPEG_QUALITY", 85),
            GPT_VISION_TEMPERATURE=_float("GPT_VISION_TEMPERATURE", 0.0),
            GPT_VISION_MAX_OUTPUT_TOKENS=_int("GPT_VISION_MAX_OUTPUT_TOKENS", 4096),
            INGEST_VISION_PREWARM_WORKERS=_int("INGEST_VISION_PREWARM_WORKERS", 1),
            # RAG core
            RERANK_PER_PART=_int("RERANK_PER_PART", 10),
            RERANK_TOP_N_CAP=_int("RERANK_TOP_N_CAP", 40),
            VOYAGE_API_KEY=_str("VOYAGE_API_KEY"),
            VOYAGE_RERANK_MODEL=_str("VOYAGE_RERANK_MODEL", "rerank-2.5-lite"),
            VOYAGE_RERANK_TIMEOUT_SECONDS=_float("VOYAGE_RERANK_TIMEOUT_SECONDS", 15.0),
            USE_VOYAGE_RERANK=_bool("USE_VOYAGE_RERANK", True, _TRUTHY_5),
            STRICT_ANSWER_MODE=_bool("STRICT_ANSWER_MODE", True, _TRUTHY_5),
            STRICT_REALTIME_STREAMING=_bool("STRICT_REALTIME_STREAMING", False, _TRUTHY_5),
            INTENT_MAX_WORKERS=_int("INTENT_MAX_WORKERS", 8),
            INTENT_TIMEOUT=_float("INTENT_TIMEOUT", 6.0),
            CONTEXT_TIMEOUT=_float("CONTEXT_TIMEOUT", 5.0),
            HISTORY_BUDGET=_int("HISTORY_BUDGET", 4000),
            SEMANTIC_CACHE_ENABLED=_bool("SEMANTIC_CACHE_ENABLED", True, _TRUTHY_5),
            SEMANTIC_CACHE_SIM_THRESHOLD=_float("SEMANTIC_CACHE_SIM_THRESHOLD", 0.93),
            SEMANTIC_CACHE_TTL_HOURS=_float("SEMANTIC_CACHE_TTL_HOURS", 24.0),
            RAG_GROUNDED_MATH_ENABLED=_bool("RAG_GROUNDED_MATH_ENABLED", False, _TRUTHY_5),
            RAG_LATE_INTERACTION_ENABLED=_bool("RAG_LATE_INTERACTION_ENABLED", False, _TRUTHY_5),
            RAG_LATE_ENCODER_READY=_bool("RAG_LATE_ENCODER_READY", False, _TRUTHY_5),
            RAG_QUERY_DECOMPOSITION_ENABLED=_bool("RAG_QUERY_DECOMPOSITION_ENABLED", False, _TRUTHY_5),
            RAG_GRAPH_RETRIEVAL_ENABLED=_bool("RAG_GRAPH_RETRIEVAL_ENABLED", False, _TRUTHY_5),
            RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED=_bool(
                "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED", False, _TRUTHY_5
            ),
            RAG_PLANNER_VERSION=_str("RAG_PLANNER_VERSION", "planner-v1"),
            RAG_LATE_INDEX_VERSION=_str("RAG_LATE_INDEX_VERSION", "late-v2"),
            RAG_GRAPH_SERVING_EPOCH=_str("RAG_GRAPH_SERVING_EPOCH", "graph-v1"),
            RAG_COMMUNITY_SERVING_EPOCH=_str(
                "RAG_COMMUNITY_SERVING_EPOCH", "community-v1"
            ),
            GLOSSARY_CACHE_TTL=_float("GLOSSARY_CACHE_TTL", 60.0),
            LLM_ROUTER_MIN_CONFIDENCE=_float("LLM_ROUTER_MIN_CONFIDENCE", 0.5),
            # Ingestion
            LLM_METADATA_MODE=_str("LLM_METADATA_MODE", "missing_only").strip().lower(),
            STRICT_INGEST_REQUIRE_VISION=_bool("STRICT_INGEST_REQUIRE_VISION", False, _TRUTHY_5),
            ROLLBACK_ON_INGEST_ERROR=_bool("ROLLBACK_ON_INGEST_ERROR", True, _TRUTHY_5),
            PDF_RENDER_DPI=_int("PDF_RENDER_DPI", 300),
            METADATA_TEXT_LIMIT=_int("METADATA_TEXT_LIMIT", 20000),
            VISION_CACHE_ENABLED=_bool("VISION_CACHE_ENABLED", True, _TRUTHY_4),
            VISION_CACHE_DIR=_str("VISION_CACHE_DIR"),
            # Server / Workers / App
            MAX_CONCURRENT_RAG=_int("MAX_CONCURRENT_RAG", 2),
            RAG_SERVER_PORT=_int("RAG_SERVER_PORT", 8100),
            RAG_SERVER_HOST=_str("RAG_SERVER_HOST", "0.0.0.0"),
            RAG_SERVER_URL=_str("RAG_SERVER_URL", ""),
            RAG_WORKER_TIMEOUT=_int("RAG_WORKER_TIMEOUT", 240),
            MAX_USER_MSG_LEN=_int("MAX_USER_MSG_LEN", 20000),
            MAX_BOT_MSG_LEN=_int("MAX_BOT_MSG_LEN", 200000),
            CATALOG_CACHE_TTL=_float("CATALOG_CACHE_TTL", 60.0),
        )


# Singleton dung chung — nap 1 lan luc import.
settings = Settings.from_env()


# ---------------------------------------------------------------------------
# Backward-compat: giu nguyen hang so cu de KHONG vo import hien co
#   `from ...config.settings import QDRANT_COLLECTION`
# ---------------------------------------------------------------------------
QDRANT_COLLECTION = settings.QDRANT_COLLECTION
