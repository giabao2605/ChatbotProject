import json
import hashlib
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from scripts.eval.run_eval import load_manifest_files, run_evaluation
from scripts.integrated_eval.math_query_quality import QualityObservationLedger, validate_frozen_preflight
from tests.unit.test_crag_eval_harness import _case
from mech_chatbot.rag.execution import RagCompleted, RagDiagnostics, RagPrepared, RagToken


@pytest.mark.parametrize("managed_runtime,observer_fails", [
    (False, False), (False, True), (True, False), (True, True),
    ("reload_drift", False), ("worker", False), ("worker_reload_drift", False),
    ("worker_transport_failure", False),
])
def test_observer_receives_actual_request_and_failure_stops_before_next_case(
    tmp_path, monkeypatch, observer_fails, managed_runtime
):
    is_worker = managed_runtime in ("worker", "worker_reload_drift", "worker_transport_failure")
    transport_failure = managed_runtime == "worker_transport_failure"
    reload_drift = managed_runtime in ("reload_drift", "worker_reload_drift")
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text("\n".join(json.dumps(_case(id=f"case-{i}", expected_document="target.md",
                                                  expected_sources=["target.md"]))
                                   for i in (1, 2)) + "\n", encoding="utf-8")
    calls, receipts = [], []
    ledger = QualityObservationLedger.create(load_manifest_files([manifest]), label="candidate")
    diagnostics = RagDiagnostics.from_mapping({
        "retrieved_docs": [{"file_goc": "target.md", "source_id": "D41P1"}],
        "generation_metrics": {}})

    class FakeExecutor:
        def run(self, request, invocation, cancellation=None):
            calls.append(invocation.trace_id)
            if transport_failure:
                raise TimeoutError("synthetic transport timeout")
            yield RagPrepared("", (), (), diagnostics)
            yield RagToken("Cau tra loi co can cu")
            yield RagCompleted("answered", invocation.trace_id, diagnostics)

    def observe(case, reported, observation):
        nonlocal ledger
        ledger = ledger.record(case, reported, observation)
        receipts.append(ledger.summary()["cases"][-1])
        reported["id"] = "observer-mutation"
        if observer_fails:
            raise ValueError("private observation content")

    kwargs = {"preflight": False, "rag_executor": FakeExecutor(),
              "intent_extractor": lambda *a, **kw: (None, None, None, None, None, {"version_policy": "current_only"}),
              "number_normalizer": lambda value: set(), "quality_observer": observe}
    output = tmp_path / "output"
    closed = []
    preflight_checks = []
    frozen_cases = load_manifest_files([manifest])
    frozen_preflight = {"passed": True}
    if is_worker:
        frozen_preflight = {**frozen_preflight, "case_resolutions": {
            case["id"]: {"expected_branches": []} for case in frozen_cases}}

    def execute():
        if not managed_runtime:
            return run_evaluation([manifest], output, "candidate", **kwargs)
        from scripts.eval import run_eval as runner
        from mech_chatbot.config import logging as logging_config
        from mech_chatbot.rag import intent

        monkeypatch.setattr(runner, "load_settings", lambda: object())
        monkeypatch.setattr(runner, "configured_repository_runtime", lambda *a, **kw: nullcontext())
        monkeypatch.setattr(runner, "_default_preflight_runner", lambda: lambda cases: frozen_preflight)
        monkeypatch.setattr(logging_config.LoggingConfig, "from_settings", lambda _: object())
        monkeypatch.setattr(logging_config, "configure_logging", lambda _: None)
        monkeypatch.setattr(logging_config, "bind_trace_runtime", lambda _: nullcontext())
        monkeypatch.setattr(intent, "extract_search_intent", kwargs["intent_extractor"])

        def build_runtime(settings, *, provider_retry_limit):
            assert provider_retry_limit == 0
            if reload_drift:
                manifest.write_text("\n".join(json.dumps({**case, "id": case["id"] + "-drift"})
                                              for case in frozen_cases) + "\n", encoding="utf-8")
            return SimpleNamespace(executor=kwargs["rag_executor"], trace_runtime=None,
                                   close=lambda: closed.append(True))

        monkeypatch.setattr(runner, "build_rag_runtime", build_runtime)

        if is_worker:
            from scripts.integrated_eval.math_query_worker import evaluate_quality_arm

            result = evaluate_quality_arm(
                [manifest], output, "candidate", expected_cases=frozen_cases,
                expected_preflight=frozen_preflight,
            )
            assert result["quality"]["reported_cases_bound"] is True
            assert result["quality"]["observation_coverage_complete"] is True
            assert result["matrix_accepted"] is False
            assert result["eval_sha256"] == hashlib.sha256((output / "candidate/eval.json").read_bytes()).hexdigest()
            return json.loads((output / "candidate/eval.json").read_text(encoding="utf-8")), result["exit_code"] == 0

        def validate_preflight(cases, preflight):
            accepted = validate_frozen_preflight(
                cases, preflight, expected_cases=frozen_cases, expected_preflight={"passed": True})
            preflight_checks.append(True)
            cases.clear()
            preflight["passed"] = False
            return accepted

        exit_code = runner.main([
            "--manifest", str(manifest), "--output-dir", str(output),
            "--run-label", "candidate", "--maximum-provider-retries", "0",
            "--stop-on-provider-failure",
        ], quality_observer=observe, preflight_validator=validate_preflight)
        return json.loads((output / "candidate/eval.json").read_text(encoding="utf-8")), exit_code == 0

    if transport_failure:
        with pytest.raises(RuntimeError, match="^quality_observation_unavailable$"):
            execute()
        assert calls == ["eval:candidate:case-1"]
        assert not (output / "candidate/eval.json").exists()
    elif reload_drift:
        with pytest.raises(RuntimeError, match="^evaluation_preflight_validation_failed$"):
            execute()
        assert calls == []
        assert receipts == []
        assert not (output / "candidate/eval.json").exists()
    elif observer_fails:
        with pytest.raises(RuntimeError, match="^quality_observer_failed$"):
            execute()
        assert calls == ["eval:candidate:case-1"]
        assert not (output / "candidate/eval.json").exists()
    else:
        report, passed = execute()
        assert passed is True
        assert [case["id"] for case in report["cases"]] == ["case-1", "case-2"]
        if not is_worker:
            assert len(receipts) == 2
            assert ledger.finalize(report["cases"])["reported_cases_bound"] is True
    assert ledger.summary()["observation_coverage_complete"] is (
        not observer_fails and not reload_drift and not is_worker)
    assert all(receipt["recomputed_matches"] for receipt in receipts)
    assert not list(output.rglob("review-content.jsonl"))
    assert closed == ([True] if managed_runtime else [])
    assert preflight_checks == ([True, True] if managed_runtime and not is_worker else [])


