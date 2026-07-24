from dataclasses import FrozenInstanceError

import pytest

from mech_chatbot.config.settings import (
    AppProcessSettings,
    ExternalAiSettings,
    LlmSettings,
    QdrantSettings,
    RagProcessSettings,
    RepositoryPolicySettings,
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
    assert rag.hyde_enabled is True
    assert rag.query_rewrite_enabled is True
    assert rag.late_interaction_enabled is False
    assert rag.late_encoder_ready is False
    assert settings.RERANK_PER_PART == 8
    assert settings.RERANK_TOP_N_CAP == 20
    assert worker.publication_reconcile_interval_seconds == 15
    assert worker.serving_reconcile_interval_seconds == 600
    assert worker.serving_reconcile_batch_size == 500
    assert worker.idle_sleep_seconds == 5
    assert worker.error_sleep_seconds == 10

    with pytest.raises(FrozenInstanceError):
        app.thread_limit = 99


def test_rag_process_projection_snapshots_query_expansion_flags():
    settings = Settings.from_env(
        {
            "HYDE_ENABLED": "false",
            "ENABLE_QUERY_REWRITE": "0",
            "RAG_EXECUTION_CONTEXT": "evaluation",
            "RAG_REQUEST_DEADLINE_SECONDS": "45.5",
            "EVALUATION_FORCE_AMBIGUOUS": "true",
            "RBAC_STRICT_SITE_FILTER": "false",
            "RAG_LATE_MODEL": "local/late-model",
            "RAG_LATE_QUERY_MAX_LENGTH": "72",
            "RAG_LATE_DOCUMENT_MAX_LENGTH": "56",
            "RAG_LATE_COLLECTION": "late-shadow",
            "RERANK_MAX_CHUNKS_PER_DOCUMENT": "6",
            "RERANK_MAX_CHUNKS_PER_SECTION": "2",
            "RERANK_CANDIDATE_CAP": "24",
            "RAG_AUTO_SOURCE_CARDS": "false",
            "GPT_STREAM_MAX_ATTEMPTS": "4",
            "STRICT_STREAMING_HOLDBACK_CHARS": "192",
            "RAG_EVAL_DRAFT_OVERRIDE": "controlled draft",
            "PARENT_CONTEXT_ENABLED": "false",
            "PARENT_CONTEXT_MAX_SECTIONS": "9",
            "PARENT_CONTEXT_MAX_CHUNKS": "7",
            "CITATION_MAX_SOURCES": "6",
            "BOM_CITATION_MAX_SOURCES": "4",
            "RAG_EVAL_FORCE_AMBIGUOUS": "true",
        }
    )

    rag = RagProcessSettings.from_settings(settings)

    assert rag.hyde_enabled is False
    assert rag.query_rewrite_enabled is False
    assert rag.execution_context == "evaluation"
    assert rag.request_deadline_seconds == 45.5
    assert rag.evaluation_force_ambiguous is True
    assert rag.strict_site_filter is False
    assert rag.late_model_name == "local/late-model"
    assert rag.late_query_max_length == 72
    assert rag.late_document_max_length == 56
    assert rag.late_collection == "late-shadow"
    assert rag.rerank_max_chunks_per_document == 6
    assert rag.rerank_max_chunks_per_section == 2
    assert rag.rerank_candidate_cap == 24
    assert rag.auto_source_cards is False
    assert rag.stream_max_attempts == 4
    assert rag.streaming_holdback_chars == 192
    assert rag.eval_draft_override == "controlled draft"
    assert rag.parent_context_enabled is False
    assert rag.parent_context_max_sections == 9
    assert rag.parent_context_max_chunks == 7
    assert rag.citation_max_sources == 6
    assert rag.bom_citation_max_sources == 4
    assert rag.eval_force_ambiguous is True


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
    repository_policy = RepositoryPolicySettings.from_settings(settings)

    assert sql.server == "sql-host"
    assert sql.trusted_connection is False
    assert qdrant.collection == "KnowledgeBase"
    assert qdrant.url == "https://qdrant.example"
    assert llm.model_name == "gpt-test"
    assert vision.model_name == "vision-test"
    assert vision.min_interval_seconds == 0.0
    assert repository_policy.strict_site_filter is True
    assert repository_policy.publication_max_attempts == 5
    assert not hasattr(qdrant, "SQL_PASSWORD")
    assert not hasattr(sql, "QDRANT_API_KEY")


def test_repository_policy_projection_snapshots_governance_overrides():
    settings = Settings.from_env(
        {
            "RBAC_STRICT_SITE_FILTER": "false",
            "KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE": "true",
            "KNOWLEDGE_ALLOW_ADMIN_APPROVAL_OVERRIDE": "1",
            "PUBLICATION_MAX_ATTEMPTS": "7",
        }
    )

    policy = RepositoryPolicySettings.from_settings(settings)

    assert policy.strict_site_filter is False
    assert policy.allow_admin_metadata_override is True
    assert policy.allow_admin_approval_override is True
    assert policy.publication_max_attempts == 7


def test_external_ai_projection_snapshots_local_policy_without_secrets():
    settings = Settings.from_env(
        {
            "APP_ENV": "development",
            "EXTERNAL_AI_LOCAL_DEVELOPMENT": "true",
            "EXTERNAL_PROCESSING_POLICY": "internal_only",
        }
    )

    external_ai = ExternalAiSettings.from_settings(settings)

    assert external_ai.application_environment == "development"
    assert external_ai.local_development is True
    assert external_ai.processing_policy == "internal_only"
    assert not hasattr(external_ai, "LLM_API_KEY")


def test_settings_snapshot_preserves_misc_phase_five_flags():
    settings = Settings.from_env(
        {
            "ENABLE_CONTEXTUAL_CHUNK": "true",
            "RAG_TRACE_LOG_FILE": "runtime/trace.jsonl",
        }
    )

    assert settings.ENABLE_CONTEXTUAL_CHUNK is True
    assert settings.RAG_TRACE_LOG_FILE == "runtime/trace.jsonl"
