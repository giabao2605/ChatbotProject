"""Real child process, invalid preflight: no runtime or provider creation."""
import os
from pathlib import Path
import sys
import subprocess
import json
import hashlib

import pytest


@pytest.mark.parametrize("fail_request,negative_quality", [(False, False), (True, False), (False, True)])
def test_production_bootstrap_runs_real_worker_with_offline_runtime(tmp_path, monkeypatch, fail_request, negative_quality):
    from scripts.integrated_eval.math_query_worker import run_quality_arm_process
    from scripts.eval.run_eval import load_manifest_files
    from tests.unit.test_crag_eval_harness import _case

    original_root = Path(__file__).resolve().parents[2]
    root = tmp_path / "source"
    for folder in ("scripts", "src"):
        for original in (original_root / folder).rglob("*.py"):
            target = root / original.relative_to(original_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
    # Only the temporary package initializer replaces external runtime seams.
    # Worker, evaluator and process bootstrap remain production source bytes.
    (root / "scripts/eval/__init__.py").write_text('''
import json, os, sys
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
from scripts.eval import run_eval as runner
from mech_chatbot.config import logging as logging_config
from mech_chatbot.rag import intent
from mech_chatbot.rag.execution import RagCompleted, RagDiagnostics, RagPrepared, RagToken

def deny_network(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.bind"):
        raise AssertionError("network forbidden in offline fixture")
sys.addaudithook(deny_network)
marker = Path(os.environ["MATRIX_TEST_MARKER"])
def record(value):
    with marker.open("a", encoding="utf-8") as stream:
        stream.write(value + "\\n")
class FakeExecutor:
    def run(self, request, invocation, cancellation=None):
        record(invocation.trace_id)
        if os.environ["MATRIX_TEST_FAIL"] == "true":
            raise TimeoutError("synthetic offline failure")
        diagnostics = RagDiagnostics.from_mapping({
            "retrieved_docs": [{"file_goc": "target.md", "source_id": "D41P1"}],
            "generation_metrics": {}})
        yield RagPrepared("", (), (), diagnostics)
        yield RagToken("Cau tra loi co can cu")
        yield RagCompleted("answered", invocation.trace_id, diagnostics)
def runtime(settings, *, provider_retry_limit):
    assert provider_retry_limit == 0
    return SimpleNamespace(executor=FakeExecutor(), trace_runtime=None,
                           close=lambda: record("closed"))
runner.load_settings = lambda: object()
runner.configured_repository_runtime = lambda *a, **kw: nullcontext()
runner._default_preflight_runner = lambda: lambda cases: {"passed": True}
runner.build_rag_runtime = runtime
logging_config.LoggingConfig.from_settings = lambda _: object()
logging_config.configure_logging = lambda _: None
logging_config.bind_trace_runtime = lambda _: nullcontext()
intent.extract_search_intent = lambda *a, **kw: (None, None, None, None, None, {"version_policy": "current_only"})
''', encoding="utf-8")
    cases = [_case(id=f"case-{index}", expected_document="target.md",
                   expected_sources=["target.md"]) for index in (1, 2)]
    if negative_quality:
        expected = json.loads((original_root / "data/grounded_math_eval_v1/eval_manifest.jsonl")
                              .read_text(encoding="utf-8").splitlines()[0])["expected_calculation"]
        cases = [{**case, "expected_calculation": expected} for case in cases]
    manifest = tmp_path / "cases.jsonl"
    manifest.write_text("\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8")
    marker = tmp_path / "lifecycle.txt"
    environment = {key: os.environ[key] for key in ("SystemRoot", "TEMP", "TMP") if key in os.environ}
    environment = {**environment, "MATRIX_TEST_MARKER": str(marker),
                   "MATRIX_TEST_FAIL": str(fail_request).lower(), "RAG_EXECUTION_CONTEXT": "test"}
    output = tmp_path / "output"
    arguments = dict(source_root=root, python=Path(sys.executable), environment=environment,
        manifest_paths=[manifest], output_dir=output, label="candidate", expected_cases=load_manifest_files([manifest]),
        expected_preflight={"passed": True}, timeout_seconds=45)
    completed = []
    real_run = subprocess.run

    def capture_child(*args, **kwargs):
        result = real_run(*args, **kwargs)
        completed.append(result)
        return result

    monkeypatch.setattr(subprocess, "run", capture_child)
    if fail_request:
        with pytest.raises(RuntimeError, match="^matrix_worker_process_failed$"):
            run_quality_arm_process(**arguments)
        assert marker.exists(), completed[0].stderr
        assert marker.read_text().splitlines() == ["eval:candidate:case-1", "closed"]
        assert not (output / "candidate/eval.json").exists()
    else:
        try:
            result = run_quality_arm_process(**arguments)
        except RuntimeError:
            pytest.fail(completed[0].stderr)
        assert result["exit_code"] == (2 if negative_quality else 0)
        from scripts.integrated_eval.math_query_dispatch import validate_matrix_worker_result
        validate_matrix_worker_result(result)
        if negative_quality:
            assert result["quality"]["all_applicable_quality_passed"] is False
        assert result["quality"]["reported_cases_bound"] is True
        assert result["quality"]["observation_coverage_complete"] is True
        assert result["eval_sha256"] == hashlib.sha256((output / "candidate/eval.json").read_bytes()).hexdigest()
        assert result["matrix_accepted"] is False
        assert result["dispatch_authorized"] is False
        assert marker.read_text().splitlines() == ["eval:candidate:case-1", "eval:candidate:case-2", "closed"]
    assert not list(output.rglob("review-content.jsonl"))


def test_real_quality_worker_and_evaluator_in_isolated_python(tmp_path):
    root = Path(__file__).resolve().parents[2]
    environment = {key: os.environ[key] for key in ("SystemRoot", "TEMP", "TMP") if key in os.environ}
    environment = {**environment, "RUN_DB_TESTS": "0", "RUN_QDRANT_TESTS": "0",
                   "RUN_EVAL_TESTS": "0", "RUN_QUERY_TASK_PROOF": "0",
                   "RAG_EXECUTION_CONTEXT": "test"}
    code = '''
import sys
from pathlib import Path
root = Path.cwd()
sys.path[:0] = [str(root), str(root / "src")]
import pytest
raise SystemExit(pytest.main([
    "tests/unit/test_eval_quality_observer.py::test_observer_receives_actual_request_and_failure_stops_before_next_case[worker-False]",
    "-p", "no:cacheprovider", "-o", "addopts=", "-q", "--tb=short",
    "--basetemp", sys.argv[1],
]))
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(tmp_path / "child-tests")],
        cwd=root, env=environment, capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_worker_process_rejects_invalid_preflight_without_creating_output(tmp_path):
    from scripts.integrated_eval.math_query_worker import run_quality_arm_process

    real = Path(__file__).resolve().parents[2]
    root = tmp_path / "isolated-source"
    for folder in ("scripts", "src"):
        for original in (real / folder).rglob("*.py"):
            target = root / original.relative_to(real)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
    output = tmp_path / "output"
    environment = {key: os.environ[key] for key in ("SystemRoot", "TEMP", "TMP") if key in os.environ}
    with pytest.raises(RuntimeError, match="^matrix_worker_process_failed$"):
        run_quality_arm_process(
            source_root=root, python=Path(sys.executable), environment=environment,
            manifest_paths=[tmp_path / "absent.jsonl"], output_dir=output, label="baseline",
            expected_cases=[{"id": "synthetic-case"}], expected_preflight={"passed": False},
            timeout_seconds=30)
    assert not output.exists()


def test_process_transport_rejects_dotenv_before_launch(tmp_path):
    from scripts.integrated_eval.math_query_worker import run_quality_arm_process

    root = tmp_path / "source"
    root.mkdir()
    (root / ".env").write_text("GPT_MODEL_NAME=undeclared-model\n", encoding="utf-8")
    with pytest.raises(ValueError, match="^matrix_worker_dotenv_forbidden$"):
        run_quality_arm_process(source_root=root, python=tmp_path / "nonexistent-python",
            environment={}, manifest_paths=[], output_dir=tmp_path / "output", label="baseline",
            expected_cases=[], expected_preflight={}, timeout_seconds=10)


@pytest.mark.parametrize("timeout", [False, True])
def test_explicit_process_transport_isolates_environment_and_bounds_lifetime(tmp_path, monkeypatch, timeout):
    from scripts.integrated_eval.math_query_worker import run_quality_arm_process

    root = tmp_path / "synthetic-source"
    package = root / "scripts/integrated_eval"
    package.mkdir(parents=True)
    (root / "scripts/__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "math_query_worker.py").write_text('''
import os, time
def evaluate_quality_arm(**packet):
    assert os.environ.get("MATRIX_AMBIENT_TEST_SECRET") is None
    assert os.environ["MATRIX_EXPLICIT_SETTING"] == "frozen"
    assert packet["label"] == "candidate"
    assert packet["expected_cases"] == [{"id": "case-1"}]
    assert packet["expected_preflight"] == {"passed": True}
    if os.environ.get("MATRIX_TEST_TIMEOUT") == "true":
        time.sleep(30)
    print("synthetic-private-log-not-protocol")
    return {"schema": "math-query-quality-worker-result-v1", "exit_code": 0,
            "quality": {"reported_cases_bound": True}}
''', encoding="utf-8")
    monkeypatch.setenv("MATRIX_AMBIENT_TEST_SECRET", "must-not-inherit")
    environment = {key: os.environ[key] for key in ("SystemRoot", "TEMP", "TMP") if key in os.environ}
    environment = {**environment, "MATRIX_EXPLICIT_SETTING": "frozen",
                   "MATRIX_TEST_TIMEOUT": str(timeout).lower()}
    args = dict(source_root=root, python=Path(sys.executable), environment=environment,
                manifest_paths=[tmp_path / "unused.jsonl"], output_dir=tmp_path / "output",
                label="candidate", expected_cases=[{"id": "case-1"}],
                expected_preflight={"passed": True}, timeout_seconds=0.5 if timeout else 20)
    if timeout:
        with pytest.raises(RuntimeError, match="^matrix_worker_process_failed$"):
            run_quality_arm_process(**args)
    else:
        assert run_quality_arm_process(**args) == {
            "schema": "math-query-quality-worker-result-v1", "exit_code": 0,
            "quality": {"reported_cases_bound": True}}
