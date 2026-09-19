"""CLI cleanup delegation must be bound to OS-owned containment."""

import json
from pathlib import Path
import pytest
import sys
from types import SimpleNamespace

from scripts.ops import query_decomposition_pilot_operator_cli as cli
from tests.unit.test_query_pilot_scheduled_host import synthetic_host_packet


def _argv(root: Path) -> list[str]:
    local = root / ".local" / "run"
    local.mkdir(parents=True)
    return [
        "--source-root", str(root),
        "--schedule", str(root / "schedule.json"),
        "--authorization", str(root / "authorization.json"),
        "--bundle", str(root / "bundle.json"),
        "--manifest", str(root / "manifest.jsonl"),
        "--trace", str(local / "trace.jsonl"),
        "--wal", str(local / "pilot.wal.jsonl"),
        "--claims", str(local / "claims"),
        "--result", str(local / "result.json"),
        "--terminal", str(local / "terminal.json"),
        "--python-exe", str(root / "python.exe"),
        "--frozen-health-output", str(local / "frozen-health.json"),
        "--runtime-state", str(local / "runtime-state.json"),
        "--runtime-stop", str(local / "runtime-stop.json"),
        "--runtime-out-log", str(local / "runtime.out.log"),
        "--runtime-err-log", str(local / "runtime.err.log"),
        "--authorization-sha256", "a" * 64,
        "--bundle-sha256", "b" * 64,
        "--manifest-sha256", "c" * 64,
        "--snapshot-fingerprint", "d" * 64,
        "--deployment-id", "synthetic",
        "--qdrant-collection", "synthetic",
        "--sql-database", "synthetic",
        "--port", "8302",
    ]


def _write_host_consumed_marker(root: Path) -> None:
    state_root = root / ".local" / "run-scheduled-host"
    state_root.mkdir(parents=True)
    (state_root / "consumed.json").write_text(json.dumps({
        "schema": "query-pilot-scheduled-host-consumed-v1",
        "packet_sha256": "e" * 64,
        "source_commit": "f" * 40,
        "retry_authorized": False,
        "catch_up_authorized": False,
    }))


def _run_and_capture_defer_flag(root: Path, monkeypatch) -> bool:
    from scripts.ops import query_decomposition_pilot_operator as operator

    observed = {}

    def supervise_pilot(**kwargs):
        observed.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(operator, "supervise_pilot", supervise_pilot)

    assert cli.operator_main(_argv(root)) == 0
    return observed["defer_capture_cleanup"]


def test_cli_uses_runtime_settings_token_without_mutating_environment(tmp_path, monkeypatch):
    import os
    from mech_chatbot.config import settings
    from scripts.ops import query_decomposition_pilot_operator as operator

    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(settings, "load_settings", lambda: SimpleNamespace(
        RAG_SERVICE_TOKEN="synthetic-settings-token"))
    observed = {}
    monkeypatch.setattr(operator, "supervise_pilot", lambda **kwargs:
                        observed.update(kwargs) or {"status": "completed"})
    assert cli.operator_main(_argv(tmp_path)) == 0
    assert observed["service_token"] == "synthetic-settings-token"
    assert "RAG_SERVICE_TOKEN" not in os.environ
    assert "synthetic-settings-token" not in (tmp_path / ".local/run/result.json").read_text()


