"""Single-use Scheduled Task host for the existing governed Query operator.

Preparation never registers a task or starts a process. Execution needs a fresh,
currently valid authorization and a logged-in interactive Windows user. This is
not a logoff/reboot continuation mechanism.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

# Direct script execution is used by Task Scheduler, with no ambient PYTHONPATH.
if __package__ in {None, ""}:
    sys.path[:0] = [str(Path(__file__).resolve().parents[2]),
                    str(Path(__file__).resolve().parents[2] / "src")]

from scripts.ops import query_decomposition_pilot_operator as operator
from scripts.ops.query_decomposition_pilot_operator_cli import _parser, _operator_arguments_sha256
from scripts.ops.query_pilot_operator_support import (
    OperatorStopped, inside, pilot_run_paths, pilot_run_root,
)

SCHEMA = "query-pilot-scheduled-host-packet-v1"
_PACKET_FIELDS = {
    "schema", "source_commit", "operator", "inputs", "interpreter_sha256",
    "host_sha256", "task_script_sha256", "job_sha256", "interactive_only",
    "restart_count", "retry_authorized", "catch_up_authorized",
}
_INPUTS = ("schedule", "authorization", "bundle", "manifest")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _arguments(values: dict) -> list[str]:
    actions = tuple(action for action in _parser()._actions if action.dest != "help")
    if set(values) != {action.dest for action in actions}:
        raise OperatorStopped("scheduled_operator_arguments_invalid")
    result = []
    for action in actions:
        value = values[action.dest]
        if action.dest == "port":
            if type(value) is not int or not 1 <= value <= 65535:
                raise OperatorStopped("scheduled_operator_arguments_invalid")
        elif not isinstance(value, str) or not value or any(c in value for c in "\r\n\0"):
            raise OperatorStopped("scheduled_operator_arguments_invalid")
        result.extend((action.option_strings[0], str(value)))
    return result


def _process_create_time() -> float:
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:
        raise OperatorStopped("scheduled_host_identity_unavailable") from None


def _validated_inputs(values: dict, source_commit: str):
    args = _parser().parse_args(_arguments(values))
    root = args.source_root.resolve()
    if root != Path(__file__).resolve().parents[2]:
        raise OperatorStopped("scheduled_source_root_mismatch")
    if operator._source_commit(root) != source_commit:
        raise OperatorStopped("scheduled_source_commit_mismatch")
    authorization, schedule, _ = operator.validate_operator_inputs(
        source_root=root, schedule_path=args.schedule.resolve(),
        authorization_path=args.authorization.resolve(),
        authorization_sha256=args.authorization_sha256,
        bundle_path=args.bundle.resolve(), bundle_sha256=args.bundle_sha256,
        manifest_path=args.manifest.resolve(), manifest_sha256=args.manifest_sha256,
        now=datetime.now(timezone.utc),
    )
    run_root = pilot_run_root(authorization, root)
    paths = pilot_run_paths(run_root)
    supplied = {
        "trace": args.trace, "wal": args.wal, "claims": args.claims,
        "result": args.result, "terminal": args.terminal,
        "frozen_health": args.frozen_health_output,
        "runtime_state": args.runtime_state, "runtime_stop": args.runtime_stop,
        "runtime_out": args.runtime_out_log, "runtime_err": args.runtime_err_log,
    }
    if os.path.lexists(run_root) or any(
        path.resolve() != paths[name] for name, path in supplied.items()
    ):
        raise OperatorStopped("scheduled_run_root_not_fresh")
    if not args.python_exe.is_file():
        raise OperatorStopped("scheduled_interpreter_invalid")
    if not operator._digest(args.snapshot_fingerprint) or any(
        re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) is None
        for value in (args.deployment_id, args.sql_database, args.qdrant_collection)
    ):
        raise OperatorStopped("scheduled_runtime_identity_invalid")
    return args, schedule, run_root


def validate_packet(packet_path: Path, expected_sha256: str) -> tuple[dict, object, dict, Path]:
    """Validate every binding before any process, state directory or task write."""
    try:
        raw = packet_path.read_bytes()
        if not operator._digest(expected_sha256) or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise OperatorStopped("scheduled_packet_drift")
        packet = json.loads(raw)
        if not isinstance(packet, dict) or set(packet) != _PACKET_FIELDS:
            raise OperatorStopped("scheduled_packet_invalid")
        if not all((
            packet["schema"] == SCHEMA, packet["interactive_only"] is True,
            type(packet["restart_count"]) is int and packet["restart_count"] == 0,
            packet["retry_authorized"] is False, packet["catch_up_authorized"] is False,
            isinstance(packet["operator"], dict),
            isinstance(packet["inputs"], dict) and set(packet["inputs"]) == set(_INPUTS),
        )):
            raise OperatorStopped("scheduled_packet_invalid")
        values = packet["operator"]
        _arguments(values)
        root = Path(values["source_root"]).resolve()
        if not inside(packet_path, root / ".local"):
            raise OperatorStopped("scheduled_packet_path_invalid")
        bindings = {
            "interpreter_sha256": Path(values["python_exe"]),
            "host_sha256": Path(__file__).resolve(),
            "task_script_sha256": root / "scripts/ops/query_pilot_scheduled_task.ps1",
            "job_sha256": root / "scripts/ops/query_pilot_windows_job.py",
        }
        if any(_hash(path) != packet[name] for name, path in bindings.items()):
            raise OperatorStopped("scheduled_executable_drift")
        if any(_hash(Path(values[name])) != packet["inputs"][name] for name in _INPUTS):
            raise OperatorStopped("scheduled_input_drift")
        args, schedule, run_root = _validated_inputs(values, packet["source_commit"])
        if os.path.lexists(_host_root(run_root)):
            raise OperatorStopped("scheduled_host_already_consumed")
        return packet, args, schedule, run_root
    except OperatorStopped:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise OperatorStopped("scheduled_packet_invalid") from None


def _host_root(run_root: Path) -> Path:
    return run_root.with_name(run_root.name + "-scheduled-host")


def wait_port_released(port: int, timeout: float) -> bool:
    """Observe an exclusive loopback bind; never stop an unowned listener."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)


