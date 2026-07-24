"""Cau hinh dung chung (tap trung, co kieu).

Phase 5 — immutable process configuration:
- `Settings.from_env(mapping)` parses one explicit environment snapshot.
- `load_settings(path)` reads a dotenv file without mutating `os.environ`.
- Composition roots project the snapshot into narrow frozen settings bundles.
- Importing this module does not read dotenv files or capture process settings.

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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel

# Cac tap "truthy" khac nhau ton tai trong code goc — giu nguyen tung cai
_TRUTHY_3 = frozenset({"1", "true", "yes"})             # SQL_TRUSTED_CONNECTION
_TRUTHY_4 = frozenset({"1", "true", "yes", "on"})       # VISION_CACHE_ENABLED
_TRUTHY_5 = frozenset({"1", "true", "yes", "y", "on"})  # env_bool / rerank / cache


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------

class Settings(BaseModel):
    """Nguon su that co kieu cho toan bo cau hinh runtime.

    Dung `Settings.from_env()` de nap tu mot mapping moi truong da chup lai.
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
    APP_ENV: str = ""
    EXTERNAL_AI_LOCAL_DEVELOPMENT: bool = False
    EXTERNAL_PROCESSING_POLICY: str = "all_external"

    # --- Vision (llm/vision_client, ingestion/pdf/vision) -----------------
    GPT_VISION_MODEL_NAME: str = "gpt-5.4"  # goc: GPT_VISION_MODEL_NAME or GPT_MODEL_NAME or gpt-5.4
    GPT_VISION_IMAGE_FORMAT: str = "jpeg"
    GPT_VISION_MAX_EDGE: int = 0
    GPT_VISION_JPEG_QUALITY: int = 85
    GPT_VISION_TEMPERATURE: float = 0.0
    GPT_VISION_MAX_OUTPUT_TOKENS: int = 4096
    INGEST_VISION_PREWARM_WORKERS: int = 1

    # --- RAG core (rag/*) -------------------------------------------------
    RERANK_PER_PART: int = 8
    RERANK_TOP_N_CAP: int = 20
    VOYAGE_API_KEY: Optional[str] = None
    VOYAGE_RERANK_MODEL: str = "rerank-2.5-lite"
    VOYAGE_RERANK_TIMEOUT_SECONDS: float = 15.0
    USE_VOYAGE_RERANK: bool = True
    STRICT_ANSWER_MODE: bool = True
    STRICT_REALTIME_STREAMING: bool = False
    HYDE_ENABLED: bool = True
    ENABLE_QUERY_REWRITE: bool = True
    INTENT_MAX_WORKERS: int = 8
    PARENT_CONTEXT_MAX_WORKERS: int = 4
    INTENT_TIMEOUT: float = 6.0
    CONTEXT_TIMEOUT: float = 5.0
    HISTORY_BUDGET: int = 4000
    SEMANTIC_CACHE_ENABLED: bool = True
    SEMANTIC_CACHE_SIM_THRESHOLD: float = 0.93
    SEMANTIC_CACHE_TTL_HOURS: float = 24.0
    RAG_CRAG_ENABLED: bool = False
    RAG_CLAIM_REPAIR_ENABLED: bool = False
    RAG_GROUNDED_MATH_ENABLED: bool = False
    RAG_LATE_INTERACTION_ENABLED: bool = False
    RAG_LATE_ENCODER_READY: bool = False
    RAG_LATE_MODEL: str = "BAAI/bge-m3"
    RAG_LATE_QUERY_MAX_LENGTH: int = 64
    RAG_LATE_DOCUMENT_MAX_LENGTH: int = 48
    RAG_LATE_COLLECTION: str = "MechChatbot_LateInteraction_v1"
    RAG_QUERY_DECOMPOSITION_ENABLED: bool = False
    RAG_GRAPH_RETRIEVAL_ENABLED: bool = False
    RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED: bool = False
    RAG_ACTIVATION_SCOPE: str = "default_rollout"
    RAG_ACTIVATION_BUNDLE_PATH: Optional[str] = None
    RAG_ACTIVATION_BUNDLE_SHA256: Optional[str] = None
    RAG_PLANNER_VERSION: str = "planner-v1"
    RAG_LATE_INDEX_VERSION: str = "late-v2"
    RERANK_MAX_CHUNKS_PER_DOCUMENT: int = 4
    RERANK_MAX_CHUNKS_PER_SECTION: int = 1
    RERANK_CANDIDATE_CAP: int = 20
    RAG_AUTO_SOURCE_CARDS: bool = True
    GPT_STREAM_MAX_ATTEMPTS: int = 3
    STRICT_STREAMING_HOLDBACK_CHARS: int = 160
    RAG_EVAL_DRAFT_OVERRIDE: Optional[str] = None
    PARENT_CONTEXT_ENABLED: bool = True
    PARENT_CONTEXT_MAX_SECTIONS: int = 8
    PARENT_CONTEXT_MAX_CHUNKS: int = 6
    CITATION_MAX_SOURCES: int = 5
    BOM_CITATION_MAX_SOURCES: int = 3
    RAG_EVAL_FORCE_AMBIGUOUS: bool = False
    RAG_GRAPH_SERVING_EPOCH: str = "graph-v1"
    RAG_COMMUNITY_SERVING_EPOCH: str = "community-v1"
    RAG_GRAPH_FINGERPRINT: Optional[str] = None
    RAG_DEPLOYMENT_ID: Optional[str] = None
    RAG_DEPLOYMENT_GIT_SHA: Optional[str] = None
    RAG_SNAPSHOT_FINGERPRINT: Optional[str] = None
    CRAG_PILOT_ASSIGNMENT_SALT: str = ""
    GLOSSARY_CACHE_TTL: float = 60.0
    LLM_ROUTER_MIN_CONFIDENCE: float = 0.5
    RAG_EXECUTION_CONTEXT: str = "production"
    RAG_REQUEST_DEADLINE_SECONDS: float = 120.0
    EVALUATION_FORCE_AMBIGUOUS: bool = False
    RBAC_STRICT_SITE_FILTER: bool = True
    KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE: bool = False
    KNOWLEDGE_ALLOW_ADMIN_APPROVAL_OVERRIDE: bool = False
    PUBLICATION_MAX_ATTEMPTS: int = 5
    RAG_TRACE_LOG_FILE: Optional[str] = None

    # --- Ingestion (ingestion/*) -----------------------------------------
    LLM_METADATA_MODE: str = "missing_only"
    STRICT_INGEST_REQUIRE_VISION: bool = False
    ROLLBACK_ON_INGEST_ERROR: bool = True
    PDF_RENDER_DPI: int = 300
    METADATA_TEXT_LIMIT: int = 20000
    VISION_CACHE_ENABLED: bool = True
    VISION_CACHE_DIR: Optional[str] = None  # default that thu tinh o vision_cache.py
    ENABLE_CONTEXTUAL_CHUNK: bool = False

    # --- Server / Workers / App (api, workers, ui) ------------------------
    MAX_CONCURRENT_RAG: int = 2
    RAG_SERVER_PORT: int = 8100
    RAG_SERVER_HOST: str = "0.0.0.0"
    RAG_SERVER_URL: str = ""
    RAG_WORKER_TIMEOUT: int = 240
    MAX_USER_MSG_LEN: int = 20000
    MAX_BOT_MSG_LEN: int = 200000
    CATALOG_CACHE_TTL: float = 60.0
    RAG_REQUIRE_SERVICE_AUTH: bool = True
    RAG_SERVICE_TOKEN: str = ""
    RAG_CORS_ALLOW_ORIGINS: tuple[str, ...] = ()

    APP_THREAD_LIMIT: int = 60
    APP_RAG_CHAT_TIMEOUT_SECONDS: int = 300
    APP_SERVER_HOST: str = "0.0.0.0"
    APP_SERVER_PORT: int = 8080
    APP_SESSION_SECRET: str = ""
    APP_COOKIE_SECURE: bool = False
    APP_COOKIE_SAMESITE: str = "lax"
    APP_SESSION_TTL_SECONDS: int = 2700
    CRAG_PILOT_REPLAY_WORKERS: int = 2
    CRAG_PILOT_REPLAY_QUEUE_SIZE: int = 8
    CRAG_PILOT_REPLAY_TIMEOUT_SECONDS: float = 300.0
    LIFECYCLE_RECONCILE_SECONDS: int = 300

    PUBLICATION_RECONCILE_INTERVAL_SECONDS: int = 15
    SERVING_RECONCILE_INTERVAL_SECONDS: int = 600
    SERVING_RECONCILE_BATCH_SIZE: int = 500
    WORKER_IDLE_SLEEP_SECONDS: int = 5
    WORKER_ERROR_SLEEP_SECONDS: int = 10

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """Parse one environment snapshot with the legacy coercion rules."""

        source = dict(os.environ if environ is None else environ)

        def _str(name: str, default: Optional[str] = None) -> Optional[str]:
            value = source.get(name)
            return value if value is not None else default

        def _int(name: str, default: int) -> int:
            raw = source.get(name)
            if raw is None or str(raw).strip() == "":
                return default
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            raw = source.get(name)
            if raw is None or str(raw).strip() == "":
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        def _bool(name: str, default: bool, truthy) -> bool:
            raw = source.get(name)
            if raw is None:
                return default
            return str(raw).strip().lower() in truthy

        def _first(*names: str, default: Optional[str] = None) -> Optional[str]:
            for name in names:
                value = source.get(name)
                if value:
                    return value
            return default

        def _csv(name: str) -> tuple[str, ...]:
            return tuple(
                item.strip()
                for item in str(source.get(name) or "").split(",")
                if item.strip()
            )

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
            APP_ENV=_str("APP_ENV", ""),
            EXTERNAL_AI_LOCAL_DEVELOPMENT=_bool(
                "EXTERNAL_AI_LOCAL_DEVELOPMENT",
                False,
                _TRUTHY_5,
            ),
            EXTERNAL_PROCESSING_POLICY=_str(
                "EXTERNAL_PROCESSING_POLICY",
                "all_external",
            ),
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
            RERANK_PER_PART=_int("RERANK_PER_PART", 8),
            RERANK_TOP_N_CAP=_int("RERANK_TOP_N_CAP", 20),
            VOYAGE_API_KEY=_str("VOYAGE_API_KEY"),
            VOYAGE_RERANK_MODEL=_str("VOYAGE_RERANK_MODEL", "rerank-2.5-lite"),
            VOYAGE_RERANK_TIMEOUT_SECONDS=_float("VOYAGE_RERANK_TIMEOUT_SECONDS", 15.0),
            USE_VOYAGE_RERANK=_bool("USE_VOYAGE_RERANK", True, _TRUTHY_5),
            STRICT_ANSWER_MODE=_bool("STRICT_ANSWER_MODE", True, _TRUTHY_5),
            STRICT_REALTIME_STREAMING=_bool("STRICT_REALTIME_STREAMING", False, _TRUTHY_5),
            HYDE_ENABLED=_bool("HYDE_ENABLED", True, _TRUTHY_5),
            ENABLE_QUERY_REWRITE=_bool("ENABLE_QUERY_REWRITE", True, _TRUTHY_5),
            INTENT_MAX_WORKERS=_int("INTENT_MAX_WORKERS", 8),
            PARENT_CONTEXT_MAX_WORKERS=_int("PARENT_CONTEXT_MAX_WORKERS", 4),
            INTENT_TIMEOUT=_float("INTENT_TIMEOUT", 6.0),
            CONTEXT_TIMEOUT=_float("CONTEXT_TIMEOUT", 5.0),
            HISTORY_BUDGET=_int("HISTORY_BUDGET", 4000),
            SEMANTIC_CACHE_ENABLED=_bool("SEMANTIC_CACHE_ENABLED", True, _TRUTHY_5),
            SEMANTIC_CACHE_SIM_THRESHOLD=_float("SEMANTIC_CACHE_SIM_THRESHOLD", 0.93),
            SEMANTIC_CACHE_TTL_HOURS=_float("SEMANTIC_CACHE_TTL_HOURS", 24.0),
            RAG_CRAG_ENABLED=_bool("RAG_CRAG_ENABLED", False, _TRUTHY_5),
            RAG_CLAIM_REPAIR_ENABLED=_bool(
                "RAG_CLAIM_REPAIR_ENABLED", False, _TRUTHY_5
            ),
            RAG_GROUNDED_MATH_ENABLED=_bool("RAG_GROUNDED_MATH_ENABLED", False, _TRUTHY_5),
            RAG_LATE_INTERACTION_ENABLED=_bool("RAG_LATE_INTERACTION_ENABLED", False, _TRUTHY_5),
            RAG_LATE_ENCODER_READY=_bool("RAG_LATE_ENCODER_READY", False, _TRUTHY_5),
            RAG_LATE_MODEL=_str("RAG_LATE_MODEL", "BAAI/bge-m3"),
            RAG_LATE_QUERY_MAX_LENGTH=_int("RAG_LATE_QUERY_MAX_LENGTH", 64),
            RAG_LATE_DOCUMENT_MAX_LENGTH=_int(
                "RAG_LATE_DOCUMENT_MAX_LENGTH",
                48,
            ),
            RAG_LATE_COLLECTION=_str(
                "RAG_LATE_COLLECTION",
                "MechChatbot_LateInteraction_v1",
            ),
            RAG_QUERY_DECOMPOSITION_ENABLED=_bool("RAG_QUERY_DECOMPOSITION_ENABLED", False, _TRUTHY_5),
            RAG_GRAPH_RETRIEVAL_ENABLED=_bool("RAG_GRAPH_RETRIEVAL_ENABLED", False, _TRUTHY_5),
            RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED=_bool(
                "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED", False, _TRUTHY_5
            ),
            RAG_ACTIVATION_SCOPE=_str("RAG_ACTIVATION_SCOPE", "default_rollout"),
            RAG_ACTIVATION_BUNDLE_PATH=_str("RAG_ACTIVATION_BUNDLE_PATH"),
            RAG_ACTIVATION_BUNDLE_SHA256=_str("RAG_ACTIVATION_BUNDLE_SHA256"),
            RAG_PLANNER_VERSION=_str("RAG_PLANNER_VERSION", "planner-v1"),
            RAG_LATE_INDEX_VERSION=_str("RAG_LATE_INDEX_VERSION", "late-v2"),
            RERANK_MAX_CHUNKS_PER_DOCUMENT=_int(
                "RERANK_MAX_CHUNKS_PER_DOCUMENT",
                4,
            ),
            RERANK_MAX_CHUNKS_PER_SECTION=_int(
                "RERANK_MAX_CHUNKS_PER_SECTION",
                1,
            ),
            RERANK_CANDIDATE_CAP=_int("RERANK_CANDIDATE_CAP", 20),
            RAG_AUTO_SOURCE_CARDS=_bool(
                "RAG_AUTO_SOURCE_CARDS",
                True,
                _TRUTHY_5,
            ),
            GPT_STREAM_MAX_ATTEMPTS=_int("GPT_STREAM_MAX_ATTEMPTS", 3),
            STRICT_STREAMING_HOLDBACK_CHARS=_int(
                "STRICT_STREAMING_HOLDBACK_CHARS",
                160,
            ),
            RAG_EVAL_DRAFT_OVERRIDE=_str("RAG_EVAL_DRAFT_OVERRIDE"),
            PARENT_CONTEXT_ENABLED=_bool(
                "PARENT_CONTEXT_ENABLED",
                True,
                _TRUTHY_5,
            ),
            PARENT_CONTEXT_MAX_SECTIONS=_int(
                "PARENT_CONTEXT_MAX_SECTIONS",
                8,
            ),
            PARENT_CONTEXT_MAX_CHUNKS=_int("PARENT_CONTEXT_MAX_CHUNKS", 6),
            CITATION_MAX_SOURCES=_int("CITATION_MAX_SOURCES", 5),
            BOM_CITATION_MAX_SOURCES=_int("BOM_CITATION_MAX_SOURCES", 3),
            RAG_EVAL_FORCE_AMBIGUOUS=_bool(
                "RAG_EVAL_FORCE_AMBIGUOUS",
                False,
                _TRUTHY_5,
            ),
            RAG_GRAPH_SERVING_EPOCH=_str("RAG_GRAPH_SERVING_EPOCH", "graph-v1"),
            RAG_COMMUNITY_SERVING_EPOCH=_str(
                "RAG_COMMUNITY_SERVING_EPOCH", "community-v1"
            ),
            RAG_GRAPH_FINGERPRINT=_str("RAG_GRAPH_FINGERPRINT"),
            RAG_DEPLOYMENT_ID=_str("RAG_DEPLOYMENT_ID"),
            RAG_DEPLOYMENT_GIT_SHA=_str("RAG_DEPLOYMENT_GIT_SHA"),
            RAG_SNAPSHOT_FINGERPRINT=_str("RAG_SNAPSHOT_FINGERPRINT"),
            CRAG_PILOT_ASSIGNMENT_SALT=_str(
                "CRAG_PILOT_ASSIGNMENT_SALT",
                "",
            ),
            GLOSSARY_CACHE_TTL=_float("GLOSSARY_CACHE_TTL", 60.0),
            LLM_ROUTER_MIN_CONFIDENCE=_float("LLM_ROUTER_MIN_CONFIDENCE", 0.5),
            RAG_EXECUTION_CONTEXT=_str("RAG_EXECUTION_CONTEXT", "production"),
            RAG_REQUEST_DEADLINE_SECONDS=_float(
                "RAG_REQUEST_DEADLINE_SECONDS",
                120.0,
            ),
            EVALUATION_FORCE_AMBIGUOUS=_bool(
                "EVALUATION_FORCE_AMBIGUOUS",
                False,
                _TRUTHY_5,
            ),
            RBAC_STRICT_SITE_FILTER=_bool(
                "RBAC_STRICT_SITE_FILTER",
                True,
                _TRUTHY_5,
            ),
            KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE=_bool(
                "KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE",
                False,
                _TRUTHY_5,
            ),
            KNOWLEDGE_ALLOW_ADMIN_APPROVAL_OVERRIDE=_bool(
                "KNOWLEDGE_ALLOW_ADMIN_APPROVAL_OVERRIDE",
                False,
                _TRUTHY_5,
            ),
            PUBLICATION_MAX_ATTEMPTS=_int("PUBLICATION_MAX_ATTEMPTS", 5),
            RAG_TRACE_LOG_FILE=_str("RAG_TRACE_LOG_FILE"),
            # Ingestion
            LLM_METADATA_MODE=_str("LLM_METADATA_MODE", "missing_only").strip().lower(),
            STRICT_INGEST_REQUIRE_VISION=_bool("STRICT_INGEST_REQUIRE_VISION", False, _TRUTHY_5),
            ROLLBACK_ON_INGEST_ERROR=_bool("ROLLBACK_ON_INGEST_ERROR", True, _TRUTHY_5),
            PDF_RENDER_DPI=_int("PDF_RENDER_DPI", 300),
            METADATA_TEXT_LIMIT=_int("METADATA_TEXT_LIMIT", 20000),
            VISION_CACHE_ENABLED=_bool("VISION_CACHE_ENABLED", True, _TRUTHY_4),
            VISION_CACHE_DIR=_str("VISION_CACHE_DIR"),
            ENABLE_CONTEXTUAL_CHUNK=_bool(
                "ENABLE_CONTEXTUAL_CHUNK",
                False,
                _TRUTHY_5,
            ),
            # Server / Workers / App
            MAX_CONCURRENT_RAG=_int("MAX_CONCURRENT_RAG", 2),
            RAG_SERVER_PORT=_int("RAG_SERVER_PORT", 8100),
            RAG_SERVER_HOST=_str("RAG_SERVER_HOST", "0.0.0.0"),
            RAG_SERVER_URL=_str("RAG_SERVER_URL", ""),
            RAG_WORKER_TIMEOUT=_int("RAG_WORKER_TIMEOUT", 240),
            MAX_USER_MSG_LEN=_int("MAX_USER_MSG_LEN", 20000),
            MAX_BOT_MSG_LEN=_int("MAX_BOT_MSG_LEN", 200000),
            CATALOG_CACHE_TTL=_float("CATALOG_CACHE_TTL", 60.0),
            RAG_REQUIRE_SERVICE_AUTH=_bool(
                "RAG_REQUIRE_SERVICE_AUTH",
                True,
                _TRUTHY_5,
            ),
            RAG_SERVICE_TOKEN=_str("RAG_SERVICE_TOKEN", ""),
            RAG_CORS_ALLOW_ORIGINS=_csv("RAG_CORS_ALLOW_ORIGINS"),
            APP_THREAD_LIMIT=_int("APP_THREAD_LIMIT", 60),
            APP_RAG_CHAT_TIMEOUT_SECONDS=_int(
                "APP_RAG_CHAT_TIMEOUT_SECONDS",
                300,
            ),
            APP_SERVER_HOST=_str("APP_SERVER_HOST", "0.0.0.0"),
            APP_SERVER_PORT=_int("APP_SERVER_PORT", 8080),
            APP_SESSION_SECRET=_first(
                "APP_SESSION_SECRET",
                "CHAT_BRIDGE_SECRET",
                "RAG_SERVICE_TOKEN",
                default="",
            ),
            APP_COOKIE_SECURE=_bool(
                "APP_COOKIE_SECURE",
                False,
                _TRUTHY_4,
            ),
            APP_COOKIE_SAMESITE=_str("APP_COOKIE_SAMESITE", "lax"),
            APP_SESSION_TTL_SECONDS=_int("APP_SESSION_TTL_SECONDS", 2700),
            CRAG_PILOT_REPLAY_WORKERS=_int("CRAG_PILOT_REPLAY_WORKERS", 2),
            CRAG_PILOT_REPLAY_QUEUE_SIZE=_int(
                "CRAG_PILOT_REPLAY_QUEUE_SIZE",
                8,
            ),
            CRAG_PILOT_REPLAY_TIMEOUT_SECONDS=_float(
                "CRAG_PILOT_REPLAY_TIMEOUT_SECONDS",
                300.0,
            ),
            LIFECYCLE_RECONCILE_SECONDS=_int(
                "LIFECYCLE_RECONCILE_SECONDS",
                300,
            ),
            PUBLICATION_RECONCILE_INTERVAL_SECONDS=_int(
                "PUBLICATION_RECONCILE_INTERVAL_SECONDS",
                15,
            ),
            SERVING_RECONCILE_INTERVAL_SECONDS=_int(
                "SERVING_RECONCILE_INTERVAL_SECONDS",
                600,
            ),
            SERVING_RECONCILE_BATCH_SIZE=_int(
                "SERVING_RECONCILE_BATCH_SIZE",
                500,
            ),
            WORKER_IDLE_SLEEP_SECONDS=_int("WORKER_IDLE_SLEEP_SECONDS", 5),
            WORKER_ERROR_SLEEP_SECONDS=_int("WORKER_ERROR_SLEEP_SECONDS", 10),
        )


