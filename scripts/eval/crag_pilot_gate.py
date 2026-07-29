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

from mech_chatbot.evaluation.crag_pilot import (
    build_pilot_artifact,
    canonical_artifact_sha256,
)


CHECKPOINT_CHECKS = (
    "time_range_valid",
    "matched_pairs_unique",
    "matched_pair_payload_complete",
    "pair_timestamps_in_window",
    "both_arms_observed",
    "all_pairs_adjudicated",
    "cohort_immutable",
    "assignment_events_unique",
    "pair_assignment_consistent",
    "owners_assigned",
    "reviewer_signoff_complete",
    "review_governance_valid",
    "daily_sampling_complete",
    "sampled_pairs_complete",
    "assignment_timestamps_in_window",
    "isolated_deployments",
    "feature_flags_correct",
    "deployment_preflight_passed",
    "assignment_version_pinned",
    "sampling_version_pinned",
    "snapshot_pinned",
    "voyage_policy_pinned",
    "wrong_refusal_reduced",
    "wrong_answer_not_increased",
    "leakage_zero",
    "latency_within_budget",
    "cost_within_budget",
    "correction_budget",
    "repair_budget",
    "claim_precision_at_least_99",
    "citation_accuracy_at_least_99",
    "no_abort_condition",
)


def build_checkpoint_artifact(
    artifact: dict, *, required_pairs: int = 20, maximum_days: float = 3.0,
) -> dict:
    checks = artifact.get("checks") or {}
    missing = [
        name for name in CHECKPOINT_CHECKS
        if checks.get(name) is not True and checks.get(name) is not False
    ]
    failed = [
        name for name in CHECKPOINT_CHECKS
        if checks.get(name) is False
    ]
    abort = artifact.get("abort") or {}
    matched_pairs = int(artifact.get("matched_pair_count") or 0)
    duration_days = float(artifact.get("duration_days") or 0.0)
    if abort.get("triggered") is True:
        decision = "aborted"
    elif duration_days > maximum_days:
        decision = "rejected"
    elif failed:
        decision = "rejected"
    elif missing or artifact.get("decision") not in {"running", "accepted"}:
        decision = "inconclusive"
    elif matched_pairs >= required_pairs:
        decision = "checkpoint_go"
    elif duration_days >= maximum_days:
        decision = "inconclusive"
    else:
        decision = "running"
    return {
        "schema": "crag-controlled-demo-checkpoint-v1",
        "source_pilot_schema": artifact.get("schema"),
        "source_pilot_sha256": canonical_artifact_sha256(artifact),
        "source_pilot_decision": artifact.get("decision"),
        "decision": decision,
        "passed": decision in {"running", "checkpoint_go"},
        "matched_pair_count": matched_pairs,
        "required_matched_pairs": required_pairs,
        "duration_days": duration_days,
        "maximum_duration_days": maximum_days,
        "abort": abort,
        "failed_checks": failed,
        "missing_checks": missing,
        "checks": {name: checks.get(name) for name in CHECKPOINT_CHECKS},
    }


def pilot_exit_code(artifact: dict, *, checkpoint: bool) -> int:
    """Return success only for a final pass or a safe controlled-demo checkpoint."""
    if not checkpoint:
        return 0 if artifact.get("passed") is True else 2
    return 0 if (
        artifact.get("schema") == "crag-controlled-demo-checkpoint-v1"
        and artifact.get("decision") in {"running", "checkpoint_go"}
        and artifact.get("passed") is True
    ) else 2


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


