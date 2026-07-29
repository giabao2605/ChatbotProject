from __future__ import annotations

import builtins
from contextlib import nullcontext
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from mech_chatbot.application.ingestion_runner import IngestionJob, IngestionResult
from mech_chatbot.application.vector_ingestion import IngestionPipelineDependencies
from mech_chatbot.composition import worker_runtime
from mech_chatbot.composition.worker_runtime import WorkerRuntime, build_worker_runtime
from mech_chatbot.config.settings import (
    QdrantSettings,
    Settings,
    VisionSettings,
    WorkerProcessSettings,
)


class FakeStore:
    def claim_next(self, worker_id):
        return None

    def mark_failed(self, job, message):
        return None

    def mark_waiting_quota(self, job, message):
        return None

    def reconcile_unexpected_failure(self, job, error):
        return None


class CompleteFakeStore(FakeStore):
    def record_classification(self, job, result):
        return None

    def record_progress(self, job, event):
        return None

    def save_report(self, job, report):
        return True

    def mark_pending_review(self, job):
        return None


class FakeRunner:
    def run(self, job):
        return IngestionResult("pending_review", {}, None, "done")


class FakeClock:
    def monotonic(self):
        return 0.0

    def sleep(self, seconds):
        return None


def _settings(**overrides) -> Settings:
    values = {
        "QDRANT_URL": "https://qdrant.test",
        "QDRANT_API_KEY": "test-key",
        **{key: str(value) for key, value in overrides.items()},
    }
    return Settings.from_env(values)


def test_build_worker_runtime_retains_supplied_graph_without_infrastructure_import(
    monkeypatch,
) -> None:
    settings = _settings(PUBLICATION_RECONCILE_INTERVAL_SECONDS=23)
    store = FakeStore()
    runner = FakeRunner()
    clock = FakeClock()
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("mech_chatbot.db"):
            raise AssertionError("fake runtime must not import SQL infrastructure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    runtime = build_worker_runtime(
        settings,
        worker_id="worker-test",
        job_store=store,
        runner=runner,
        clock=clock,
        reconcile_publications=lambda **_: {},
        reconcile_serving_state=lambda **_: {},
        reconcile_job_failure=store.reconcile_unexpected_failure,
    )

    assert isinstance(runtime, WorkerRuntime)
    assert runtime.settings == WorkerProcessSettings.from_settings(settings)
    assert not hasattr(runtime.settings, "QDRANT_API_KEY")
    assert runtime.job_store is store
    assert runtime.runner is runner
    assert runtime.clock is clock


def test_build_default_worker_runtime_injects_composed_pipeline_dependencies(
    monkeypatch,
) -> None:
    from mech_chatbot import db as db_package

    captured = {}
    repository = SimpleNamespace(
        get_pending_job=lambda worker_id: None,
        update_ingestion_job=lambda *args, **kwargs: None,
        update_ingestion_report=lambda *args, **kwargs: True,
        mark_job_failed=lambda *args, **kwargs: None,
        mark_job_waiting_quota=lambda *args, **kwargs: None,
        write_audit_log=lambda *args, **kwargs: None,
        get_department_knowledge_governance=lambda _department: {
            "external_processing_policy": "all_external",
            "is_active": True,
        },
        reset_document_metadata=lambda *args, **kwargs: 1,
        get_document_info=lambda *args, **kwargs: {
            "external_processing_policy": "internal_only",
        },
        update_document_classification=lambda *args, **kwargs: True,
        clear_reingest_snapshot=lambda *args, **kwargs: None,
        mark_document_ingest_failed=lambda *args, **kwargs: None,
        restore_document_children=lambda *args, **kwargs: False,
        save_bom_records=lambda *args, **kwargs: 0,
        save_technical_attributes=lambda *args, **kwargs: None,
        save_document_attributes=lambda *args, **kwargs: None,
        save_document_page=lambda *args, **kwargs: None,
        save_page_metadata=lambda *args, **kwargs: 1,
        engine=object(),
        reconcile_publications=lambda **kwargs: {},
        reconcile_serving_state=lambda **kwargs: {},
    )
    monkeypatch.setattr(db_package, "repository", repository, raising=False)

    classifier_module = ModuleType("mech_chatbot.ingestion.document_classifier")
    classifier_module.classify_document = lambda *args, **kwargs: {}  # type: ignore[attr-defined]
    file_module = ModuleType("mech_chatbot.ingestion.file_ingestor")

    def learn_new_file_typed(**kwargs):
        captured.update(kwargs)
        return True, "done", {"quality_status": "ready_for_review"}

    file_module.learn_new_file_typed = learn_new_file_typed  # type: ignore[attr-defined]
    external_module = ModuleType("mech_chatbot.llm.external_ai")
    external_module.external_processing_context = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: nullcontext()
    )
    monkeypatch.setitem(sys.modules, classifier_module.__name__, classifier_module)
    monkeypatch.setitem(sys.modules, file_module.__name__, file_module)
    monkeypatch.setitem(sys.modules, external_module.__name__, external_module)

    dependencies = IngestionPipelineDependencies(
        vector_store=object(),
        qdrant_client=object(),
        collection_name="KnowledgeBase",
    )
    vision_model = object()
    qdrant_settings_seen = []
    vision_settings_seen = []

    def qdrant_builder(settings: QdrantSettings):
        qdrant_settings_seen.append(settings)
        return dependencies

    def vision_builder(settings: VisionSettings):
        vision_settings_seen.append(settings)
        return vision_model

    settings = _settings(QDRANT_COLLECTION="KnowledgeBase")
    runtime = build_worker_runtime(
        settings,
        job_store=CompleteFakeStore(),
        qdrant_builder=qdrant_builder,
        vision_builder=vision_builder,
    )
    runtime.runner.run(
        IngestionJob(
            job_id=1,
            file_path=Path("manual.pdf"),
            file_name="manual.pdf",
            owner_department="quality",
            shared_departments=(),
            domain=None,
            security_level=None,
            process_stage=None,
            site=None,
        )
    )

    assert runtime.worker_id == "worker-1"
    assert qdrant_settings_seen == [QdrantSettings.from_settings(settings)]
    assert vision_settings_seen == [VisionSettings.from_settings(settings)]
    assert captured["dependencies"] is dependencies
    assert captured["vision_model"] is vision_model
    assert captured["persistence"] is not None


