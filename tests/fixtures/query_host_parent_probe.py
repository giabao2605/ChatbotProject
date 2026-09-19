"""Finite OS-boundary probe: no operator, authorization or provider is invoked."""
import argparse
import json
import os
from pathlib import Path
import sys

import psutil
from scripts.ops.query_decomposition_pilot_operator_cli import (
    _current_process_in_windows_job, _parent_process_matches,
)
from scripts.ops.query_pilot_windows_job import WindowsJobProcess

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=["run", "probe"])
parser.add_argument("--packet", type=Path, required=True)
parser.add_argument("--packet-sha256", required=True)
args = parser.parse_args()
if args.mode == "probe":
    result = {
        "parent_matches": _parent_process_matches(
            host_pid=int(os.environ["SYNTHETIC_HOST_PID"]),
            host_create_time=float(os.environ["SYNTHETIC_HOST_CREATE_TIME"]),
            host_script=Path(__file__), packet_path=args.packet,
            packet_sha256=args.packet_sha256),
        "in_job": _current_process_in_windows_job(),
    }
    with args.packet.with_suffix(".result.json").open("x") as output:
        json.dump(result, output)
    raise SystemExit(0 if all(result.values()) else 1)

environment = {**os.environ, "SYNTHETIC_HOST_PID": str(os.getpid()),
               "SYNTHETIC_HOST_CREATE_TIME": repr(psutil.Process().create_time())}
command = [sys.executable, str(Path(__file__).resolve()), "probe", "--packet",
           str(args.packet), "--packet-sha256", args.packet_sha256]
with WindowsJobProcess(command, cwd=args.packet.parent, env=environment) as job:
    exit_code = job.wait(timeout=15)
    job.terminate()
    assert job.wait_empty(timeout=5)
raise SystemExit(exit_code)