def load_settings(
    dotenv_path: str | os.PathLike[str] | None = None,
) -> Settings:
    """Read dotenv plus process environment into one immutable snapshot.

    Existing process variables take precedence, matching python-dotenv's
    historical ``override=False`` behavior. The function never mutates
    ``os.environ``.
    """

    from dotenv import dotenv_values

    dotenv_mapping = {
        key: value
        for key, value in dotenv_values(dotenv_path).items()
        if value is not None
    }
    return Settings.from_env({**dotenv_mapping, **os.environ})


@dataclass(frozen=True, slots=True)
class SqlSettings:
    server: str
    database: str
    driver: str
    username: str | None
    password: str | None
    trusted_connection: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> "SqlSettings":
        return cls(
            server=settings.SQL_SERVER,
            database=settings.SQL_DATABASE,
            driver=settings.SQL_DRIVER,
            username=settings.SQL_USERNAME,
            password=settings.SQL_PASSWORD,
            trusted_connection=settings.SQL_TRUSTED_CONNECTION,
        )


@dataclass(frozen=True, slots=True)
class RepositoryPolicySettings:
    strict_site_filter: bool
    allow_admin_metadata_override: bool
    allow_admin_approval_override: bool
    publication_max_attempts: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "RepositoryPolicySettings":
        return cls(
            strict_site_filter=settings.RBAC_STRICT_SITE_FILTER,
            allow_admin_metadata_override=(
                settings.KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE
            ),
            allow_admin_approval_override=(
                settings.KNOWLEDGE_ALLOW_ADMIN_APPROVAL_OVERRIDE
            ),
            publication_max_attempts=max(1, settings.PUBLICATION_MAX_ATTEMPTS),
        )