def test_system_clock_delegates_to_time_module(monkeypatch) -> None:
    sleeps = []
    monkeypatch.setattr(worker_runtime.time, "monotonic", lambda: 12.5)
    monkeypatch.setattr(worker_runtime.time, "sleep", sleeps.append)
    clock = worker_runtime.SystemWorkerClock()

    assert clock.monotonic() == 12.5
    clock.sleep(3)
    assert sleeps == [3]


def test_default_vision_builder_uses_explicit_snapshot(monkeypatch) -> None:
    created = []
    vision_module = ModuleType("mech_chatbot.llm.vision_client")
    monkeypatch.setenv("PROXYLLM_API_KEY", "ambient-key")
    monkeypatch.setenv("PROXYLLM_BASE_URL", "https://ambient.test/v1")
    monkeypatch.setenv("GPT_VISION_MODEL_NAME", "ambient-vision")

    model = object()

    def build_vision_model(snapshot):
        created.append(snapshot)
        return model

    vision_module.build_vision_model = build_vision_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, vision_module.__name__, vision_module)
    settings = VisionSettings(
        **{
            "api" + "_key": "configured-test-value",
            "base_url": "https://snapshot.test/v1",
            "model_name": "snapshot-vision",
            "image_format": "jpeg",
            "max_edge": 0,
            "jpeg_quality": 85,
            "temperature": 0.0,
            "max_output_tokens": 4096,
            "timeout_seconds": 120.0,
            "min_interval_seconds": 0.25,
        }
    )

    result = worker_runtime._build_vision(settings)

    assert result is model
    assert created == [settings]


def test_worker_runtime_closes_only_composition_owned_resources(monkeypatch) -> None:
    class Closable:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    class Database:
        def __init__(self):
            self.engine = object()
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    qdrant_client = Closable()
    database = Database()
    dependencies = IngestionPipelineDependencies(
        vector_store=object(),
        qdrant_client=qdrant_client,
        collection_name="KnowledgeBase",
    )
    store = FakeStore()

    runtime = build_worker_runtime(
        _settings(QDRANT_COLLECTION="KnowledgeBase"),
        job_store=store,
        runner=FakeRunner(),
        clock=FakeClock(),
        reconcile_publications=lambda **_: {},
        reconcile_serving_state=lambda **_: {},
        reconcile_job_failure=store.reconcile_unexpected_failure,
        database_runtime=database,
        qdrant_builder=lambda _settings: dependencies,
    )

    runtime.close()

    assert database.close_calls == 1
    assert qdrant_client.close_calls == 0

    owned = WorkerRuntime(
        settings=runtime.settings,
        worker_id="worker-owned",
        job_store=store,
        runner=FakeRunner(),
        clock=FakeClock(),
        reconcile_publications=lambda **_: {},
        reconcile_serving_state=lambda **_: {},
        reconcile_job_failure=store.reconcile_unexpected_failure,
        pipeline_dependencies=dependencies,
        owns_pipeline_dependencies=True,
    )

    owned.close()

    assert qdrant_client.close_calls == 1