def contain_operator(
    command: list[str], *, source_root: Path, environment: dict,
    timeout_seconds: float, port: int, job_factory=None,
    port_released=wait_port_released,
) -> dict:
    """Own one operator tree; no retries and no synthesized operator evidence.

    Injection seams are OS process and socket boundaries, never exposed by CLI.
    The caller constructs the only permitted operator command from the packet.
    """
    if job_factory is None:
        from scripts.ops.query_pilot_windows_job import WindowsJobProcess

        job_factory = WindowsJobProcess
    reason, exit_code, empty, released = "scheduled_host_failure", None, False, False
    try:
        with job_factory(command, cwd=source_root, env=environment) as job:
            try:
                exit_code = job.wait(timeout=timeout_seconds)
                reason = "operator_exited" if exit_code == 0 else "operator_failed"
            except subprocess.TimeoutExpired:
                reason = "operator_timeout"
            except Exception:
                reason = "scheduled_host_failure"
            finally:
                # The job is the ownership proof; PID enumeration cannot replace it.
                job.terminate()
                empty = job.wait_empty(timeout=15)
                released = empty and port_released(port, 15)
                if not empty:
                    reason = "owned_job_not_empty"
                elif not released:
                    reason = "runtime_port_not_released"
    except Exception:
        reason, empty, released = "scheduled_host_cleanup_failure", False, False
    return {
        "schema": "query-pilot-scheduled-host-receipt-v1",
        "reason": reason, "operator_exit_code": exit_code,
        "owned_job_empty": empty, "runtime_port_released": released,
        "recorded_at": operator._format(datetime.now(timezone.utc)),
        "retry_authorized": False, "catch_up_authorized": False,
        "pilot_accepted": False, "preserve_wal": True,
    }


def prepare_packet(values: dict, source_commit: str, packet_path: Path) -> dict:
    """Freeze a non-executing task input using an already materialized approval."""
    args, _, run_root = _validated_inputs(values, source_commit)
    root = args.source_root.resolve()
    target = packet_path.resolve()
    if not inside(target, root / ".local") or inside(target, run_root) or inside(target, _host_root(run_root)):
        raise OperatorStopped("scheduled_packet_path_invalid")
    normalized = {
        name: str(value.resolve()) if isinstance(value, Path) else value
        for name, value in vars(args).items()
    }
    packet = {
        "schema": SCHEMA, "source_commit": source_commit, "operator": normalized,
        "inputs": {name: _hash(Path(normalized[name])) for name in _INPUTS},
        "interpreter_sha256": _hash(args.python_exe.resolve()),
        "host_sha256": _hash(Path(__file__).resolve()),
        "task_script_sha256": _hash(root / "scripts/ops/query_pilot_scheduled_task.ps1"),
        "job_sha256": _hash(root / "scripts/ops/query_pilot_windows_job.py"),
        "interactive_only": True, "restart_count": 0,
        "retry_authorized": False, "catch_up_authorized": False,
    }
    if os.path.lexists(_host_root(run_root)):
        raise OperatorStopped("scheduled_host_already_consumed")
    operator._exclusive_json(target, packet)
    return {"packet": str(target), "packet_sha256": _hash(target),
            "runtime_started": False, "task_registered": False}