@dataclass(frozen=True, slots=True)
class QdrantSettings:
    url: str | None
    api_key: str | None
    collection: str
    embedding_model: str
    embedding_device: str
    embedding_dimension: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "QdrantSettings":
        return cls(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
            collection=settings.QDRANT_COLLECTION,
            embedding_model=settings.EMBEDDING_MODEL,
            embedding_device=settings.EMBEDDING_DEVICE,
            embedding_dimension=settings.EMBEDDING_DIM,
        )


@dataclass(frozen=True, slots=True)
class LlmSettings:
    api_key: str | None
    base_url: str | None
    model_name: str
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    min_interval_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "LlmSettings":
        return cls(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model_name=settings.GPT_MODEL_NAME,
            temperature=settings.GPT_TEMPERATURE,
            max_output_tokens=settings.GPT_MAX_OUTPUT_TOKENS,
            timeout_seconds=settings.GPT_TIMEOUT_SECONDS,
            min_interval_seconds=settings.GPT_MIN_INTERVAL_SECONDS,
        )


@dataclass(frozen=True, slots=True)
class ExternalAiSettings:
    application_environment: str
    local_development: bool
    processing_policy: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "ExternalAiSettings":
        return cls(
            application_environment=settings.APP_ENV,
            local_development=settings.EXTERNAL_AI_LOCAL_DEVELOPMENT,
            processing_policy=settings.EXTERNAL_PROCESSING_POLICY,
        )


