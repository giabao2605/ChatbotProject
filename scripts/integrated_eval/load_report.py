"""Join SSE load latency with labeled-eval cost, retry and fallback metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _select_summary(benchmark: dict, concurrency: int) -> dict:
    if benchmark.get("schema") != "rag-concurrency-benchmark-v1":
        raise ValueError("benchmark schema must be rag-concurrency-benchmark-v1")
    results = benchmark.get("results")
    if not isinstance(results, list):
        raise ValueError("benchmark must contain results[].summary")
    matches = [
        item.get("summary") for item in results
        if isinstance(item, dict)
        and isinstance(item.get("summary"), dict)
        and item["summary"].get("concurrency") == concurrency
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one benchmark summary for concurrency {concurrency}"
        )
    return matches[0]


def build_integrated_load_report(
    benchmark: dict, evaluation: dict, *, concurrency: int,
    source_benchmark_sha256=None, source_eval_sha256=None,
) -> dict:
    summary = _select_summary(benchmark, concurrency)
    case_count = int(evaluation.get("case_count") or len(evaluation.get("cases") or []))
    if evaluation.get("schema") != "rag-labeled-eval-v4" or case_count <= 0:
        raise ValueError("evaluation must be a non-empty rag-labeled-eval-v4 artifact")
    return {
        "schema": "integrated-load-report-v1",
        "concurrency": int(summary.get("concurrency") or 0),
        "requests": int(summary.get("requests") or 0),
        "successful_requests": int(summary.get("successful_requests") or 0),
        "first_token_p50_ms": summary.get("first_token_p50_ms"),
        "first_token_p95_ms": summary.get("first_token_p95_ms"),
        "complete_p50_ms": summary.get("complete_p50_ms"),
        "complete_p95_ms": summary.get("complete_p95_ms"),
        "cost_per_query": (
            float(evaluation.get("total_estimated_cost") or 0) / case_count
            if case_count else None
        ),
        "provider_retry_rate": (
            int(evaluation.get("provider_retries") or 0) / case_count
            if case_count else None
        ),
        "fallback_rate": (evaluation.get("fallback_coverage") or {}).get(
            "fallback_rate"
        ),
        "stage_latency": summary.get("stage_latency") or {},
        "source_benchmark_sha256": source_benchmark_sha256,
        "source_eval_sha256": source_eval_sha256,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    artifact = build_integrated_load_report(
        json.loads(args.benchmark.read_text(encoding="utf-8")),
        json.loads(args.eval.read_text(encoding="utf-8")),
        concurrency=args.concurrency,
        source_benchmark_sha256=hashlib.sha256(args.benchmark.read_bytes()).hexdigest(),
        source_eval_sha256=hashlib.sha256(args.eval.read_bytes()).hexdigest(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
