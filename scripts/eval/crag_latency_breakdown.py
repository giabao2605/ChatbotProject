"""Build a privacy-safe per-stage latency artifact from immutable RAG traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path


STAGE_EVENTS = {
    "retrieval": "retrieval",
    "parent_context": "parent_context",
    "rerank": "rerank",
    "llm_generation": "generation",
    "grounded_math_generation": "generation",
    "deterministic_generation": "generation",
    "corrective_retrieval": "correction",
    "claim_repair": "claim_repair",
}
STAGE_ORDER = (
    "retrieval",
    "parent_context",
    "rerank",
    "generation",
    "correction",
    "claim_repair",
    "total",
)


def _parse_timestamp(value: str | None):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _nearest_rank(values: list[int], percentile: float):
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(len(ordered) * percentile + 0.999999))
    return ordered[min(rank, len(ordered)) - 1]


def _latency(value) -> int | None:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def build_latency_breakdown(
    path: Path,
    *,
    start: str | None = None,
    end: str | None = None,
    execution_contexts: set[str] | None = None,
) -> dict:
    """Return only hashed trace identity and numeric stage latency."""
    path = Path(path)
    start_at = _parse_timestamp(start)
    end_at = _parse_timestamp(end)
    contexts = execution_contexts or {"evaluation"}
    stages_by_trace: dict[str, dict[str, int]] = defaultdict(dict)
    completed_traces: set[str] = set()
    parse_errors = 0

    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if not isinstance(event, dict):
            parse_errors += 1
            continue
        if str(event.get("execution_context") or "production") not in contexts:
            continue
        timestamp = _parse_timestamp(event.get("ts"))
        if start_at and (timestamp is None or timestamp < start_at):
            continue
        if end_at and (timestamp is None or timestamp > end_at):
            continue
        trace_id = str(event.get("trace_id") or "").strip()
        if not trace_id:
            continue
        event_name = str(event.get("event") or "")
        if event_name == "rag_end":
            latency = _latency(event.get("final_latency_ms"))
            if latency is not None:
                stages_by_trace[trace_id]["total"] = latency
                completed_traces.add(trace_id)
            continue
        stage = STAGE_EVENTS.get(event_name)
        latency = _latency(event.get("latency_ms"))
        if stage and latency is not None:
            stages_by_trace[trace_id][stage] = (
                stages_by_trace[trace_id].get(stage, 0) + latency
            )

    traces = []
    stage_values: dict[str, list[int]] = defaultdict(list)
    for trace_id in sorted(completed_traces):
        values = {
            stage: stages_by_trace[trace_id][stage]
            for stage in STAGE_ORDER
            if stage in stages_by_trace[trace_id]
        }
        for stage, latency in values.items():
            stage_values[stage].append(latency)
        traces.append(
            {
                "trace_id_sha256": hashlib.sha256(trace_id.encode("utf-8")).hexdigest(),
                "stages_ms": values,
            }
        )

    stage_summary = {}
    for stage in STAGE_ORDER:
        values = stage_values.get(stage) or []
        if not values:
            continue
        stage_summary[stage] = {
            "sample_count": len(values),
            "latency_p50_ms": _nearest_rank(values, 0.50),
            "latency_p95_ms": _nearest_rank(values, 0.95),
            "latency_max_ms": max(values),
        }
    return {
        "schema": "crag-latency-breakdown-v1",
        "source": {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "git_sha": _git_sha(),
        },
        "filters": {
            "start": start,
            "end": end,
            "execution_contexts": sorted(contexts),
        },
        "query_count": len(traces),
        "parse_errors": parse_errors,
        "stage_summary": stage_summary,
        "traces": traces,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--context", action="append", dest="contexts")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_latency_breakdown(
        args.path,
        start=args.start,
        end=args.end,
        execution_contexts=set(args.contexts or ["evaluation"]),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if report["query_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