def test_cli_env_marker_alone_cannot_defer_terminal_capture_cleanup(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("RAG_QUERY_PILOT_HOST_CONTAINED", "1")

    assert _run_and_capture_defer_flag(tmp_path, monkeypatch) is False


def test_cli_defer_cleanup_requires_env_marker_and_os_job_membership(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(cli, "_current_process_in_windows_job", lambda: True)
    monkeypatch.setenv("RAG_QUERY_PILOT_HOST_CONTAINED", "1")
    _write_host_consumed_marker(tmp_path)

    assert _run_and_capture_defer_flag(tmp_path, monkeypatch) is False


def test_cli_os_job_membership_without_marker_does_not_defer_cleanup(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(cli, "_current_process_in_windows_job", lambda: True)
    monkeypatch.delenv("RAG_QUERY_PILOT_HOST_CONTAINED", raising=False)
    _write_host_consumed_marker(tmp_path)

    assert _run_and_capture_defer_flag(tmp_path, monkeypatch) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows host fixture")
@pytest.mark.parametrize("drift", [None, "host_sha256", "job_sha256", "source_commit"])
def test_cleanup_binding_checks_frozen_packet(synthetic_host_packet, monkeypatch, drift):
    host, path, prepared, values, run_root, _ = synthetic_host_packet
    packet = json.loads(path.read_bytes())
    if drift:
        packet = {**packet, drift: "0" * len(packet[drift])}
        path.write_text(json.dumps(packet))
    digest = cli.hashlib.sha256(path.read_bytes()).hexdigest()
    args = cli._parser().parse_args(host._arguments(values))
    operator_sha = cli._operator_arguments_sha256(values)
    state = run_root.with_name(run_root.name + "-scheduled-host")
    state.mkdir()
    (state / "consumed.json").write_text(json.dumps({
        "schema": "query-pilot-scheduled-host-consumed-v1",
        "packet_path": str(path), "packet_sha256": digest,
        "operator_arguments_sha256": operator_sha, "source_commit": packet["source_commit"],
        "host_pid": 123, "host_create_time": 456.0,
        "retry_authorized": False, "catch_up_authorized": False,
    }))
    for key, value in {"CONTAINED": "1", "PACKET": str(path), "PACKET_SHA256": digest,
        "OPERATOR_SHA256": operator_sha, "PID": "123", "CREATE_TIME": "456.0",
        "SCRIPT": str(Path(values["source_root"]) / "scripts/ops/query_pilot_scheduled_host.py")}.items():
        monkeypatch.setenv("RAG_QUERY_PILOT_HOST_" + key, value)
    monkeypatch.setattr(cli, "_current_process_in_windows_job", lambda: True)
    monkeypatch.setattr(cli, "_parent_process_matches", lambda **kwargs: True)
    assert cli._defer_capture_cleanup_authorized(args, Path(values["source_root"])) is (drift is None)


@pytest.mark.parametrize("mismatch", [None, "pid", "time", "command", "exe", "absent"])
def test_host_parent_matching_handles_windows_venv_redirector(tmp_path, monkeypatch, mismatch):
    import psutil

    host_path, packet_path = tmp_path / "host.py", tmp_path / "packet.json"
    actual_exe = str(tmp_path / "base-python.exe")
    host = SimpleNamespace(pid=124 if mismatch == "pid" else 123,
        create_time=lambda: 457.0 if mismatch == "time" else 456.0,
        exe=lambda: str(tmp_path / "other.exe") if mismatch == "exe" else actual_exe,
        cmdline=lambda: [actual_exe, str(host_path), "run", "--packet", str(packet_path),
                         "--packet-sha256", ("b" if mismatch == "command" else "a") * 64])
    shim = SimpleNamespace(exe=lambda: sys.executable,
                           parent=lambda: None if mismatch == "absent" else host,
                           cmdline=lambda: [sys.executable, "-m", "operator"])
    child = SimpleNamespace(parent=lambda: shim, exe=lambda: actual_exe,
                            cmdline=lambda: [actual_exe, "-m", "operator"])
    monkeypatch.setattr(psutil, "Process", lambda: child)
    assert cli._parent_process_matches(host_pid=123, host_create_time=456.0,
        host_script=host_path, packet_path=packet_path, packet_sha256="a" * 64) is (mismatch is None)


def test_invalid_host_packet_stops_cli_before_supervisor(tmp_path, monkeypatch):
    from scripts.ops import query_decomposition_pilot_operator as operator

    def forbidden(**kwargs):
        pytest.fail("invalid delegation must not dispatch")

    monkeypatch.setattr(operator, "supervise_pilot", forbidden)
    monkeypatch.setenv("RAG_QUERY_PILOT_HOST_PACKET", str(tmp_path / "missing.json"))
    monkeypatch.setenv("RAG_QUERY_PILOT_HOST_CONTAINED", "1")
    assert cli.operator_main(_argv(tmp_path)) == 1
    terminal = json.loads((tmp_path / ".local/run/terminal.json").read_bytes())
    assert terminal["status"] == "terminal_failure"


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows venv process chain")
def test_real_windows_job_child_recognizes_bound_host_parent(tmp_path):
    import os
    import subprocess

    source = Path(cli.__file__).resolve().parents[2]
    packet = tmp_path / "synthetic-packet.json"
    result = subprocess.run([sys.executable,
        str(source / "tests/fixtures/query_host_parent_probe.py"), "run", "--packet",
        str(packet), "--packet-sha256", "a" * 64], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(source), str(source / "src")))},
        capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(packet.with_suffix(".result.json").read_bytes()) == {
        "parent_matches": True, "in_job": True}


def test_job_membership_is_boolean_and_non_windows_is_false(monkeypatch):
    assert type(cli._current_process_in_windows_job()) is bool
    monkeypatch.setattr(cli.sys, "platform", "linux")
    assert cli._current_process_in_windows_job() is False


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{", b'{"schema":"wrong"}'])
def test_packet_rejects_malformed_shape(tmp_path, raw):
    path = tmp_path / "packet.json"
    path.write_bytes(raw)
    assert cli._packet_bound_to_args(path, cli.hashlib.sha256(raw).hexdigest(), {}) is None
    assert cli._packet_bound_to_args(path, "0" * 64, {}) is None


@pytest.mark.parametrize("raw", [b"[]", b"null", b"{", b"{}"])
def test_consumed_marker_rejects_malformed_shape(tmp_path, raw):
    state = tmp_path / ".local/run-scheduled-host"
    state.mkdir(parents=True)
    (state / "consumed.json").write_bytes(raw)
    assert cli._consumed_marker_bound(source_root=tmp_path,
        result_path=tmp_path / ".local/run/result.json",
        packet_path=tmp_path / ".local/packet.json", packet_sha256="a" * 64,
        packet={}, operator_sha256="b" * 64) is None


def test_consumed_marker_rejects_outside_and_missing_paths(tmp_path):
    for packet_path in (tmp_path / "outside.json", tmp_path / ".local/missing.json"):
        assert cli._consumed_marker_bound(source_root=tmp_path,
            result_path=tmp_path / ".local/run/result.json", packet_path=packet_path,
            packet_sha256="a" * 64, packet={}, operator_sha256="b" * 64) is None
