"""Fail-closed scheduled host boundaries; no pilot or provider invocation."""

import hashlib
import json
import subprocess
from pathlib import Path
import os
import sys

import pytest

from scripts.ops import query_pilot_scheduled_host as host


def test_packet_rejects_arbitrary_command_before_execution(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_text(json.dumps({"schema": host.SCHEMA, "command": ["cmd", "/c"]}))
    with pytest.raises(host.OperatorStopped, match="scheduled_packet_invalid"):
        host.validate_packet(packet, hashlib.sha256(packet.read_bytes()).hexdigest())


def test_packet_rejects_changed_bytes_before_parsing(tmp_path):
    packet = tmp_path / "packet.json"
    packet.write_text("{}"); digest = hashlib.sha256(packet.read_bytes()).hexdigest()
    packet.write_text('{"token": "never echo this"}')
    with pytest.raises(host.OperatorStopped, match="scheduled_packet_drift"):
        host.validate_packet(packet, digest)


@pytest.mark.parametrize("exit_code,empty,released,expected", [
    (0, True, True, "operator_exited"),
    (7, True, True, "operator_failed"),
    (None, True, True, "operator_timeout"),
    (0, False, True, "owned_job_not_empty"),
    (0, True, False, "runtime_port_not_released"),
])
def test_lifetime_receipt_requires_job_empty_and_port_release(
    tmp_path, exit_code, empty, released, expected,
):
    events = []

    class Job:
        pid = 123

        def __enter__(self):
            return self

        def __exit__(self, *_):
            events.append("closed")

        def wait(self, timeout):
            if exit_code is None:
                raise subprocess.TimeoutExpired("operator", timeout)
            return exit_code

        def terminate(self):
            events.append("terminated")

        def wait_empty(self, timeout):
            events.append("empty_checked")
            return empty

    receipt = host.contain_operator(
        ["bound-python", "-m", "scripts.ops.query_decomposition_pilot_operator"],
        source_root=tmp_path, environment={}, timeout_seconds=1, port=8302,
        job_factory=lambda *args, **kwargs: Job(),
        port_released=lambda port, timeout: released,
    )
    assert receipt["reason"] == expected
    assert receipt["owned_job_empty"] is empty
    assert receipt["runtime_port_released"] is (empty and released)
    assert events == ["terminated", "empty_checked", "closed"]
    assert receipt["retry_authorized"] is False


def test_lifetime_exception_is_sanitized_and_cleanup_still_attempted(tmp_path):
    class Job:
        pid = 1

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def wait(self, timeout):
            raise RuntimeError("provider key must never appear")

        def terminate(self):
            return None

        def wait_empty(self, timeout):
            return True

    result = host.contain_operator(
        [], source_root=tmp_path, environment={}, timeout_seconds=1, port=8302,
        job_factory=lambda *a, **kw: Job(), port_released=lambda *a: True,
    )
    assert result["reason"] == "scheduled_host_failure"
    assert "provider key" not in json.dumps(result)


def test_prepare_rejects_extra_arguments_without_creating_packet(tmp_path):
    target = tmp_path / "packet.json"
    with pytest.raises(host.OperatorStopped, match="scheduled_operator_arguments_invalid"):
        host.prepare_packet({"command": "arbitrary"}, "a" * 40, target)
    assert not target.exists()


def test_run_rejects_bad_packet_without_consuming_root(tmp_path):
    target = tmp_path / "packet.json"
    target.write_text("{}")
    before = set(tmp_path.iterdir())
    with pytest.raises(host.OperatorStopped, match="scheduled_packet_invalid"):
        host.run_packet(target, hashlib.sha256(target.read_bytes()).hexdigest())
    assert set(tmp_path.iterdir()) == before


def test_cleanup_exception_cannot_turn_into_success(tmp_path):
    events = []

    class Job:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            events.append("closed")

        def wait(self, timeout):
            return 0

        def terminate(self):
            events.append("terminate_attempted")
            raise OSError("private error")

    receipt = host.contain_operator(
        [], source_root=tmp_path, environment={}, timeout_seconds=1, port=8302,
        job_factory=lambda *a, **kw: Job(),
    )
    assert events == ["terminate_attempted", "closed"]
    assert receipt["reason"] == "scheduled_host_cleanup_failure"
    assert receipt["owned_job_empty"] is False
    assert receipt["runtime_port_released"] is False


@pytest.mark.parametrize("empty,released,deleted", [(True, True, True), (False, True, False), (True, False, False)])
def test_terminal_capture_cleanup_requires_verified_tree_and_port(tmp_path, empty, released, deleted):
    captures = tmp_path / "review-captures"
    captures.mkdir()
    captured = captures / "one.capture.json"
    captured.write_text("synthetic ciphertext")
    result = host.finalize_operator_outcome({
        "reason": "operator_failed", "owned_job_empty": empty,
        "runtime_port_released": released,
    }, run_root=tmp_path, authorization_sha256="a" * 64)
    assert captured.exists() is not deleted
    assert result["status"] == "terminal_failure"
    assert result["capture_cleanup_status"] == ("completed" if deleted else "deferred_unsafe_runtime")


def test_direct_script_help_does_not_need_ambient_pythonpath(tmp_path):
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(Path(host.__file__).resolve()), "--help"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "prepare" in result.stdout


def test_completed_operator_preserves_capture_for_human_review(tmp_path):
    captures = tmp_path / "review-captures"
    captures.mkdir()
    captured = captures / "one.capture.json"
    captured.write_text("synthetic ciphertext")
    (tmp_path / "result.json").write_text(json.dumps({
        "schema": "query-decomposition-pilot-operator-result-v1",
        "status": "completed", "completed_request_count": 100,
        "provider_retries": 0, "replacement_requests": 0, "catch_up_requests": 0,
    }))
    receipt = host.finalize_operator_outcome({
        "reason": "operator_exited", "owned_job_empty": True,
        "runtime_port_released": True, "pilot_accepted": False,
    }, run_root=tmp_path, authorization_sha256="a" * 64)
    assert receipt["status"] == "completed"
    assert receipt["capture_cleanup_status"] == "preserved_for_review"
    assert receipt["pilot_accepted"] is False
    assert captured.read_text() == "synthetic ciphertext"
    assert not (tmp_path / "capture-deletion.journal.json").exists()


def test_cleanup_journal_drift_preserves_capture_and_reports_failure(tmp_path):
    captures = tmp_path / "review-captures"
    captures.mkdir()
    captured = captures / "one.capture.json"
    captured.write_text("synthetic ciphertext")
    (tmp_path / "capture-deletion.journal.json").write_text("{}")
    receipt = host.finalize_operator_outcome({
        "reason": "operator_failed", "owned_job_empty": True,
        "runtime_port_released": True,
    }, run_root=tmp_path, authorization_sha256="a" * 64)
    assert receipt["status"] == "terminal_failure"
    assert receipt["capture_cleanup_status"] == "failed"
    assert captured.exists()


def test_port_release_wait_observes_delayed_close_without_stopping_listener():
    import socket
    import threading

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert host.wait_port_released(port, 0) is False
        assert listener.fileno() != -1
        closer = threading.Timer(0.1, listener.close)
        closer.start()
        try:
            assert host.wait_port_released(port, 2) is True
        finally:
            closer.join(timeout=2)


def _synthetic_source(tmp_path, transport_stub):
    import shutil
    from tests.unit.test_feature_activation import _clean_git_root

    root = tmp_path / "synthetic-repo"
    root.mkdir()
    _clean_git_root(root)
    source = Path(host.__file__).resolve().parents[2]
    script_dir = root / "scripts" / "ops"
    script_dir.mkdir(parents=True)
    for name in ("query_pilot_scheduled_host.py", "query_pilot_scheduled_task.ps1",
                 "query_pilot_windows_job.py", "query_decomposition_pilot_operator.py"):
        shutil.copyfile(source / "scripts" / "ops" / name, script_dir / name)
    if transport_stub:
        # Only the temporary host's run entry is replaced, BEFORE source freeze.
        # Validation stays real; task handoff must never reach a RAG operator.
        target = script_dir / "query_pilot_scheduled_host.py"
        stub = (source / "tests/fixtures/query_scheduled_transport_stub.py").read_text()
        target.write_text(target.read_text().replace(
            "from __future__ import annotations", "from __future__ import annotations\n" + stub, 1))
    subprocess.run(["git", "add", "scripts"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "synthetic launcher source"], cwd=root, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return root, source, commit


def _synthetic_authorization(root, source, commit):
    from datetime import datetime, timedelta, timezone
    import shutil
    from tests.unit.test_feature_activation import _controlled_query_evidence
    from scripts.ops.query_controlled_demo_activation import prepare_activation_draft
    from scripts.ops.query_decomposition_pilot_launch import (
        CONSOLIDATED_AUTHORIZATION, prepare_consolidated_launch, finalize_consolidated_launch,
    )

    _controlled_query_evidence(root, evidence_source_commit=commit)
    manifest = root / "data" / "pilot-manifest.jsonl"
    shutil.copyfile(source / "data/decomposition_eval_v1/eval_manifest.jsonl", manifest)
    evidence = root / ".local/query-window"
    activation = root / ".local/activation-draft.json"
    prepare_activation_draft(
        source_root=root, source_commit=commit, evidence_root=root,
        owner_decision=evidence / "query-owner-decision.json",
        owner_decision_finalization=evidence / "owner-decision-finalization.json",
        output=activation, owner="bao.nguyen",
    )
    launch = root / ".local/new-launch/consolidated"
    prepare_consolidated_launch(source_root=root, source_commit=commit,
        activation_draft_path=activation, manifest_path=manifest, output_dir=launch,
        owner="bao.nguyen")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    draft = launch / "consolidated-launch-draft.json"
    approval = launch / "synthetic-approval.json"
    approval.write_text(json.dumps({
        "schema": "query-decomposition-consolidated-launch-approval-v1",
        "draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "actor": "bao.nguyen", "authorized_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=26)).isoformat(),
        "authorization": CONSOLIDATED_AUTHORIZATION,
    }))
    materialized = launch / "materialized"
    result = finalize_consolidated_launch(draft_path=draft, approval_path=approval,
                                         output_dir=materialized, now=now)
    return result, materialized, manifest


