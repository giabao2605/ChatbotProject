"""Process adapter for the application-owned ingestion lifecycle."""

from __future__ import annotations

import sys

from mech_chatbot.application.ingestion_runner import IngestionJob
from mech_chatbot.composition.worker_runtime import WorkerRuntime, build_worker_runtime
from mech_chatbot.config.logging import logger
from mech_chatbot.config.worker_settings import worker_int


def _reconcile(runtime: WorkerRuntime, last_publication: float, last_serving: float) -> tuple[float, float]:
    now = runtime.clock.monotonic()
    publication_interval = worker_int("PUBLICATION_RECONCILE_INTERVAL_SECONDS", 15, 5)
    serving_interval = worker_int("SERVING_RECONCILE_INTERVAL_SECONDS", 600, 60)
    if now - last_publication >= publication_interval:
        try:
            summary = runtime.reconcile_publications(limit=10)
            if summary.get("processed"):
                logger.info("Publication reconciliation: %s", summary)
        except Exception as exc:  # noqa: BLE001 - reconciliation must not stop polling
            logger.warning("Publication reconciliation failed: %s", exc)
        last_publication = now
    if now - last_serving >= serving_interval:
        try:
            summary = runtime.reconcile_serving_state(
                limit=worker_int("SERVING_RECONCILE_BATCH_SIZE", 500, 1),
                worker_id="ingestion-worker-serving-reconciler",
            )
            if summary.get("failed_doc_ids"):
                logger.warning("Serving reconciliation has failed docs: %s", summary)
        except Exception as exc:  # noqa: BLE001 - reconciliation must not stop polling
            logger.warning("Serving reconciliation failed: %s", exc)
        last_serving = now
    return last_publication, last_serving


def run_worker(runtime: WorkerRuntime | None = None) -> None:
    """Poll, claim, and delegate jobs; business decisions stay in the runner."""

    resolved_runtime = runtime or build_worker_runtime(None)
    logger.info("Khởi động Ingestion Worker chạy ngầm...")
    print("Ingestion Worker đã sẵn sàng. Đang chờ file mới...")
    last_publication_reconcile = 0.0
    last_serving_reconcile = 0.0

    while True:
        job: IngestionJob | None = None
        try:
            last_publication_reconcile, last_serving_reconcile = _reconcile(
                resolved_runtime,
                last_publication_reconcile,
                last_serving_reconcile,
            )
            job = resolved_runtime.job_store.claim_next(resolved_runtime.worker_id)
            if job is None:
                resolved_runtime.clock.sleep(5)
                continue
            logger.info("Worker bắt đầu xử lý JobID %s: %s", job.job_id, job.file_name)
            result = resolved_runtime.runner.run(job)
            logger.info(
                "Job %s kết thúc với outcome=%s reason=%s",
                job.job_id,
                result.outcome,
                result.reason_code,
            )
        except Exception as exc:  # noqa: BLE001 - process loop must reconcile a claimed job
            logger.error("Lỗi không xác định trong Ingestion Worker: %s", exc, exc_info=True)
            if job is not None:
                resolved_runtime.reconcile_job_failure(job, exc)
            resolved_runtime.clock.sleep(10)


if __name__ == "__main__":
    from mech_chatbot.config.validate import assert_config_valid

    assert_config_valid()
    sys.stdout.reconfigure(encoding="utf-8")
    run_worker()
