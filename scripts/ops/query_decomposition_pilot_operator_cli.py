"""Command-line boundary for the governed Query pilot operator."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path


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


def operator_main(argv: list[str] | None = None) -> int:
    from scripts.ops import query_decomposition_pilot_operator as operator

    args = _parser().parse_args(argv)
    try:
        root = args.source_root.resolve()
        if not all(operator._inside(path.resolve(), root / ".local") for path in (
            args.result, args.terminal,
        )):
            raise operator.OperatorStopped("operator_output_outside_dot_local")
        if not operator._operator_outputs_fresh((args.result, args.terminal)):
            raise operator.OperatorStopped("operator_output_not_fresh")
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
            service_token=os.environ.get("RAG_SERVICE_TOKEN", ""),
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
