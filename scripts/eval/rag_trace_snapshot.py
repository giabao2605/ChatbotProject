"""Create reproducible refusal snapshots from the RAG JSONL trace."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mech_chatbot.evaluation.metrics import nearest_rank


DEFAULT_EXCLUDED_REASONS = {"client_cancelled"}
RERANK_PROVIDERS = ("jina", "voyage")
NON_FALLBACK_COMMUNITY_REASONS = frozenset({
    "approved_summary_used",
    "community_summaries_disabled",
    "graph_retrieval_disabled",
    "query_not_global",
})


def _parse_timestamp(value: str | None):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_snapshot(
    path: Path,
    *,
    start: str | None = None,
    end: str | None = None,
    execution_contexts: set[str] | None = None,
    excluded_reasons: set[str] | None = None,
) -> dict:
    path = Path(path)
    contexts = execution_contexts or {"production"}
    excluded = excluded_reasons if excluded_reasons is not None else DEFAULT_EXCLUDED_REASONS
    start_at = _parse_timestamp(start)
    end_at = _parse_timestamp(end)
    counts: Counter[str] = Counter()
    included_timestamps: list[str] = []
    parse_errors = 0
    legacy_reason_events = 0
    query_latencies: list[float] = []
    estimated_cost = 0.0
    corrective_attempts = 0
    repair_attempts = 0
    corrections_by_trace: Counter[str] = Counter()
    successful_corrections_by_trace: Counter[str] = Counter()
    correction_errors = 0
    repairs_by_trace: Counter[str] = Counter()
    llm_retries = 0
    query_count = 0
    event_counts: Counter[str] = Counter()
    error_events: Counter[str] = Counter()
    fallback_events: Counter[str] = Counter()
    retry_events: Counter[str] = Counter()
    planner_max = subquery_max = calculation_max = graph_edge_max = 0
    retries_by_trace: Counter[str] = Counter()
    external_ai_latencies: dict[str, list[float]] = defaultdict(list)
    external_ai_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    rerank_statuses = {provider: Counter() for provider in RERANK_PROVIDERS}
    rerank_fallbacks: Counter[str] = Counter()
    rerank_status_codes = {provider: Counter() for provider in RERANK_PROVIDERS}
    rerank_retry_attempts: Counter[str] = Counter()
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if str(event.get("execution_context") or "production") not in contexts:
            continue
        timestamp = _parse_timestamp(event.get("ts"))
        if start_at and (timestamp is None or timestamp < start_at):
            continue
        if end_at and (timestamp is None or timestamp > end_at):
            continue
        event_name = str(event.get("event") or "<missing>")
        event_counts[event_name] += 1
        has_error = bool(
            event.get("error")
            or event.get("error_type")
            or str(event.get("status") or "").casefold() == "error"
        )
        if has_error:
            error_events[event_name] += 1
        fallback_fields = [
            value
            for key, value in event.items()
            if "fallback" in str(key).casefold() and bool(value)
        ]
        has_fallback = event_name.endswith("_fallback") or bool(
            fallback_fields
            and not (
                event_name == "community_summaries"
                and all(
                    str(value).casefold() in NON_FALLBACK_COMMUNITY_REASONS
                    for value in fallback_fields
                )
            )
        )
        if has_fallback:
            fallback_events[event_name] += 1
        if event_name.endswith("_retry") or bool(event.get("retry_attempted")):
            retry_events[event_name] += 1
        if event_name == "query_decomposition":
            planner_max = max(planner_max, int(event.get("planner_count") or 0))
            subquery_max = max(subquery_max, int(event.get("subquery_count") or 0))
        if event_name == "grounded_math_generation":
            calculation_max = max(calculation_max, int(event.get("calculations") or 0))
        if event_name == "graph_retrieval":
            graph_edge_max = max(graph_edge_max, int(event.get("edge_count") or 0))
        if event.get("cost_role") == "rollup":
            estimated_cost += float(event.get("exclusive_estimated_cost") or 0)
        elif event.get("estimated_cost") is not None:
            estimated_cost += float(event.get("estimated_cost") or 0)
        if event.get("event") == "corrective_retrieval" and event.get("attempt"):
            corrective_attempts += 1
            correction_trace_id = str(event.get("trace_id") or "<missing>")
            corrections_by_trace[correction_trace_id] += 1
            if event.get("error"):
                correction_errors += 1
            else:
                successful_corrections_by_trace[correction_trace_id] += 1
        if event.get("event") == "claim_repair" and event.get("attempted"):
            repair_attempts += 1
            repairs_by_trace[str(event.get("trace_id") or "<missing>")] += 1
        if event.get("event") == "llm_retry":
            llm_retries += 1
            retries_by_trace[str(event.get("trace_id") or "<missing>")] += 1
        if event.get("event") == "external_ai_call":
            surface = str(event.get("surface") or "unknown")
            try:
                latency = max(0.0, float(event.get("latency_ms") or 0.0))
            except (TypeError, ValueError):
                latency = 0.0
            external_ai_latencies[surface].append(latency)
            external_ai_statuses[surface][str(event.get("status") or "unknown")] += 1
        provider = str(event.get("backend") or "")
        if event.get("event") == "rerank" and provider in RERANK_PROVIDERS:
            rerank_statuses[provider][str(event.get("status") or "unknown")] += 1
            rerank_fallbacks[provider] += int(bool(event.get("fallback")))
            if event.get("provider_status_code") is not None:
                rerank_status_codes[provider][str(event.get("provider_status_code"))] += 1
            rerank_retry_attempts[provider] += int(bool(event.get("retry_attempted")))
        if event.get("event") != "rag_end":
            continue
        query_count += 1
        if event.get("ts"):
            included_timestamps.append(event["ts"])
        if event.get("final_latency_ms") is not None:
            query_latencies.append(float(event["final_latency_ms"]))
        if not event.get("refusal"):
            continue
        reason = event.get("refusal_reason")
        if not reason and event.get("reason"):
            reason = event.get("reason")
            legacy_reason_events += 1
        if not reason or reason in excluded:
            continue
        counts[str(reason)] += 1
    ordered_latencies = sorted(query_latencies)
    external_ai_latency = {}
    for surface, values in sorted(external_ai_latencies.items()):
        ordered = sorted(values)
        statuses = external_ai_statuses[surface]
        external_ai_latency[surface] = {
            "call_count": len(ordered),
            "success_count": statuses.get("success", 0),
            "error_count": statuses.get("error", 0),
            "cancelled_count": statuses.get("cancelled", 0),
            "unknown_count": sum(
                count
                for status, count in statuses.items()
                if status not in {"success", "error", "cancelled"}
            ),
            "latency_p50_ms": nearest_rank(ordered, 0.50),
            "latency_p95_ms": nearest_rank(ordered, 0.95),
            "latency_max_ms": max(ordered, default=None),
        }
    rerank_by_provider = {}
    for provider in RERANK_PROVIDERS:
        statuses = rerank_statuses[provider]
        calls = sum(statuses.values())
        fallbacks = rerank_fallbacks[provider]
        rerank_by_provider[provider] = {
            "call_count": calls,
            "success_count": statuses.get("success", 0),
            "error_count": statuses.get("error", 0),
            "fallback_count": fallbacks,
            "error_rate": statuses.get("error", 0) / calls if calls else 0.0,
            "fallback_rate": fallbacks / calls if calls else 0.0,
            "status_codes": dict(sorted(rerank_status_codes[provider].items())),
            "retry_attempt_count": rerank_retry_attempts[provider],
        }
    return {
        "schema": "rag-refusal-snapshot-v1",
        "source": {
            "path": str(path.resolve()),
            "git_sha": _git_sha(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
        "filters": {
            "start": start,
            "end": end,
            "execution_contexts": sorted(contexts),
            "excluded_reasons": sorted(excluded),
            "exclude_empty_reason": True,
        },
        "observed_range": {
            "first": min(included_timestamps, default=None),
            "last": max(included_timestamps, default=None),
        },
        "denominator": sum(counts.values()),
        "refusal_reasons": dict(sorted(counts.items())),
        "parse_errors": parse_errors,
        "legacy_reason_events": legacy_reason_events,
        "event_counts": dict(sorted(event_counts.items())),
        "error_event_count": sum(error_events.values()),
        "error_events": dict(sorted(error_events.items())),
        "fallback_event_count": sum(fallback_events.values()),
        "fallback_events": dict(sorted(fallback_events.items())),
        "retry_event_count": sum(retry_events.values()),
        "retry_events": dict(sorted(retry_events.items())),
        "external_ai_latency": external_ai_latency,
        "rerank_by_provider": rerank_by_provider,
        "voyage_rerank": rerank_by_provider["voyage"],
        "system_metrics": {
            "query_count": query_count,
            "latency_p50_ms": nearest_rank(ordered_latencies, 0.50),
            "latency_p95_ms": nearest_rank(ordered_latencies, 0.95),
            "estimated_cost": round(estimated_cost, 8),
            "correction_rate": corrective_attempts / query_count if query_count else 0.0,
            "repair_rate": repair_attempts / query_count if query_count else 0.0,
            "max_corrections_per_query": max(corrections_by_trace.values(), default=0),
            "max_repairs_per_query": max(repairs_by_trace.values(), default=0),
            "correction_trace_ids": sorted(successful_corrections_by_trace),
            "correction_error_count": correction_errors,
            "repair_trace_ids": sorted(repairs_by_trace),
            "retry_rate": llm_retries / query_count if query_count else 0.0,
        },
        "observed_budget_metrics": {
            "max_planner_count": planner_max,
            "max_subquery_count": subquery_max,
            "max_correction_count": max(corrections_by_trace.values(), default=0),
            "max_repair_count": max(repairs_by_trace.values(), default=0),
            "max_calculation_count": calculation_max,
            "max_graph_edge_count": graph_edge_max,
            "max_provider_retries": max(retries_by_trace.values(), default=0),
        },
    }


def render_markdown(report: dict) -> str:
    rerank_by_provider = report.get("rerank_by_provider") or {
        "voyage": report.get("voyage_rerank", {})
    }
    lines = [
        "# RAG refusal snapshot",
        "",
        f"- Commit: `{report['source']['git_sha']}`",
        f"- Source: `{report['source']['path']}`",
        f"- Denominator: {report['denominator']}",
        f"- Observed range: `{report['observed_range']['first']}` to `{report['observed_range']['last']}`",
        f"- Filters: `{json.dumps(report['filters'], ensure_ascii=False)}`",
        *[
            f"- {provider.title()} rerank: "
            f"`{json.dumps(metrics, ensure_ascii=False)}`"
            for provider, metrics in rerank_by_provider.items()
        ],
        "",
        "| Refusal reason | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {reason} | {count} |"
        for reason, count in report["refusal_reasons"].items()
    )
    lines.extend([
        "",
        "## External AI latency",
        "",
        "| Surface | Calls | Success | Error | Cancelled | Unknown | P50 ms | P95 ms | Max ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    lines.extend(
        "| {surface} | {call_count} | {success_count} | {error_count} | "
        "{cancelled_count} | {unknown_count} | "
        "{latency_p50_ms} | {latency_p95_ms} | {latency_max_ms} |".format(
            surface=surface,
            **metrics,
        )
        for surface, metrics in report.get("external_ai_latency", {}).items()
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--context", action="append", dest="contexts")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    report = build_snapshot(
        args.path,
        start=args.start,
        end=args.end,
        execution_contexts=set(args.contexts or ["production"]),
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json_output:
        args.json_output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    if args.markdown_output:
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
