"""Build a fail-closed CRAG production-pilot decision artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.evaluation.crag_pilot import build_pilot_artifact


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected one JSON object")
        rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_markdown(artifact: dict) -> str:
    checks = artifact.get("checks") or {}
    lines = [
        "# CRAG production pilot",
        "",
        f"- Run: `{artifact.get('run_id')}`",
        f"- Experiment: `{artifact.get('experiment_id')}`",
        f"- Commit: `{artifact.get('git_sha')}`",
        f"- UTC range: `{artifact.get('start_at')}` to `{artifact.get('end_at')}`",
        f"- Matched pairs: {artifact.get('matched_pair_count')}",
        f"- Decision: `{artifact.get('decision')}`",
        f"- Passed: `{str(bool(artifact.get('passed'))).lower()}`",
        "",
        "## Checks",
        "",
    ]
    lines.extend(
        f"- {name}: `{'pass' if passed else 'fail'}`"
        for name, passed in checks.items()
    )
    lines.extend([
        "",
        "## Abort",
        "",
        f"- Triggered: `{str(bool((artifact.get('abort') or {}).get('triggered'))).lower()}`",
        f"- Reasons: `{json.dumps((artifact.get('abort') or {}).get('reasons') or [])}`",
        "",
        "## Metrics",
        "",
        f"- Control: `{json.dumps((artifact.get('metrics') or {}).get('control') or {})}`",
        f"- Candidate: `{json.dumps((artifact.get('metrics') or {}).get('candidate') or {})}`",
    ])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--trace-snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {args.output_dir}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("pilot config must be a JSON object")
    preflight = json.loads(args.preflight.read_text(encoding="utf-8"))
    if not isinstance(preflight, dict):
        raise ValueError("deployment preflight must be a JSON object")
    config["deployment_preflight"] = preflight
    trace_snapshot = json.loads(args.trace_snapshot.read_text(encoding="utf-8"))
    if not isinstance(trace_snapshot, dict):
        raise ValueError("trace snapshot must be a JSON object")
    trace_source = Path(str((trace_snapshot.get("source") or {}).get("path") or ""))
    if not trace_source.is_file():
        raise ValueError("trace snapshot source file does not exist")
    trace_sha256 = _sha256(trace_source)
    if trace_sha256 != (trace_snapshot.get("source") or {}).get("sha256"):
        raise ValueError("trace snapshot source SHA-256 mismatch")
    config["trace_snapshot"] = trace_snapshot
    config["source_artifacts"] = {
        "assignments_sha256": _sha256(args.assignments),
        "pairs_sha256": _sha256(args.pairs),
        "windows_sha256": _sha256(args.windows),
        "preflight_sha256": _sha256(args.preflight),
        "trace_snapshot_sha256": _sha256(args.trace_snapshot),
        "trace_sha256": trace_sha256,
    }
    artifact = build_pilot_artifact(
        config,
        _read_jsonl(args.pairs),
        assignment_events=_read_jsonl(args.assignments),
        monitoring_windows=_read_jsonl(args.windows),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "pilot.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "pilot.md").write_text(
        _render_markdown(artifact), encoding="utf-8"
    )
    return 0 if artifact["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