@pytest.fixture
def synthetic_host_packet(tmp_path, request):
    """Synthetic evidence stays in a temporary repo; no validator is replaced."""
    import importlib.util

    root, source, commit = _synthetic_source(
        tmp_path, getattr(request, "param", None) == "scheduled_transport")
    result, materialized, manifest = _synthetic_authorization(root, source, commit)
    auth_path = Path(result["pilot_authorization"]["path"])
    auth = json.loads(auth_path.read_bytes())
    schedule = materialized / "authorized/schedule.json"
    bundle = materialized / "activation/query-controlled-demo-bundle.json"
    run_root = root / auth["pilot_run_root"]
    paths = host.pilot_run_paths(run_root)
    values = {"source_root": str(root), "python_exe": sys.executable,
        "schedule": str(schedule), "authorization": str(auth_path),
        "bundle": str(bundle), "manifest": str(manifest),
        "authorization_sha256": result["pilot_authorization"]["sha256"],
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "snapshot_fingerprint": "a" * 64, "deployment_id": "synthetic",
        "qdrant_collection": "synthetic", "sql_database": "synthetic", "port": 8302}
    for name, key in {"trace":"trace", "wal":"wal", "claims":"claims",
        "result":"result", "terminal":"terminal", "runtime_state":"runtime_state",
        "runtime_stop":"runtime_stop", "runtime_out_log":"runtime_out",
        "runtime_err_log":"runtime_err", "frozen_health_output":"frozen_health"}.items():
        values[name] = str(paths[key])
    spec = importlib.util.spec_from_file_location("scripts.ops.synthetic_scheduled_host", root / "scripts/ops/query_pilot_scheduled_host.py")
    copied_host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(copied_host)
    packet = materialized.parent / "host-packet.json"
    prepared = copied_host.prepare_packet(values, commit, packet)
    return copied_host, packet, prepared, values, run_root, commit


