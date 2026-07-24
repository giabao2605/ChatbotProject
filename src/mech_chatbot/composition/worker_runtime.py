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
from mech_chatbot.application.vector_ingestion import IngestionPipelineDependencies
from mech_chatbot.config.settings import (
    QdrantSettings,
    Settings,
    VisionSettings,
    WorkerProcessSettings,
)


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


def _build_qdrant(settings: QdrantSettings) -> IngestionPipelineDependencies:
    from mech_chatbot.adapters.qdrant_runtime import build_qdrant_runtime

    return build_qdrant_runtime(settings)


def _build_vision(settings: VisionSettings) -> Any:
    api_key = str(settings.api_key or "").strip()
    if not api_key or api_key == "DIEN_KEY_CUA_BAN_VAO_DAY":
        return None

    from mech_chatbot.llm.vision_client import GPTVisionModel

    return GPTVisionModel(
        api_key,
        settings.model_name,
        settings.base_url,
    )


class _RepositoryProvider:
    def __init__(self) -> None:
        self._repository: Any | None = None

    def get(self) -> Any:
        if self._repository is None:
            from mech_chatbot.db import repository

            self._repository = repository
        return self._repository


def _build_job_store(repository: Any) -> RepositoryIngestionJobStore:
    return RepositoryIngestionJobStore(
        get_pending_job=repository.get_pending_job,
        update_ingestion_job=repository.update_ingestion_job,
        update_ingestion_report=repository.update_ingestion_report,
        mark_job_failed=repository.mark_job_failed,
        mark_job_waiting_quota=repository.mark_job_waiting_quota,
        write_audit_log=repository.write_audit_log,
        engine=repository.engine,
    )


def _build_runner(
    settings: Settings,
    job_store: Any,
    repository: Any,
    *,
    pipeline_dependencies: IngestionPipelineDependencies | None,
    vision_model: Any,
    qdrant_builder: QdrantBuilder,
    vision_builder: VisionBuilder,
) -> IngestionRunner:
    from mech_chatbot.ingestion.document_classifier import classify_document
    from mech_chatbot.ingestion.file_ingestor import learn_new_file_typed
    from mech_chatbot.llm.external_ai import external_processing_context

    dependencies = pipeline_dependencies or qdrant_builder(
        QdrantSettings.from_settings(settings)
    )
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
            load_governance=repository.get_department_knowledge_governance,
        ),
        processor=IngestionPipelineProcessor(
            partial(
                learn_new_file_typed,
                dependencies=dependencies,
                vision_model=vision,
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
) -> WorkerRuntime:
    """Build the worker graph while retaining existing production adapters.

    Callers may supply a complete fake graph for deterministic tests. The
    default branch imports the existing repository and legacy ingestion
    functions only at the composition boundary.
    """

    repository = _RepositoryProvider()
    resolved_store = job_store or _build_job_store(repository.get())
    resolved_runner = runner or _build_runner(
        settings_snapshot,
        resolved_store,
        repository.get(),
        pipeline_dependencies=pipeline_dependencies,
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
            reconcile_publications or repository.get().reconcile_publications
        ),
        reconcile_serving_state=(
            reconcile_serving_state or repository.get().reconcile_serving_state
        ),
        reconcile_job_failure=(
            reconcile_job_failure or resolved_store.reconcile_unexpected_failure
        ),
    )


__all__ = [
    "SystemWorkerClock",
    "WorkerClock",
    "WorkerJobStore",
    "WorkerRunner",
    "WorkerRuntime",
    "build_worker_runtime",
]