@dataclass(frozen=True, slots=True)
class VisionSettings:
    api_key: str | None
    base_url: str | None
    model_name: str
    image_format: str
    max_edge: int
    jpeg_quality: int
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    min_interval_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "VisionSettings":
        return cls(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            model_name=settings.GPT_VISION_MODEL_NAME,
            image_format=settings.GPT_VISION_IMAGE_FORMAT,
            max_edge=settings.GPT_VISION_MAX_EDGE,
            jpeg_quality=settings.GPT_VISION_JPEG_QUALITY,
            temperature=settings.GPT_VISION_TEMPERATURE,
            max_output_tokens=settings.GPT_VISION_MAX_OUTPUT_TOKENS,
            timeout_seconds=settings.GPT_TIMEOUT_SECONDS,
            min_interval_seconds=settings.GPT_MIN_INTERVAL_SECONDS,
        )


@dataclass(frozen=True, slots=True)
class AppProcessSettings:
    thread_limit: int
    rag_base_url: str
    rag_service_token: str
    rag_chat_timeout_seconds: int
    server_host: str
    server_port: int
    lifecycle_reconcile_seconds: int
    pilot_replay_workers: int
    pilot_replay_queue_size: int
    pilot_replay_timeout_seconds: float
    session_secret: str
    cookie_secure: bool
    cookie_samesite: str
    session_ttl_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "AppProcessSettings":
        return cls(
            thread_limit=settings.APP_THREAD_LIMIT,
            rag_base_url=(
                settings.RAG_SERVER_URL or "http://127.0.0.1:8100"
            ).rstrip("/"),
            rag_service_token=settings.RAG_SERVICE_TOKEN,
            rag_chat_timeout_seconds=settings.APP_RAG_CHAT_TIMEOUT_SECONDS,
            server_host=settings.APP_SERVER_HOST,
            server_port=settings.APP_SERVER_PORT,
            lifecycle_reconcile_seconds=max(
                60,
                settings.LIFECYCLE_RECONCILE_SECONDS,
            ),
            pilot_replay_workers=settings.CRAG_PILOT_REPLAY_WORKERS,
            pilot_replay_queue_size=settings.CRAG_PILOT_REPLAY_QUEUE_SIZE,
            pilot_replay_timeout_seconds=(
                settings.CRAG_PILOT_REPLAY_TIMEOUT_SECONDS
            ),
            session_secret=settings.APP_SESSION_SECRET,
            cookie_secure=settings.APP_COOKIE_SECURE,
            cookie_samesite=(
                settings.APP_COOKIE_SAMESITE
                if settings.APP_COOKIE_SAMESITE in {"lax", "strict", "none"}
                else "lax"
            ),
            session_ttl_seconds=max(
                60,
                min(86400, settings.APP_SESSION_TTL_SECONDS),
            ),
        )


