"""Fail-closed prerequisite report for roadmap milestone 2.8."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_GROUPS = ("global", "local", "relational")


def validate_manifest_groups(cases, *, source=Path("<community-manifest>")) -> dict[str, int]:
    from scripts.eval.run_eval import validate_live_case

    counts = {name: 0 for name in REQUIRED_GROUPS}
    seen = set()
    for line_number, case in enumerate(cases or (), 1):
        validate_live_case(case, source=Path(source), line_number=line_number)
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in seen:
            raise ValueError("manifest case ids must be unique and non-empty")
        seen.add(case_id)
        group = str(case.get("evaluation_group") or "").strip().lower()
        if group not in counts:
            raise ValueError(f"unsupported community evaluation group: {group}")
        counts[group] += 1
    missing = [name for name, count in counts.items() if count == 0]
    if missing:
        raise ValueError(f"manifest missing groups: {', '.join(missing)}")
    return counts


def build_readiness(
    *, graph_gate, graph_readiness, detection_report, manifest_groups, detection_version,
    serving_epoch, min_global_answer_gain, approved_summary_count=0,
    pending_summaries_served=0, stale_summary_violations=0,
    max_indexing_latency_ms=60000.0,
) -> dict:
    target = float(min_global_answer_gain or 0.0)
    capability_checks = {
        "graph_gate_schema": (
            graph_gate.get("schema") == "retrieval-intelligence-gate-v1"
        ),
        "graph_readiness_schema": (
            graph_readiness.get("schema") == "graph-readiness-v1"
        ),
        "evaluation_groups_complete": all(
            int(manifest_groups.get(name) or 0) > 0 for name in REQUIRED_GROUPS
        ),
        "detection_version_present": bool(str(detection_version or "").strip()),
        "serving_epoch_present": bool(str(serving_epoch or "").strip()),
        "quality_target_locked": target > 0.0,
        "detection_schema": (
            detection_report.get("schema") == "graph-community-detection-v1"
        ),
        "serving_edge_validation": (
            detection_report.get("serving_edge_validation_passed") is True
        ),
    }
    capability_passed = all(capability_checks.values())
    graph_gate_passed = graph_gate.get("passed") is True
    precision = float(graph_readiness.get("reviewed_edge_precision") or 0.0)
    coverage = float(graph_readiness.get("structured_coverage") or 0.0)
    provenance = float(detection_report.get("provenance_completeness") or 0.0)
    indexing_latency = float(detection_report.get("indexing_latency_ms") or 0.0)
    max_indexing_latency = float(max_indexing_latency_ms or 0.0)
    blockers = []
    if not capability_passed:
        blockers.append("capability_contract_incomplete")
    if not graph_gate_passed:
        blockers.append("graph_gate_not_passed")
    if precision < 0.95:
        blockers.append("reviewed_edge_precision_below_95_percent")
    if coverage < 0.80:
        blockers.append("structured_coverage_below_80_percent")
    if provenance != 1.0:
        blockers.append("community_provenance_incomplete")
    if max_indexing_latency <= 0 or indexing_latency > max_indexing_latency:
        blockers.append("indexing_latency_over_budget")
    ready_for_generation = not blockers
    if ready_for_generation and int(approved_summary_count or 0) <= 0:
        blockers.append("no_reviewed_summary_available")
    if int(pending_summaries_served or 0) != 0:
        blockers.append("pending_summary_served")
    if int(stale_summary_violations or 0) != 0:
        blockers.append("stale_summary_served")
    ready_for_serving = ready_for_generation and not blockers
    return {
        "schema": "community-summary-readiness-v1",
        "capability_passed": capability_passed,
        "ready_for_generation": ready_for_generation,
        "ready_for_serving": ready_for_serving,
        "detection_version": str(detection_version or ""),
        "serving_epoch": str(serving_epoch or ""),
        "target_locked_before_benchmark": target > 0.0,
        "min_global_answer_gain": target,
        "prerequisite_graph_gate_passed": graph_gate_passed,
        "reviewed_edge_precision": precision,
        "structured_coverage": coverage,
        "provenance_completeness": provenance,
        "serving_epoch_valid": bool(str(serving_epoch or "").strip()),
        "indexing_latency_ms": indexing_latency,
        "max_indexing_latency_ms": max_indexing_latency,
        "manifest_groups": dict(manifest_groups),
        "approved_summary_count": int(approved_summary_count or 0),
        "pending_summaries_served": int(pending_summaries_served or 0),
        "stale_summary_violations": int(stale_summary_violations or 0),
        "capability_checks": capability_checks,
        "blockers": blockers,
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-gate", type=Path, required=True)
    parser.add_argument("--graph-readiness", type=Path, required=True)
    parser.add_argument("--detection", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--detection-version", default="connected-components-v1")
    parser.add_argument("--serving-epoch", default="community-v1")
    parser.add_argument("--min-global-answer-gain", type=float, default=0.10)
    parser.add_argument("--max-indexing-latency-ms", type=float, default=60000.0)
    args = parser.parse_args(argv)

    artifact = build_readiness(
        graph_gate=_read_json(args.graph_gate),
        graph_readiness=_read_json(args.graph_readiness),
        detection_report=_read_json(args.detection),
        manifest_groups=validate_manifest_groups(
            _read_jsonl(args.manifest), source=args.manifest
        ),
        detection_version=args.detection_version,
        serving_epoch=args.serving_epoch,
        min_global_answer_gain=args.min_global_answer_gain,
        max_indexing_latency_ms=args.max_indexing_latency_ms,
    )
    artifact.update({
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "inputs": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                args.graph_gate, args.graph_readiness, args.detection, args.manifest
            )
        },
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if artifact["ready_for_generation"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
