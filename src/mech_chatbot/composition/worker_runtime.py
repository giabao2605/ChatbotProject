"""Composition root for the ingestion worker process."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mech_chatbot.adapters.ingestion_runtime import (
    IngestionPipelineProcessor,
    LegacyDocumentClassifier,
    RepositoryIngestionJobStore,
)
from mech_chatbot.application.ingestion_runner import IngestionJob, IngestionResult, IngestionRunner


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

    settings: Any
    worker_id: str
    job_store: WorkerJobStore
    runner: WorkerRunner
    clock: WorkerClock
    reconcile_publications: Callable[..., Any]
    reconcile_serving_state: Callable[..., Any]
    reconcile_job_failure: Callable[[IngestionJob, Exception], None]


def build_worker_runtime(
    existing_settings: Any,
    *,
    worker_id: str = "worker-1",
    job_store: WorkerJobStore | None = None,
    runner: WorkerRunner | None = None,
    clock: WorkerClock | None = None,
    reconcile_publications: Callable[..., Any] | None = None,
    reconcile_serving_state: Callable[..., Any] | None = None,
    reconcile_job_failure: Callable[[IngestionJob, Exception], None] | None = None,
) -> WorkerRuntime:
    """Build the worker graph while retaining existing production adapters.

    Callers may supply a complete fake graph for deterministic tests. The
    default branch imports the existing repository and legacy ingestion
    functions only at the composition boundary.
    """

    repository: Any | None = None

    def repository_module() -> Any:
        nonlocal repository
        if repository is None:
            from mech_chatbot.db import repository as loaded_repository

            repository = loaded_repository
        return repository

    resolved_store = job_store
    if resolved_store is None:
        repo = repository_module()
        resolved_store = RepositoryIngestionJobStore(
            get_pending_job=repo.get_pending_job,
            update_ingestion_job=repo.update_ingestion_job,
            update_ingestion_report=repo.update_ingestion_report,
            mark_job_failed=repo.mark_job_failed,
            mark_job_waiting_quota=repo.mark_job_waiting_quota,
            write_audit_log=repo.write_audit_log,
            engine=repo.engine,
        )

    resolved_runner = runner
    if resolved_runner is None:
        from mech_chatbot.ingestion.document_classifier import classify_document
        from mech_chatbot.ingestion.file_ingestor import learn_new_file_typed
        from mech_chatbot.llm.external_ai import external_processing_context

        resolved_runner = IngestionRunner(
            job_store=resolved_store,
            classifier=LegacyDocumentClassifier(
                classify_document,
                processing_context=external_processing_context,
                load_governance=repo.get_department_knowledge_governance,
            ),
            processor=IngestionPipelineProcessor(
                learn_new_file_typed,
                processing_context=external_processing_context,
            ),
        )

    return WorkerRuntime(
        settings=existing_settings,
        worker_id=worker_id,
        job_store=resolved_store,
        runner=resolved_runner,
        clock=clock or SystemWorkerClock(),
        reconcile_publications=(
            reconcile_publications or repository_module().reconcile_publications
        ),
        reconcile_serving_state=(
            reconcile_serving_state or repository_module().reconcile_serving_state
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