@dataclass(frozen=True, slots=True)
class RagProcessSettings:
    host: str
    port: int
    max_concurrent_requests: int
    require_service_auth: bool
    service_token: str
    cors_allow_origins: tuple[str, ...]
    deployment_id: str | None
    deployment_git_sha: str | None
    snapshot_fingerprint: str | None
    graph_fingerprint: str | None
    pilot_assignment_salt: str
    hyde_enabled: bool
    query_rewrite_enabled: bool
    late_interaction_enabled: bool
    late_encoder_ready: bool
    execution_context: str
    request_deadline_seconds: float
    evaluation_force_ambiguous: bool
    strict_site_filter: bool
    late_model_name: str
    late_use_fp16: bool
    late_query_max_length: int
    late_document_max_length: int
    late_collection: str
    late_index_version: str
    rerank_max_chunks_per_document: int
    rerank_max_chunks_per_section: int
    rerank_candidate_cap: int
    auto_source_cards: bool
    stream_max_attempts: int
    streaming_holdback_chars: int
    eval_draft_override: str | None
    parent_context_enabled: bool
    parent_context_max_sections: int
    parent_context_max_chunks: int
    citation_max_sources: int
    bom_citation_max_sources: int
    eval_force_ambiguous: bool

    @classmethod
    def from_settings(cls, settings: Settings) -> "RagProcessSettings":
        return cls(
            host=settings.RAG_SERVER_HOST,
            port=settings.RAG_SERVER_PORT,
            max_concurrent_requests=settings.MAX_CONCURRENT_RAG,
            require_service_auth=settings.RAG_REQUIRE_SERVICE_AUTH,
            service_token=settings.RAG_SERVICE_TOKEN,
            cors_allow_origins=settings.RAG_CORS_ALLOW_ORIGINS,
            deployment_id=settings.RAG_DEPLOYMENT_ID,
            deployment_git_sha=settings.RAG_DEPLOYMENT_GIT_SHA,
            snapshot_fingerprint=settings.RAG_SNAPSHOT_FINGERPRINT,
            graph_fingerprint=settings.RAG_GRAPH_FINGERPRINT,
            pilot_assignment_salt=settings.CRAG_PILOT_ASSIGNMENT_SALT,
            hyde_enabled=settings.HYDE_ENABLED,
            query_rewrite_enabled=settings.ENABLE_QUERY_REWRITE,
            late_interaction_enabled=settings.RAG_LATE_INTERACTION_ENABLED,
            late_encoder_ready=settings.RAG_LATE_ENCODER_READY,
            execution_context=settings.RAG_EXECUTION_CONTEXT,
            request_deadline_seconds=settings.RAG_REQUEST_DEADLINE_SECONDS,
            evaluation_force_ambiguous=settings.EVALUATION_FORCE_AMBIGUOUS,
            strict_site_filter=settings.RBAC_STRICT_SITE_FILTER,
            late_model_name=settings.RAG_LATE_MODEL,
            late_use_fp16=settings.EMBEDDING_DEVICE.lower().startswith("cuda"),
            late_query_max_length=settings.RAG_LATE_QUERY_MAX_LENGTH,
            late_document_max_length=settings.RAG_LATE_DOCUMENT_MAX_LENGTH,
            late_collection=settings.RAG_LATE_COLLECTION,
            late_index_version=settings.RAG_LATE_INDEX_VERSION,
            rerank_max_chunks_per_document=(
                settings.RERANK_MAX_CHUNKS_PER_DOCUMENT
            ),
            rerank_max_chunks_per_section=(
                settings.RERANK_MAX_CHUNKS_PER_SECTION
            ),
            rerank_candidate_cap=settings.RERANK_CANDIDATE_CAP,
            auto_source_cards=settings.RAG_AUTO_SOURCE_CARDS,
            stream_max_attempts=max(1, settings.GPT_STREAM_MAX_ATTEMPTS),
            streaming_holdback_chars=max(
                64,
                settings.STRICT_STREAMING_HOLDBACK_CHARS,
            ),
            eval_draft_override=settings.RAG_EVAL_DRAFT_OVERRIDE,
            parent_context_enabled=settings.PARENT_CONTEXT_ENABLED,
            parent_context_max_sections=max(
                1,
                settings.PARENT_CONTEXT_MAX_SECTIONS,
            ),
            parent_context_max_chunks=max(
                1,
                settings.PARENT_CONTEXT_MAX_CHUNKS,
            ),
            citation_max_sources=max(1, settings.CITATION_MAX_SOURCES),
            bom_citation_max_sources=max(
                1,
                settings.BOM_CITATION_MAX_SOURCES,
            ),
            eval_force_ambiguous=settings.RAG_EVAL_FORCE_AMBIGUOUS,
        )