def run_packet(packet_path: Path, expected_sha256: str) -> dict:
    """Run exactly once; absent receipt after hard host death requires disposition."""
    from mech_chatbot.config.settings import load_settings

    packet, args, schedule, run_root = validate_packet(packet_path, expected_sha256)
    if Path(sys.executable).resolve() != args.python_exe.resolve():
        raise OperatorStopped("scheduled_interpreter_mismatch")
    service_token = load_settings().RAG_SERVICE_TOKEN
    if not service_token.strip():
        raise OperatorStopped("scheduled_service_token_missing")
    if not wait_port_released(args.port, 0):
        raise OperatorStopped("scheduled_runtime_port_occupied")
    host_create_time = _process_create_time()
    state_root = _host_root(run_root)
    state_root.mkdir(parents=False, exist_ok=False)
    packet = {**packet, "operator_arguments_sha256": _operator_arguments_sha256(packet["operator"])}
    operator._exclusive_json(state_root / "consumed.json", {
        "schema": "query-pilot-scheduled-host-consumed-v1",
        "packet_path": str(packet_path.resolve()),
        "packet_sha256": expected_sha256,
        "operator_arguments_sha256": packet["operator_arguments_sha256"],
        "source_commit": packet["source_commit"],
        "host_pid": os.getpid(),
        "host_create_time": host_create_time,
        "retry_authorized": False, "catch_up_authorized": False,
        "recorded_at": operator._format(datetime.now(timezone.utc)),
    })
    root = args.source_root.resolve()
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
                   "RAG_SERVICE_TOKEN": service_token,
                   "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                   "RAG_QUERY_PILOT_HOST_CONTAINED": "1",
                   "RAG_QUERY_PILOT_HOST_PACKET": str(packet_path.resolve()),
                   "RAG_QUERY_PILOT_HOST_PACKET_SHA256": expected_sha256,
                   "RAG_QUERY_PILOT_HOST_PID": str(os.getpid()),
                   "RAG_QUERY_PILOT_HOST_CREATE_TIME": repr(host_create_time),
                   "RAG_QUERY_PILOT_HOST_OPERATOR_SHA256": packet["operator_arguments_sha256"]}
    command = [str(args.python_exe.resolve()), "-m",
               "scripts.ops.query_decomposition_pilot_operator", *_arguments(packet["operator"])]
    last_card = operator._timestamp(schedule["cards"][-1]["scheduled_at"])
    timeout = max(1, min(26 * 3600, (last_card - datetime.now(timezone.utc)).total_seconds() + 300))
    receipt = contain_operator(command, source_root=root, environment=environment,
                               timeout_seconds=timeout, port=args.port)
    receipt = finalize_operator_outcome(
        receipt, run_root=run_root, authorization_sha256=args.authorization_sha256,
    )
    receipt = {**receipt, "packet_sha256": expected_sha256,
               "source_commit": packet["source_commit"]}
    operator._exclusive_json(state_root / "receipt.json", receipt)
    return receipt


def finalize_operator_outcome(receipt: dict, *, run_root: Path, authorization_sha256: str) -> dict:
    """Preserve review samples on success; delete terminal samples only after stop proof."""
    result_valid = False
    try:
        result = json.loads(pilot_run_paths(run_root)["result"].read_bytes())
        result_valid = all((
            result.get("schema") == "query-decomposition-pilot-operator-result-v1",
            result.get("status") == "completed", result.get("completed_request_count") == 100,
            result.get("provider_retries") == 0, result.get("replacement_requests") == 0,
            result.get("catch_up_requests") == 0,
            not os.path.lexists(pilot_run_paths(run_root)["terminal"]),
        ))
    except (OSError, ValueError, AttributeError):
        pass
    safe = receipt.get("owned_job_empty") is True and receipt.get("runtime_port_released") is True
    success = receipt["reason"] == "operator_exited" and result_valid and safe
    cleanup_status = "preserved_for_review" if success else "deferred_unsafe_runtime"
    if safe and not success:
        captures = pilot_run_paths(run_root)["captures"]
        try:
            if captures.is_symlink() or not inside(captures, run_root):
                raise ValueError("capture_path_invalid")
            if captures.exists():
                operator.cleanup_terminal_captures(
                    capture_dir=captures, authorization_sha256=authorization_sha256,
                )
                cleanup_status = "completed"
            else:
                cleanup_status = "not_present"
        except (OSError, RuntimeError, ValueError):
            cleanup_status = "failed"
    return {**receipt, "status": "completed" if success else "terminal_failure",
            "operator_result_valid": result_valid, "capture_cleanup_status": cleanup_status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--packet", type=Path, required=True)
    prepare.add_argument("--source-commit", required=True)
    prepare.add_argument("operator_arguments", nargs=argparse.REMAINDER)
    for name in ("validate", "run"):
        sub = commands.add_parser(name)
        sub.add_argument("--packet", type=Path, required=True)
        sub.add_argument("--packet-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            remaining = args.operator_arguments
            operator_args = _parser().parse_args(remaining[1:] if remaining[:1] == ["--"] else remaining)
            values = {name: str(value) if isinstance(value, Path) else value
                      for name, value in vars(operator_args).items()}
            result = prepare_packet(values, args.source_commit, args.packet)
        elif args.command == "validate":
            packet, operator_args, _, _ = validate_packet(args.packet, args.packet_sha256)
            result = {"status": "validated", "source_root": str(operator_args.source_root.resolve()),
                      "python_exe": str(operator_args.python_exe.resolve()),
                      "interpreter_sha256": packet["interpreter_sha256"],
                      "task_registered": False, "runtime_started": False}
        else:
            result = run_packet(args.packet, args.packet_sha256)
        print(json.dumps(result))
        return int(result.get("status") == "terminal_failure")
    except Exception:
        print(json.dumps({"status": "terminal_failure", "reason": "scheduled_host_rejected",
                          "retry_authorized": False, "catch_up_authorized": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