def _read_json_object(path: Path, label: str) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


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
    parser.add_argument("--control-trace", type=Path, required=True)
    parser.add_argument("--candidate-trace", type=Path, required=True)
    parser.add_argument(
        "--control-latency-breakdown", type=Path, action="append", required=True
    )
    parser.add_argument(
        "--candidate-latency-breakdown", type=Path, action="append", required=True
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--checkpoint",
        action="store_true",
        help="Allow a safe in-progress pilot while preserving all abort checks.",
    )
    args = parser.parse_args(argv)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {args.output_dir}")
    config = _read_json_object(args.config, "pilot config")
    preflight = _read_json_object(args.preflight, "deployment preflight")
    config["deployment_preflight"] = preflight
    trace_snapshot = _read_json_object(args.trace_snapshot, "trace snapshot")
    trace_source = Path(str((trace_snapshot.get("source") or {}).get("path") or ""))
    if not trace_source.is_file():
        raise ValueError("trace snapshot source file does not exist")
    trace_sha256 = _sha256(trace_source)
    if trace_sha256 != (trace_snapshot.get("source") or {}).get("sha256"):
        raise ValueError("trace snapshot source SHA-256 mismatch")
    config["trace_snapshot"] = trace_snapshot
    arm_trace_paths = {
        "control": args.control_trace.resolve(),
        "candidate": args.candidate_trace.resolve(),
    }
    if arm_trace_paths["control"] == arm_trace_paths["candidate"]:
        raise ValueError("control and candidate trace paths must be distinct")
    arm_artifact_paths = {
        "control": [path.resolve() for path in args.control_latency_breakdown],
        "candidate": [path.resolve() for path in args.candidate_latency_breakdown],
    }
    all_artifact_paths = [
        path for paths in arm_artifact_paths.values() for path in paths
    ]
    if len(set(all_artifact_paths)) != len(all_artifact_paths):
        raise ValueError("latency breakdown paths must be distinct across both arms")

    latency_breakdowns: dict[str, list[dict]] = {}
    latency_hashes: dict[str, list[str]] = {}
    arm_trace_hashes = {
        arm: _sha256(path) for arm, path in arm_trace_paths.items()
    }
    if arm_trace_hashes["control"] == arm_trace_hashes["candidate"]:
        raise ValueError("control and candidate trace hashes must be distinct")
    observed_artifact_hashes: set[str] = set()
    observed_content_hashes: set[str] = set()
    for arm in ("control", "candidate"):
        artifact_paths = arm_artifact_paths[arm]
        trace_hash = arm_trace_hashes[arm]
        artifacts = [
            _read_json_object(path, f"{arm} latency breakdown")
            for path in artifact_paths
        ]
        if any(
            (artifact.get("source") or {}).get("sha256") != trace_hash
            for artifact in artifacts
        ):
            raise ValueError(f"{arm} latency breakdown source SHA-256 mismatch")
        file_hashes = [_sha256(path) for path in artifact_paths]
        content_hashes = [canonical_artifact_sha256(value) for value in artifacts]
        if any(value in observed_artifact_hashes for value in file_hashes):
            raise ValueError("latency breakdown file hashes must be distinct")
        if any(value in observed_content_hashes for value in content_hashes):
            raise ValueError("latency breakdown content hashes must be distinct")
        observed_artifact_hashes.update(file_hashes)
        observed_content_hashes.update(content_hashes)
        latency_breakdowns[arm] = [
            {
                "artifact": artifact,
                "file_sha256": file_hash,
                "content_sha256": content_hash,
            }
            for artifact, file_hash, content_hash in zip(
                artifacts, file_hashes, content_hashes
            )
        ]
        latency_hashes[arm] = file_hashes
    config["latency_breakdowns"] = latency_breakdowns
    config["source_artifacts"] = {
        "assignments_sha256": _sha256(args.assignments),
        "pairs_sha256": _sha256(args.pairs),
        "windows_sha256": _sha256(args.windows),
        "preflight_sha256": _sha256(args.preflight),
        "trace_snapshot_sha256": _sha256(args.trace_snapshot),
        "trace_sha256": trace_sha256,
        "control_trace_sha256": arm_trace_hashes["control"],
        "candidate_trace_sha256": arm_trace_hashes["candidate"],
        "latency_breakdown_sha256s": latency_hashes,
    }
    artifact = build_pilot_artifact(
        config,
        _read_jsonl(args.pairs),
        assignment_events=_read_jsonl(args.assignments),
        monitoring_windows=_read_jsonl(args.windows),
    )
    checkpoint_marker = None
    if args.checkpoint:
        required_pairs = int(config.get("checkpoint_matched_pairs") or 20)
        checkpoint_marker = build_checkpoint_artifact(
            artifact,
            required_pairs=required_pairs,
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "pilot.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "pilot.md").write_text(
        _render_markdown(artifact), encoding="utf-8"
    )
    if checkpoint_marker is not None:
        (args.output_dir / "checkpoint.json").write_text(
            json.dumps(checkpoint_marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return pilot_exit_code(
        checkpoint_marker if checkpoint_marker is not None else artifact,
        checkpoint=args.checkpoint,
    )


if __name__ == "__main__":
    raise SystemExit(main())