def test_invalid_observer_is_rejected_before_reading_manifest_or_creating_output(tmp_path):
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="quality_observer_must_be_callable"):
        run_evaluation([tmp_path / "missing.jsonl"], output, "candidate", quality_observer=True)
    assert not output.exists()


@pytest.mark.parametrize("resolution", [{"other": {}}, {"case-1": {"id": "changed"}},
                                      {"case-1": {"user_roles": ["admin"]}}, []])
def test_worker_rejects_unexpected_resolution_fields_before_loading_runtime(tmp_path, resolution):
    from scripts.integrated_eval.math_query_worker import evaluate_quality_arm

    output = tmp_path / "output"
    with pytest.raises(ValueError, match="invalid frozen quality resolutions"):
        evaluate_quality_arm([tmp_path / "missing.jsonl"], output, "candidate",
                             expected_cases=[_case(id="case-1")],
                             expected_preflight={"passed": True, "case_resolutions": resolution})
    assert not output.exists()


def test_worker_rejects_failed_frozen_preflight_before_loading_runtime(tmp_path):
    from scripts.integrated_eval.math_query_worker import evaluate_quality_arm

    output = tmp_path / "output"
    with pytest.raises(ValueError, match="invalid frozen quality preflight"):
        evaluate_quality_arm([tmp_path / "missing.jsonl"], output, "candidate",
                             expected_cases=[_case(id="case-1")],
                             expected_preflight={"passed": False})
    assert not output.exists()


def test_request_failure_with_observer_stops_before_second_request(tmp_path):
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text("\n".join(json.dumps(_case(id=f"case-{i}")) for i in (1, 2)) + "\n",
                        encoding="utf-8")
    calls, observed = [], []

    class FailedExecutor:
        def run(self, request, invocation, cancellation=None):
            calls.append(invocation.trace_id)
            raise RuntimeError("synthetic request failure")

    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="^quality_observation_unavailable$"):
        run_evaluation([manifest], output, "candidate", preflight=False,
                       rag_executor=FailedExecutor(),
                       intent_extractor=lambda *a, **kw: (None, None, None, None, None, {}),
                       number_normalizer=lambda value: set(),
                       quality_observer=lambda *values: observed.append(values))
    assert calls == ["eval:candidate:case-1"]
    assert observed == []
    assert not (output / "candidate/eval.json").exists()
