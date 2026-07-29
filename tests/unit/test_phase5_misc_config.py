from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from mech_chatbot.api import file_access
from mech_chatbot.config import logging as trace_logging
from mech_chatbot.ingestion import vision_cache
from mech_chatbot.ingestion.pdf.config import PdfIngestionConfig


pytestmark = pytest.mark.unit


def test_file_access_strict_site_policy_uses_explicit_value(monkeypatch):
    monkeypatch.setenv("RBAC_STRICT_SITE_FILTER", "false")

    assert file_access.strict_site_filter_enabled(True) is True
    assert file_access.strict_site_filter_enabled(False) is False


def test_pdf_ingestion_config_is_immutable_and_normalizes_paths(tmp_path):
    config = PdfIngestionConfig(
        image_dir=tmp_path / "images",
        vision_cache_dir=tmp_path / "cache",
        vision_cache_enabled=False,
        vision_prewarm_workers=3,
        pdf_render_dpi=144,
        metadata_text_limit=1234,
    )

    assert config.image_dir == Path(tmp_path / "images")
    assert config.vision_cache_dir == Path(tmp_path / "cache")
    assert config.vision_prewarm_workers == 3
    assert config.pdf_render_dpi == 144
    assert config.metadata_text_limit == 1234
    with pytest.raises((AttributeError, TypeError)):
        config.pdf_render_dpi = 300


def test_pdf_ingestion_config_projects_only_worker_values(tmp_path):
    snapshot = SimpleNamespace(
        EMBEDDING_CHUNK_SIZE=180,
        EMBEDDING_CHUNK_OVERLAP=30,
        EMBEDDING_MODEL="embedding-v2",
        LLM_METADATA_MODE="always",
        ENABLE_CONTEXTUAL_CHUNK=True,
        STRICT_INGEST_REQUIRE_VISION=True,
        ROLLBACK_ON_INGEST_ERROR=False,
        PDF_RENDER_DPI=200,
        METADATA_TEXT_LIMIT=9000,
        INGEST_VISION_PREWARM_WORKERS=4,
        VISION_CACHE_ENABLED=False,
        VISION_CACHE_DIR=str(tmp_path / "cache"),
    )

    config = PdfIngestionConfig.from_settings(snapshot)

    assert config.strict_require_vision is True
    assert config.embedding_chunk_size == 180
    assert config.embedding_chunk_overlap == 30
    assert config.embedding_model_name == "embedding-v2"
    assert config.llm_metadata_mode == "always"
    assert config.contextual_chunk_enabled is True
    assert config.rollback_on_error is False
    assert config.pdf_render_dpi == 200
    assert config.metadata_text_limit == 9000
    assert config.vision_prewarm_workers == 4
    assert config.vision_cache_enabled is False
    assert config.vision_cache_dir == tmp_path / "cache"


def test_vision_cache_uses_explicit_config_without_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_CACHE_ENABLED", "false")
    monkeypatch.setenv("VISION_CACHE_DIR", str(tmp_path / "wrong"))
    config = vision_cache.VisionCacheConfig(
        enabled=True,
        directory=tmp_path / "selected",
    )
    key = vision_cache.hash_image_bytes(b"page")

    assert vision_cache.put(key, {"materials": ["steel"]}, config=config)
    assert vision_cache.get(key, config=config) == {"materials": ["steel"]}
    assert not (tmp_path / "wrong").exists()


def test_logging_import_does_not_create_files_or_handlers(tmp_path, monkeypatch):
    system_logger = logging.getLogger("MechChatbot")
    trace_logger = logging.getLogger("RagTrace")
    for logger in (system_logger, trace_logger):
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    monkeypatch.chdir(tmp_path)
    reloaded = importlib.reload(trace_logging)

    assert not (tmp_path / "logs").exists()
    assert list(reloaded.logger.handlers) == []
    assert list(reloaded.trace_logger.handlers) == []


def test_logging_is_created_only_by_explicit_configuration(tmp_path):
    config = trace_logging.LoggingConfig(
        system_log_file=tmp_path / "system" / "chatbot.log",
        trace_log_file=tmp_path / "trace" / "rag.jsonl",
    )

    trace_logging.configure_logging(config)
    try:
        trace_logging.logger.info("configured")
        trace_logging.trace_logger.info('{"event":"configured"}')
        for target in (trace_logging.logger, trace_logging.trace_logger):
            for handler in target.handlers:
                handler.flush()

        assert config.system_log_file.exists()
        assert config.trace_log_file.exists()
    finally:
        for target in (trace_logging.logger, trace_logging.trace_logger):
            for handler in tuple(target.handlers):
                target.removeHandler(handler)
                handler.close()


def test_trace_execution_context_is_explicit(monkeypatch):
    events: list[str] = []
    monkeypatch.setenv("RAG_EXECUTION_CONTEXT", "evaluation")
    monkeypatch.setattr(trace_logging.trace_logger, "info", events.append)

    trace_logging.log_trace(
        "route",
        "trace-explicit",
        execution_context="test",
        latency_ms=1,
    )

    assert '"execution_context": "test"' in events[0]


def test_trace_callbacks_are_task_scoped_and_do_not_import_upper_layers(
    monkeypatch,
):
    events: list[dict] = []
    persisted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        trace_logging.trace_logger,
        "info",
        lambda message: events.append(json.loads(message)),
    )

    with trace_logging.bind_trace_runtime(
        trace_logging.TraceRuntime(
            persist=lambda trace_id, payload: persisted.append(
                (trace_id, payload)
            ),
            execution_context=lambda: "evaluation",
        )
    ):
        trace_logging.log_trace("rag_end", "trace-bound", refusal=False)

    trace_logging.log_trace("route", "trace-default")

    assert events[0]["execution_context"] == "evaluation"
    assert persisted[0][0] == "trace-bound"
    assert events[1]["execution_context"] == "production"


def test_maintenance_runtime_binds_and_closes_cli_dependencies(monkeypatch):
    from mech_chatbot.composition import maintenance_runtime
    from mech_chatbot.config.repository_runtime import (
        current_qdrant_runtime,
        current_repository_engine,
    )

    closed = []
    database = SimpleNamespace(
        engine=object(),
        close=lambda: closed.append("sql"),
    )
    qdrant = SimpleNamespace(
        client=object(),
        collection_name="KnowledgeBase",
        close=lambda: closed.append("qdrant"),
    )
    monkeypatch.setattr(maintenance_runtime, "load_settings", lambda: object())
    monkeypatch.setattr(
        maintenance_runtime.SqlSettings,
        "from_settings",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        maintenance_runtime.QdrantSettings,
        "from_settings",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        maintenance_runtime,
        "build_database_runtime",
        lambda _settings: database,
    )
    monkeypatch.setattr(
        maintenance_runtime,
        "build_qdrant_admin_runtime",
        lambda _settings: qdrant,
    )

    @maintenance_runtime.with_configured_repository_runtime(
        include_qdrant=True
    )
    def operation():
        return current_repository_engine(), current_qdrant_runtime()

    engine, (client, collection) = operation()

    assert engine is database.engine
    assert client is qdrant.client
    assert collection == "KnowledgeBase"
    assert closed == ["qdrant", "sql"]
    assert current_repository_engine() is None
