"""Inserted only into a temporary host before its synthetic Git freeze.

Proves the production PowerShell wrapper's task transport, not host execution.
The real host process/cleanup path has separate Windows Job integration tests.
"""
import sys as _proof_sys

if __name__ == "__main__" and _proof_sys.argv[1:2] == ["run"]:
    import argparse as _proof_argparse
    import json as _proof_json
    from pathlib import Path as _ProofPath

    _proof_parser = _proof_argparse.ArgumentParser()
    _proof_parser.add_argument("command", choices=["run"])
    _proof_parser.add_argument("--packet", required=True)
    _proof_parser.add_argument("--packet-sha256", required=True)
    _proof_args = _proof_parser.parse_args()
    _proof_output = _ProofPath(__file__).resolve().parents[2] / ".local/scheduled-handoff.json"
    with _proof_output.open("x", encoding="utf-8") as _proof_stream:
        _proof_json.dump({"packet": _proof_args.packet,
                          "packet_sha256": _proof_args.packet_sha256,
                          "cwd": str(_ProofPath.cwd()),
                          "provider_calls": 0, "pilot_dispatches": 0}, _proof_stream)
    raise SystemExit(0)
