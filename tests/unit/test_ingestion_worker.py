from __future__ import annotations

from dataclasses import dataclass

import pytest

from mech_chatbot.application.ingestion_runner import IngestionJob, IngestionResult
from mech_chatbot.composition.worker_runtime import WorkerRuntime
from mech_chatbot.workers import ingestion_worker


class _StopWorker(BaseException):
    pass


@dataclass
class FakeClock:
    now: float = 1_000.0
    sleep_calls: list[float] | None = None

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if self.sleep_calls is None:
            self.sleep_calls = []
        self.sleep_calls.append(seconds)
        raise _StopWorker


class FakeStore:
    def __init__(self, jobs: list[IngestionJob | None]) -> None:
        self.jobs = iter(jobs)
        self.claimed: list[tuple[str, str]] = []
        self.failed: list[tuple[int, str]] = []
        self.waiting: list[tuple[int, str]] = []

    def claim_next(self, worker_id: str):
        job = next(self.jobs)
        if job is not None:
            self.claimed.append((worker_id, job.file_name))
        return job

    def mark_failed(self, job, message: str) -> None:
        self.failed.append((job.job_id, message))

    def mark_waiting_quota(self, job, message: str) -> None:
        self.waiting.append((job.job_id, message))

    def reconcile_unexpected_failure(self, job, error: Exception) -> None:
        message = str(error)
        if "quota" in message or "resource_exhausted" in message:
            self.mark_waiting_quota(job, message)
        else:
            self.mark_failed(job, message)


class FakeRunner:
    def __init__(self, result: IngestionResult | None = None, error: Exception | None = None) -> None:
        self.result = result or IngestionResult("pending_review", {}, None, "done")
        self.error = error
        self.jobs: list[IngestionJob] = []

    def run(self, job: IngestionJob) -> IngestionResult:
        self.jobs.append(job)
        if self.error is not None:
            raise self.error
        return self.result


def _job() -> IngestionJob:
    return IngestionJob(
        job_id=17,
        file_path="C:/incoming/manual.pdf",
        file_name="manual.pdf",
        owner_department="quality",
        shared_departments=("QA",),
        domain="mechanical",
        security_level="internal",
        process_stage="assembly",
        site="HCM",
    )


def _runtime(store, runner, clock, publication=None, serving=None) -> WorkerRuntime:
    return WorkerRuntime(
        settings=None,
        worker_id="worker-test",
        job_store=store,
        runner=runner,
        clock=clock,
        reconcile_publications=publication or (lambda **_: {}),
        reconcile_serving_state=serving or (lambda **_: {}),
        reconcile_job_failure=store.reconcile_unexpected_failure,
    )


def test_worker_sleeps_when_no_job_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    store = FakeStore([None])
    runner = FakeRunner()

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker(_runtime(store, runner, clock))

    assert clock.sleep_calls == [5]
    assert runner.jobs == []


def test_worker_reconciles_before_claiming() -> None:
    clock = FakeClock()
    store = FakeStore([None])
    runner = FakeRunner()
    calls: list[str] = []

    def publication(**kwargs):
        calls.append("publication")
        return {"processed": 2}

    def serving(**kwargs):
        calls.append("serving")
        return {"failed_doc_ids": []}

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker(_runtime(store, runner, clock, publication, serving))

    assert calls == ["publication", "serving"]


def test_worker_logs_failed_serving_reconciliation_without_stopping() -> None:
    clock = FakeClock()
    store = FakeStore([None])
    runner = FakeRunner()

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker(
            _runtime(
                store,
                runner,
                clock,
                serving=lambda **_kwargs: {"failed_doc_ids": [17]},
            )
        )

    assert clock.sleep_calls == [5]
    assert runner.jobs == []


def test_worker_continues_when_reconciliation_fails() -> None:
    clock = FakeClock()
    store = FakeStore([None])
    runner = FakeRunner()

    def unavailable(**kwargs):
        raise RuntimeError("reconciler unavailable")

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker(
            _runtime(store, runner, clock, unavailable, unavailable)
        )

    assert clock.sleep_calls == [5]
    assert runner.jobs == []


def test_worker_delegates_lifecycle_to_runner_without_state_decisions() -> None:
    clock = FakeClock()
    store = FakeStore([_job(), None])
    runner = FakeRunner(IngestionResult("blocked", {"quality_status": "blocked"}, "quality_blocked", "blocked"))

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker(_runtime(store, runner, clock))

    assert runner.jobs == [_job()]
    assert store.failed == []
    assert store.waiting == []


@pytest.mark.parametrize(
    ("message", "waiting"),
    [("resource_exhausted: quota exceeded", True), ("unexpected parser failure", False)],
)
def test_worker_reconciles_unexpected_runner_errors(
    message: str,
    waiting: bool,
) -> None:
    clock = FakeClock()
    job = _job()
    store = FakeStore([job])
    runner = FakeRunner(error=RuntimeError(message))

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker(_runtime(store, runner, clock))

    assert clock.sleep_calls == [10]
    if waiting:
        assert store.waiting == [(job.job_id, message)]
        assert store.failed == []
    else:
        assert store.failed == [(job.job_id, message)]
        assert store.waiting == []