@dataclass(frozen=True, slots=True)
class WorkerProcessSettings:
    publication_reconcile_interval_seconds: int
    serving_reconcile_interval_seconds: int
    serving_reconcile_batch_size: int
    idle_sleep_seconds: int
    error_sleep_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "WorkerProcessSettings":
        return cls(
            publication_reconcile_interval_seconds=max(
                5,
                settings.PUBLICATION_RECONCILE_INTERVAL_SECONDS,
            ),
            serving_reconcile_interval_seconds=max(
                60,
                settings.SERVING_RECONCILE_INTERVAL_SECONDS,
            ),
            serving_reconcile_batch_size=max(
                1,
                settings.SERVING_RECONCILE_BATCH_SIZE,
            ),
            idle_sleep_seconds=max(0, settings.WORKER_IDLE_SLEEP_SECONDS),
            error_sleep_seconds=max(0, settings.WORKER_ERROR_SLEEP_SECONDS),
        )


# Singleton dung chung — nap 1 lan luc import.
settings = Settings.from_env()


# ---------------------------------------------------------------------------
# Backward-compat: giu nguyen hang so cu de KHONG vo import hien co
#   `from ...config.settings import QDRANT_COLLECTION`
# ---------------------------------------------------------------------------
QDRANT_COLLECTION = settings.QDRANT_COLLECTION
