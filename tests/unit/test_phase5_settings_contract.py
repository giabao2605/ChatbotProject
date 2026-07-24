from dataclasses import FrozenInstanceError

import pytest

from mech_chatbot.config.settings import (
    AppProcessSettings,
    LlmSettings,
    QdrantSettings,
    RagProcessSettings,
    Settings,
    SqlSettings,
    VisionSettings,
    WorkerProcessSettings,
)


pytestmark = pytest.mark.unit


def test_settings_parse_one_explicit_environment_snapshot():
    environment = {
        "APP_THREAD_LIMIT": "7",
        "APP_RAG_CHAT_TIMEOUT_SECONDS": "45",
        "RAG_REQUIRE_SERVICE_AUTH": "false",
        "RAG_CORS_ALLOW_ORIGINS": "https://one.example, https://two.example",
        "PUBLICATION_RECONCILE_INTERVAL_SECONDS": "21",
        "SERVING_RECONCILE_INTERVAL_SECONDS": "900",
        "SERVING_RECONCILE_BATCH_SIZE": "250",
        "QDRANT_URL": "https://qdrant.example",
        "QDRANT_API_KEY": "qdrant-secret",
        "PROXYLLM_API_KEY": "llm-secret",
        "PROXYLLM_BASE_URL": "https://llm.example/v1",
        "GPT_VISION_JPEG_QUALITY": "90",
    }

    settings = Settings.from_env(environment)
    environment["APP_THREAD_LIMIT"] = "99"

    assert settings.APP_THREAD_LIMIT == 7
    assert settings.RAG_REQUIRE_SERVICE_AUTH is False
    assert settings.RAG_CORS_ALLOW_ORIGINS == (
        "https://one.example",
        "https://two.example",
    )
    assert settings.PUBLICATION_RECONCILE_INTERVAL_SECONDS == 21
    assert settings.LLM_API_KEY == "llm-secret"


def test_process_projections_are_frozen_and_keep_existing_defaults():
    settings = Settings.from_env({})

    app = AppProcessSettings.from_settings(settings)
    rag = RagProcessSettings.from_settings(settings)
    worker = WorkerProcessSettings.from_settings(settings)

    assert app.thread_limit == 60
    assert app.rag_base_url == "http://127.0.0.1:8100"
    assert app.rag_chat_timeout_seconds == 300
    assert app.cookie_secure is False
    assert app.cookie_samesite == "lax"
    assert app.session_ttl_seconds == 2700
    assert rag.host == "0.0.0.0"
    assert rag.port == 8100
    assert rag.max_concurrent_requests == 2
    assert rag.require_service_auth is True
    assert worker.publication_reconcile_interval_seconds == 15
    assert worker.serving_reconcile_interval_seconds == 600
    assert worker.serving_reconcile_batch_size == 500
    assert worker.idle_sleep_seconds == 5
    assert worker.error_sleep_seconds == 10

    with pytest.raises(FrozenInstanceError):
        app.thread_limit = 99


def test_app_security_projection_preserves_secret_fallback_and_clamps_values():
    settings = Settings.from_env(
        {
            "CHAT_BRIDGE_SECRET": "bridge-secret",
            "APP_COOKIE_SECURE": "true",
            "APP_COOKIE_SAMESITE": "invalid",
            "APP_SESSION_TTL_SECONDS": "999999",
        }
    )

    app = AppProcessSettings.from_settings(settings)

    assert app.session_secret == "bridge-secret"
    assert app.cookie_secure is True
    assert app.cookie_samesite == "lax"
    assert app.session_ttl_seconds == 86400


def test_adapter_projections_expose_only_their_required_configuration():
    settings = Settings.from_env(
        {
            "SQL_SERVER": "sql-host",
            "SQL_DATABASE": "knowledge",
            "SQL_TRUSTED_CONNECTION": "false",
            "SQL_USERNAME": "chatbot",
            "SQL_PASSWORD": "sql-secret",
            "QDRANT_URL": "https://qdrant.example",
            "QDRANT_API_KEY": "qdrant-secret",
            "QDRANT_COLLECTION": "KnowledgeBase",
            "PROXYLLM_API_KEY": "llm-secret",
            "PROXYLLM_BASE_URL": "https://llm.example/v1",
            "GPT_MODEL_NAME": "gpt-test",
            "GPT_VISION_MODEL_NAME": "vision-test",
        }
    )

    sql = SqlSettings.from_settings(settings)
    qdrant = QdrantSettings.from_settings(settings)
    llm = LlmSettings.from_settings(settings)
    vision = VisionSettings.from_settings(settings)

    assert sql.server == "sql-host"
    assert sql.trusted_connection is False
    assert qdrant.collection == "KnowledgeBase"
    assert qdrant.url == "https://qdrant.example"
    assert llm.model_name == "gpt-test"
    assert vision.model_name == "vision-test"
    assert not hasattr(qdrant, "SQL_PASSWORD")
    assert not hasattr(sql, "QDRANT_API_KEY")
