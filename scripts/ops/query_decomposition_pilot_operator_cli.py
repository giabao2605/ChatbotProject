"""Command-line boundary for the governed Query pilot operator."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    for name in (
        "source-root", "schedule", "authorization", "bundle", "manifest",
        "trace", "wal", "claims", "result", "terminal", "python-exe",
        "frozen-health-output", "runtime-state", "runtime-stop",
        "runtime-out-log", "runtime-err-log",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in (
        "authorization-sha256", "bundle-sha256", "manifest-sha256",
        "snapshot-fingerprint", "deployment-id", "qdrant-collection",
        "sql-database",
    ):
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--port", type=int, required=True)
    return parser


def _current_process_in_windows_job() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.IsProcessInJob.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        )
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        result = wintypes.BOOL()
        ok = kernel32.IsProcessInJob(
            kernel32.GetCurrentProcess(),
            wintypes.HANDLE(0),
            ctypes.byref(result),
        )
        return bool(ok and result.value)
    except Exception:
        return False


def _operator_values(args: argparse.Namespace) -> dict:
    return {
        name: str(value.resolve()) if isinstance(value, Path) else value
        for name, value in vars(args).items()
    }


def _operator_arguments_sha256(values: dict) -> str:
    raw = json.dumps(
        values,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _path_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _packet_bound_to_args(packet_path: Path, expected_sha256: str, values: dict) -> dict | None:
    try:
        raw = packet_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            return None
        packet = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(packet, dict)
        or packet.get("schema") != "query-pilot-scheduled-host-packet-v1"
        or packet.get("operator") != values
    ):
        return None
    try:
        from scripts.ops import query_decomposition_pilot_operator as operator

        root = Path(values["source_root"])
        bindings = {
            "host_sha256": root / "scripts/ops/query_pilot_scheduled_host.py",
            "job_sha256": root / "scripts/ops/query_pilot_windows_job.py",
            "task_script_sha256": root / "scripts/ops/query_pilot_scheduled_task.ps1",
            "interpreter_sha256": Path(values["python_exe"]),
        }
        if (operator._source_commit(root) != packet["source_commit"]
                or any(hashlib.sha256(path.read_bytes()).hexdigest() != packet[key]
                       for key, path in bindings.items())
                or any(hashlib.sha256(Path(values[key]).read_bytes()).hexdigest() != packet["inputs"][key]
                       for key in ("schedule", "authorization", "bundle", "manifest"))):
            return None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None
    return packet


def _consumed_marker_bound(
    *, source_root: Path, result_path: Path, packet_path: Path,
    packet_sha256: str, packet: dict, operator_sha256: str,
) -> dict | None:
    run_root = result_path.resolve().parent
    state_root = run_root.with_name(run_root.name + "-scheduled-host")
    consumed_path = state_root / "consumed.json"
    if not all((
        _path_inside(packet_path, source_root / ".local"),
        _path_inside(run_root, source_root / ".local"),
        _path_inside(state_root, source_root / ".local"),
    )):
        return None
    try:
        consumed = json.loads(consumed_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(consumed, dict):
        return None
    expected = {
        "schema": "query-pilot-scheduled-host-consumed-v1",
        "packet_path": str(packet_path.resolve()),
        "packet_sha256": packet_sha256,
        "operator_arguments_sha256": operator_sha256,
        "source_commit": packet.get("source_commit"),
        "retry_authorized": False,
        "catch_up_authorized": False,
    }
    if any(consumed.get(name) != value for name, value in expected.items()):
        return None
    if not isinstance(consumed.get("host_pid"), int):
        return None
    if type(consumed.get("host_create_time")) not in (int, float):
        return None
    return consumed


def _parent_process_matches(
    *, host_pid: int, host_create_time: float, host_script: Path,
    packet_path: Path, packet_sha256: str,
) -> bool:
    try:
        import psutil

        current = psutil.Process()
        parent = current.parent()
        if (parent is not None
                and Path(parent.exe()).resolve() == Path(sys.executable).resolve()
                and parent.cmdline()[1:] == current.cmdline()[1:]):
            parent = parent.parent()
        if parent is None or parent.pid != host_pid:
            return False
        if abs(float(parent.create_time()) - host_create_time) > 0.001:
            return False
        command = parent.cmdline()
        expected = [
            str(host_script.resolve()),
            "run",
            "--packet",
            str(packet_path.resolve()),
            "--packet-sha256",
            packet_sha256,
        ]
        return (command[1:] == expected
                and Path(parent.exe()).resolve() == Path(current.exe()).resolve())
    except Exception:
        return False


def _defer_capture_cleanup_authorized(args: argparse.Namespace, source_root: Path) -> bool:
    packet_path = Path(os.environ.get("RAG_QUERY_PILOT_HOST_PACKET", "")).resolve()
    packet_sha256 = os.environ.get("RAG_QUERY_PILOT_HOST_PACKET_SHA256", "")
    host_script = source_root / "scripts/ops/query_pilot_scheduled_host.py"
    operator_sha256 = os.environ.get("RAG_QUERY_PILOT_HOST_OPERATOR_SHA256", "")
    values = _operator_values(args)
    packet = _packet_bound_to_args(packet_path, packet_sha256, values)
    if packet is None or operator_sha256 != _operator_arguments_sha256(values):
        return False
    consumed = _consumed_marker_bound(
        source_root=source_root, result_path=args.result.resolve(),
        packet_path=packet_path, packet_sha256=packet_sha256,
        packet=packet, operator_sha256=operator_sha256,
    )
    if consumed is None:
        return False
    try:
        host_pid = int(os.environ.get("RAG_QUERY_PILOT_HOST_PID", ""))
        host_create_time = float(os.environ.get("RAG_QUERY_PILOT_HOST_CREATE_TIME", ""))
    except ValueError:
        return False
    return (
        os.environ.get("RAG_QUERY_PILOT_HOST_CONTAINED") == "1"
        and consumed["host_pid"] == host_pid
        and abs(float(consumed["host_create_time"]) - host_create_time) <= 0.001
        and _current_process_in_windows_job()
        and _parent_process_matches(
            host_pid=host_pid, host_create_time=host_create_time,
            host_script=host_script, packet_path=packet_path,
            packet_sha256=packet_sha256,
        )
    )


def operator_main(argv: list[str] | None = None) -> int:
    from scripts.ops import query_decomposition_pilot_operator as operator
    from mech_chatbot.config.settings import load_settings

    args = _parser().parse_args(argv)
    try:
        root = args.source_root.resolve()
        if not all(operator._inside(path.resolve(), root / ".local") for path in (
            args.result, args.terminal,
        )):
            raise operator.OperatorStopped("operator_output_outside_dot_local")
        if not operator._operator_outputs_fresh((args.result, args.terminal)):
            raise operator.OperatorStopped("operator_output_not_fresh")
        defer_cleanup = _defer_capture_cleanup_authorized(args, root)
        if os.environ.get("RAG_QUERY_PILOT_HOST_PACKET") and not defer_cleanup:
            raise operator.OperatorStopped("scheduled_cleanup_delegation_rejected")
        result = operator.supervise_pilot(
            python_exe=args.python_exe, source_root=args.source_root,
            schedule_path=args.schedule, authorization_path=args.authorization,
            authorization_sha256=args.authorization_sha256,
            bundle_path=args.bundle, bundle_sha256=args.bundle_sha256,
            manifest_path=args.manifest, manifest_sha256=args.manifest_sha256,
            snapshot_fingerprint=args.snapshot_fingerprint,
            deployment_id=args.deployment_id, port=args.port,
            qdrant_collection=args.qdrant_collection,
            sql_database=args.sql_database, trace_path=args.trace,
            wal_path=args.wal, claim_dir=args.claims,
            frozen_health_path=args.frozen_health_output,
            runtime_state_path=args.runtime_state,
            runtime_stop_path=args.runtime_stop,
            runtime_out_log=args.runtime_out_log,
            runtime_err_log=args.runtime_err_log,
            result_path=args.result, terminal_path=args.terminal,
            service_token=load_settings().RAG_SERVICE_TOKEN,
            defer_capture_cleanup=defer_cleanup,
        )
        operator._exclusive_json(args.result.resolve(), result)
        return 0
    except Exception as exc:
        reason = operator._terminal_reason(exc)
        try:
            operator._exclusive_json(args.terminal.resolve(), {
                "schema": "query-decomposition-pilot-operator-terminal-v1",
                "status": "terminal_failure", "reason": reason,
                "recorded_at": operator._format(datetime.now(timezone.utc)),
                "retry_authorized": False, "catch_up_authorized": False,
                "raw_question_persisted": False,
            })
        except operator.OperatorStopped:
            pass
        return 1


__all__ = ["operator_main"]
