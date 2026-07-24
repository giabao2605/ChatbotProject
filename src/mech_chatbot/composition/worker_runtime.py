"""Composition root for the ingestion worker process."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

from mech_chatbot.adapters.ingestion_runtime import (
    IngestionPipelineProcessor,
    LegacyDocumentClassifier,
    RepositoryIngestionJobStore,
)
from mech_chatbot.application.ingestion_runner import IngestionJob, IngestionResult, IngestionRunner
from mech_chatbot.application.vector_ingestion import (
    IngestionPersistence,
    IngestionPipelineDependencies,
)
from mech_chatbot.config.settings import (
    QdrantSettings,
    RepositoryPolicySettings,
    Settings,
    SqlSettings,
    VisionSettings,
    WorkerProcessSettings,
)
from mech_chatbot.db.engine import DatabaseRuntime, build_database_runtime


_MISSING = object()
QdrantBuilder = Callable[[QdrantSettings], IngestionPipelineDependencies]
VisionBuilder = Callable[[VisionSettings], Any]


class WorkerClock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class WorkerJobStore(Protocol):
    def claim_next(self, worker_id: str) -> IngestionJob | None: ...

    def mark_failed(self, job: IngestionJob, message: str) -> None: ...

    def mark_waiting_quota(self, job: IngestionJob, message: str) -> None: ...

    def reconcile_unexpected_failure(
        self,
        job: IngestionJob,
        error: Exception,
    ) -> None: ...


class WorkerRunner(Protocol):
    def run(self, job: IngestionJob) -> IngestionResult: ...


class SystemWorkerClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class WorkerRuntime:
    """Immutable dependencies for one worker process."""

    settings: WorkerProcessSettings
    worker_id: str
    job_store: WorkerJobStore
    runner: WorkerRunner
    clock: WorkerClock
    reconcile_publications: Callable[..., Any]
    reconcile_serving_state: Callable[..., Any]
    reconcile_job_failure: Callable[[IngestionJob, Exception], None]
    database_runtime: DatabaseRuntime | None = None
    pipeline_dependencies: IngestionPipelineDependencies | None = None
    owns_pipeline_dependencies: bool = False

    def close(self) -> None:
        if self.owns_pipeline_dependencies and self.pipeline_dependencies is not None:
            close = getattr(
                self.pipeline_dependencies.qdrant_client,
                "close",
                None,
            )
            if callable(close):
                close()
        if self.database_runtime is not None:
            self.database_runtime.close()


def _build_qdrant(settings: QdrantSettings) -> IngestionPipelineDependencies:
    from mech_chatbot.adapters.qdrant_runtime import build_qdrant_runtime

    return build_qdrant_runtime(settings)


def _build_vision(settings: VisionSettings) -> Any:
    api_key = str(settings.api_key or "").strip()
    if not api_key or api_key == "DIEN_KEY_CUA_BAN_VAO_DAY":
        return None

    from mech_chatbot.llm.vision_client import build_vision_model

    return build_vision_model(settings)


class _RepositoryProvider:
    def __init__(self) -> None:
        self._repository: Any | None = None

    def get(self) -> Any:
        if self._repository is None:
            from mech_chatbot.db import repository

            self._repository = repository
        return self._repository


def _with_engine(callback: Callable[..., Any], engine: Any) -> Callable[..., Any]:
    return partial(callback, db_engine=engine)


def _build_job_store(repository: Any, engine: Any) -> RepositoryIngestionJobStore:
    return RepositoryIngestionJobStore(
        get_pending_job=_with_engine(repository.get_pending_job, engine),
        update_ingestion_job=_with_engine(repository.update_ingestion_job, engine),
        update_ingestion_report=_with_engine(repository.update_ingestion_report, engine),
        mark_job_failed=_with_engine(repository.mark_job_failed, engine),
        mark_job_waiting_quota=_with_engine(
            repository.mark_job_waiting_quota,
            engine,
        ),
        write_audit_log=_with_engine(repository.write_audit_log, engine),
        engine=engine,
    )


def _build_ingestion_persistence(
    repository: Any,
    engine: Any,
    settings: Settings,
) -> IngestionPersistence:
    reingest_snapshots: dict[int, Any] = {}
    return IngestionPersistence(
        reset_document_metadata=partial(
            repository.reset_document_metadata,
            db_engine=engine,
            snapshot_store=reingest_snapshots,
            classification_model=settings.GPT_MODEL_NAME,
        ),
        get_document_info=_with_engine(repository.get_document_info, engine),
        update_document_classification=_with_engine(
            repository.update_document_classification,
            engine,
        ),
        clear_reingest_snapshot=partial(
            repository.clear_reingest_snapshot,
            snapshot_store=reingest_snapshots,
        ),
        mark_document_ingest_failed=_with_engine(
            repository.mark_document_ingest_failed,
            engine,
        ),
        restore_document_children=partial(
            repository.restore_document_children,
            db_engine=engine,
            snapshot_store=reingest_snapshots,
        ),
        save_bom_records=_with_engine(repository.save_bom_records, engine),
        save_technical_attributes=_with_engine(
            repository.save_technical_attributes,
            engine,
        ),
        save_document_attributes=_with_engine(
            repository.save_document_attributes,
            engine,
        ),
        save_document_page=_with_engine(repository.save_document_page, engine),
        save_page_metadata=_with_engine(repository.save_page_metadata, engine),
    )


def _build_runner(
    settings: Settings,
    job_store: Any,
    repository: Any,
    engine: Any,
    *,
    pipeline_dependencies: IngestionPipelineDependencies | None,
    vision_model: Any,
    qdrant_builder: QdrantBuilder,
    vision_builder: VisionBuilder,
) -> IngestionRunner:
    from mech_chatbot.ingestion.document_classifier import classify_document
    from mech_chatbot.ingestion.file_ingestor import learn_new_file_typed
    from mech_chatbot.ingestion.pdf.config import PdfIngestionConfig
    from mech_chatbot.llm.external_ai import external_processing_context

    dependencies = pipeline_dependencies or qdrant_builder(
        QdrantSettings.from_settings(settings)
    )
    persistence = _build_ingestion_persistence(repository, engine, settings)
    vision = (
        vision_builder(VisionSettings.from_settings(settings))
        if vision_model is _MISSING
        else vision_model
    )
    return IngestionRunner(
        job_store=job_store,
        classifier=LegacyDocumentClassifier(
            classify_document,
            processing_context=external_processing_context,
            load_governance=_with_engine(
                repository.get_department_knowledge_governance,
                engine,
            ),
        ),
        processor=IngestionPipelineProcessor(
            partial(
                learn_new_file_typed,
                dependencies=dependencies,
                persistence=persistence,
                vision_model=vision,
                config=PdfIngestionConfig.from_settings(settings),
            ),
            processing_context=external_processing_context,
        ),
    )


def build_worker_runtime(
    settings_snapshot: Settings,
    *,
    worker_id: str = "worker-1",
    job_store: WorkerJobStore | None = None,
    runner: WorkerRunner | None = None,
    clock: WorkerClock | None = None,
    reconcile_publications: Callable[..., Any] | None = None,
    reconcile_serving_state: Callable[..., Any] | None = None,
    reconcile_job_failure: Callable[[IngestionJob, Exception], None] | None = None,
    pipeline_dependencies: IngestionPipelineDependencies | None = None,
    vision_model: Any = _MISSING,
    qdrant_builder: QdrantBuilder = _build_qdrant,
    vision_builder: VisionBuilder = _build_vision,
    database_runtime: DatabaseRuntime | None = None,
) -> WorkerRuntime:
    """Build the worker graph while retaining existing production adapters.

    Callers may supply a complete fake graph for deterministic tests. The
    default branch imports the existing repository and legacy ingestion
    functions only at the composition boundary.
    """

    needs_repository = (
        job_store is None
        or runner is None
        or reconcile_publications is None
        or reconcile_serving_state is None
    )
    owned_database = database_runtime
    repository = _RepositoryProvider()
    if needs_repository and owned_database is None:
        owned_database = build_database_runtime(
            SqlSettings.from_settings(settings_snapshot)
        )
    repository_engine = owned_database.engine if owned_database is not None else None
    resolved_pipeline_dependencies = pipeline_dependencies
    owns_pipeline_dependencies = False
    if (
        resolved_pipeline_dependencies is None
        and (
            runner is None
            or reconcile_publications is None
            or reconcile_serving_state is None
        )
    ):
        resolved_pipeline_dependencies = qdrant_builder(
            QdrantSettings.from_settings(settings_snapshot)
        )
        owns_pipeline_dependencies = True
    resolved_store = job_store or _build_job_store(
        repository.get(),
        repository_engine,
    )
    resolved_runner = runner or _build_runner(
        settings_snapshot,
        resolved_store,
        repository.get(),
        repository_engine,
        pipeline_dependencies=resolved_pipeline_dependencies,
        vision_model=vision_model,
        qdrant_builder=qdrant_builder,
        vision_builder=vision_builder,
    )
    return WorkerRuntime(
        settings=WorkerProcessSettings.from_settings(settings_snapshot),
        worker_id=worker_id,
        job_store=resolved_store,
        runner=resolved_runner,
        clock=clock or SystemWorkerClock(),
        reconcile_publications=(
            reconcile_publications
            or partial(
                repository.get().reconcile_publications,
                db_engine=repository_engine,
                policy=RepositoryPolicySettings.from_settings(settings_snapshot),
                qdrant_client=resolved_pipeline_dependencies.qdrant_client,
                collection_name=resolved_pipeline_dependencies.collection_name,
            )
        ),
        reconcile_serving_state=(
            reconcile_serving_state
            or partial(
                repository.get().reconcile_serving_state,
                db_engine=repository_engine,
                qdrant_client=resolved_pipeline_dependencies.qdrant_client,
                collection_name=resolved_pipeline_dependencies.collection_name,
            )
        ),
        reconcile_job_failure=(
            reconcile_job_failure or resolved_store.reconcile_unexpected_failure
        ),
        database_runtime=owned_database,
        pipeline_dependencies=resolved_pipeline_dependencies,
        owns_pipeline_dependencies=owns_pipeline_dependencies,
    )


__all__ = [
    "SystemWorkerClock",
    "WorkerClock",
    "WorkerJobStore",
    "WorkerRunner",
    "WorkerRuntime",
    "build_worker_runtime",
]
