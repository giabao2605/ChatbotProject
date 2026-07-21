from __future__ import annotations

import builtins
from contextlib import nullcontext
import sys
from types import ModuleType, SimpleNamespace

from mech_chatbot.application.ingestion_runner import IngestionResult
from mech_chatbot.composition import worker_runtime
from mech_chatbot.composition.worker_runtime import WorkerRuntime, build_worker_runtime


class FakeStore:
    def claim_next(self, worker_id):
        return None

    def mark_failed(self, job, message):
        return None

    def mark_waiting_quota(self, job, message):
        return None

    def reconcile_unexpected_failure(self, job, error):
        return None


class FakeRunner:
    def run(self, job):
        return IngestionResult("pending_review", {}, None, "done")


class FakeClock:
    def monotonic(self):
        return 0.0

    def sleep(self, seconds):
        return None


def test_build_worker_runtime_retains_supplied_graph_without_infrastructure_import(
    monkeypatch,
) -> None:
    settings = object()
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
    assert runtime.settings is settings
    assert runtime.job_store is store
    assert runtime.runner is runner
    assert runtime.clock is clock


def test_build_default_worker_runtime_assembles_existing_adapters(monkeypatch) -> None:
    from mech_chatbot import db as db_package

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
        engine=object(),
        reconcile_publications=lambda **kwargs: {},
        reconcile_serving_state=lambda **kwargs: {},
    )
    monkeypatch.setattr(db_package, "repository", repository, raising=False)

    classifier_module = ModuleType("mech_chatbot.ingestion.document_classifier")
    classifier_module.classify_document = lambda *args, **kwargs: {}  # type: ignore[attr-defined]
    file_module = ModuleType("mech_chatbot.ingestion.file_ingestor")
    file_module.learn_new_file_typed = (  # type: ignore[attr-defined]
        lambda **kwargs: (True, "done", {"quality_status": "ready_for_review"})
    )
    external_module = ModuleType("mech_chatbot.llm.external_ai")
    external_module.external_processing_context = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: nullcontext()
    )
    monkeypatch.setitem(sys.modules, classifier_module.__name__, classifier_module)
    monkeypatch.setitem(sys.modules, file_module.__name__, file_module)
    monkeypatch.setitem(sys.modules, external_module.__name__, external_module)

    runtime = build_worker_runtime(object())

    assert runtime.worker_id == "worker-1"
    assert runtime.job_store is not None
    assert runtime.runner is not None


def test_system_clock_delegates_to_time_module(monkeypatch) -> None:
    sleeps = []
    monkeypatch.setattr(worker_runtime.time, "monotonic", lambda: 12.5)
    monkeypatch.setattr(worker_runtime.time, "sleep", sleeps.append)
    clock = worker_runtime.SystemWorkerClock()

    assert clock.monotonic() == 12.5
    clock.sleep(3)
    assert sleeps == [3]