def test_host_process_identity_failure_is_not_a_zero_timestamp(monkeypatch):
    import psutil

    def unavailable(*args):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "Process", unavailable)
    with pytest.raises(host.OperatorStopped, match="scheduled_host_identity_unavailable"):
        host._process_create_time()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process boundary")
def test_prepare_and_validate_real_synthetic_authorization_chain(synthetic_host_packet, capsys):
    copied_host, packet, prepared, values, run_root, commit = synthetic_host_packet
    root = Path(values["source_root"])
    script_dir = root / "scripts/ops"
    source = Path(host.__file__).resolve().parents[2]
    validated, _, _, resolved = copied_host.validate_packet(packet, prepared["packet_sha256"])
    assert resolved == run_root
    assert validated["source_commit"] == commit
    assert prepared["runtime_started"] is False
    assert not run_root.exists()
    capsys.readouterr()
    assert copied_host.main(["validate", "--packet", str(packet),
                             "--packet-sha256", prepared["packet_sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "validated"
    second_packet = packet.with_name("second-preparation.json")
    assert copied_host.main(["prepare", "--packet", str(second_packet),
        "--source-commit", commit, "--", *copied_host._arguments(values)]) == 0
    assert json.loads(capsys.readouterr().out)["runtime_started"] is False
    assert copied_host.main(["validate", "--packet", str(packet),
                             "--packet-sha256", "0" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "scheduled_host_rejected"
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    inspected = subprocess.run([
        str(powershell), "-NoProfile", "-NonInteractive", "-File",
        str(script_dir / "query_pilot_scheduled_task.ps1"), "-Packet", str(packet),
        "-PacketSha256", prepared["packet_sha256"],
    ], cwd=root, env={**os.environ, "PYTHONPATH": os.pathsep.join((str(source), str(source / "src")))},
       capture_output=True, text=True, timeout=30)
    assert inspected.returncode == 0, inspected.stdout + inspected.stderr
    assert json.loads(inspected.stdout)["task_registered"] is False
    assert not run_root.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process boundary")
def test_synthetic_packet_rejects_drift_and_consumed_roots(synthetic_host_packet):
    copied_host, packet, prepared, _, run_root, _ = synthetic_host_packet
    validated = json.loads(packet.read_bytes())
    original = packet.read_bytes()
    for field in ("interpreter_sha256", "host_sha256", "task_script_sha256", "job_sha256"):
        changed = {**validated, field: "0" * 64}
        packet.write_text(json.dumps(changed))
        with pytest.raises(host.OperatorStopped, match="scheduled_executable_drift"):
            copied_host.validate_packet(packet, hashlib.sha256(packet.read_bytes()).hexdigest())
    packet.write_bytes(original)
    host_root = run_root.with_name(run_root.name + "-scheduled-host")
    host_root.mkdir()
    with pytest.raises(host.OperatorStopped, match="scheduled_host_already_consumed"):
        copied_host.validate_packet(packet, prepared["packet_sha256"])
    host_root.rmdir()
    run_root.mkdir()
    with pytest.raises(host.OperatorStopped, match="scheduled_run_root_not_fresh"):
        copied_host.validate_packet(packet, prepared["packet_sha256"])
    assert not (run_root / "pilot.wal.jsonl").exists()
    run_root.rmdir()


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("RUN_QUERY_TASK_PROOF") != "1",
                    reason="Explicit opt-in for temporary Scheduled Task registration")
@pytest.mark.parametrize("synthetic_host_packet", ["scheduled_transport"], indirect=True)
def test_real_wrapper_registers_exact_synthetic_task(synthetic_host_packet):
    from xml.etree import ElementTree

    _, packet, prepared, values, run_root, _ = synthetic_host_packet
    source = Path(host.__file__).resolve().parents[2]
    root = Path(values["source_root"])
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-File",
        str(source / "tests/fixtures/query_scheduled_registration_proof.ps1"),
        "-Wrapper", str(root / "scripts/ops/query_pilot_scheduled_task.ps1"),
        "-Packet", str(packet), "-PacketSha256", prepared["packet_sha256"], "-Start"],
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(source), str(source / "src")))},
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    xml = ElementTree.fromstring(proof["xml"])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    value = lambda path: xml.findtext(path, namespaces=ns)
    assert len(xml.find("t:Triggers", ns)) == 0
    assert value("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert proof["run_level"] == "Limited"
    assert value("t:Principals/t:Principal/t:RunLevel") in (None, "LeastPrivilege")
    assert value("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert value("t:Settings/t:RestartOnFailure/t:Count") in (None, "0")
    assert proof["restart_count"] == 0
    assert value("t:Settings/t:ExecutionTimeLimit") in ("PT26H", "P1DT2H")
    assert value("t:Actions/t:Exec/t:Command") == str(Path(sys.executable).resolve())
    assert value("t:Actions/t:Exec/t:WorkingDirectory") == str(root)
    expected = (f'"{root / "scripts/ops/query_pilot_scheduled_host.py"}" run '
                f'--packet "{packet}" --packet-sha256 {prepared["packet_sha256"]}')
    assert value("t:Actions/t:Exec/t:Arguments") == expected
    assert "RAG_SERVICE_TOKEN" not in proof["xml"]
    assert not os.environ.get("RAG_SERVICE_TOKEN") or os.environ["RAG_SERVICE_TOKEN"] not in proof["xml"]
    assert proof["task_started"] is True
    assert proof["task_result"] == 0
    marker = json.loads((root / ".local/scheduled-handoff.json").read_bytes())
    assert marker == {"packet": str(packet), "packet_sha256": prepared["packet_sha256"],
                      "cwd": str(root), "provider_calls": 0, "pilot_dispatches": 0}
    assert not run_root.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process boundary")
@pytest.mark.parametrize("operator_exit", [0, 7])
@pytest.mark.parametrize("token_source", ["environment", "settings"])
def test_synthetic_packet_run_outcome(synthetic_host_packet, monkeypatch, operator_exit, token_source):
    copied_host, packet, prepared, _, run_root, _ = synthetic_host_packet
    host_root = run_root.with_name(run_root.name + "-scheduled-host")
    import win32process

    create_process = win32process.CreateProcess
    launched = []

    def fake_operator_process(executable, command_line, process_security,
                              thread_security, inherit, flags, environment,
                              cwd, startup):
        assert "scripts.ops.query_decomposition_pilot_operator" in command_line
        assert environment["RAG_QUERY_PILOT_HOST_CONTAINED"] == "1"
        assert environment["RAG_SERVICE_TOKEN"] == "synthetic-test-token"
        launched.append(command_line)
        completed = {"schema": "query-decomposition-pilot-operator-result-v1",
            "status": "completed", "completed_request_count": 100,
            "provider_retries": 0, "replacement_requests": 0, "catch_up_requests": 0}
        code = (
            "import pathlib,sys; p=pathlib.Path(%r); p.mkdir(); "
            "c=p/'review-captures'; c.mkdir(); "
            "(c/'one.capture.json').write_text('synthetic ciphertext'); "
            "(p/'result.json').write_text(%r); sys.exit(%d)"
        ) % (str(run_root), json.dumps(completed), operator_exit)
        return create_process(executable, subprocess.list2cmdline([executable, "-c", code]),
            process_security, thread_security, inherit, flags, environment, cwd, startup)

    monkeypatch.setattr(win32process, "CreateProcess", fake_operator_process)
    if token_source == "environment":
        monkeypatch.setenv("RAG_SERVICE_TOKEN", "synthetic-test-token")
    else:
        from types import SimpleNamespace
        from mech_chatbot.config import settings
        monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)
        monkeypatch.setattr(settings, "load_settings", lambda: SimpleNamespace(
            RAG_SERVICE_TOKEN="synthetic-test-token"))
    outcome = copied_host.run_packet(packet, prepared["packet_sha256"])
    assert len(launched) == 1
    assert outcome["status"] == ("completed" if operator_exit == 0 else "terminal_failure")
    assert outcome["owned_job_empty"] is True
    assert outcome["runtime_port_released"] is True
    assert (run_root / "review-captures/one.capture.json").exists() is (operator_exit == 0)
    assert json.loads((host_root / "receipt.json").read_bytes()) == outcome
    assert "synthetic-test-token" not in (host_root / "receipt.json").read_text()
    with pytest.raises(host.OperatorStopped):
        copied_host.run_packet(packet, prepared["packet_sha256"])
    assert len(launched) == 1


@pytest.mark.parametrize("token", ["synthetic-settings-token", ""])
def test_host_resolves_settings_before_consumption(tmp_path, monkeypatch, token):
    from types import SimpleNamespace
    from mech_chatbot.config import settings

    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(settings, "load_settings", lambda: SimpleNamespace(RAG_SERVICE_TOKEN=token))
    run_root = tmp_path / "run"
    args = SimpleNamespace(python_exe=Path(sys.executable), port=8302)
    monkeypatch.setattr(host, "validate_packet", lambda *a: ({}, args, {}, run_root))
    monkeypatch.setattr(host, "wait_port_released", lambda *a: False)
    expected = "scheduled_runtime_port_occupied" if token else "scheduled_service_token_missing"
    with pytest.raises(host.OperatorStopped, match=expected):
        host.run_packet(tmp_path / "packet.json", "a" * 64)
    assert "RAG_SERVICE_TOKEN" not in os.environ
    assert not host._host_root(run_root).exists()
