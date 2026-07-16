"""Run a metadata-only provider capacity smoke before live evaluation."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for value in (ROOT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from mech_chatbot.evaluation.milestone_decisions import classify_provider_outcome


_SMOKE_MESSAGES = [
    ("system", "Return only the word OK."),
    ("human", "Provider readiness probe."),
]


def _percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def run_provider_smoke(invoke, *, request_count: int = 5) -> dict:
    if request_count != 5:
        raise ValueError("controlled-demo provider smoke requires exactly five requests")
    latencies = []
    errors = []
    error_types = []
    retry_total = 0
    successful = 0
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for index in range(request_count):
        retry_counter = {"count": 0}
        started = time.perf_counter()
        try:
            invoke(
                _SMOKE_MESSAGES,
                # Exercise the already-approved generation surface. The trace
                # id distinguishes this probe without widening egress policy.
                surface="generation",
                trace_id=f"provider-smoke-{index + 1}",
                retry_counter=retry_counter,
            )
            successful += 1
        except Exception as exc:  # the artifact stores only the class/category
            errors.append(str(exc))
            error_types.append(type(exc).__name__)
        finally:
            retry_total += int(retry_counter.get("count") or 0)
            latencies.append((time.perf_counter() - started) * 1000)
    provider_outcome = classify_provider_outcome(errors)
    passed = successful == request_count and retry_total == 0
    return {
        "schema": "provider-smoke-v1",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "request_count": request_count,
        "successful_requests": successful,
        "failed_requests": len(errors),
        "provider_retries": retry_total,
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "error_types": error_types,
        "provider_outcome": provider_outcome,
        "passed": passed,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from mech_chatbot.llm.llm_client import gpt_invoke, get_llm_model_name

    artifact = run_provider_smoke(gpt_invoke)
    artifact["model"] = get_llm_model_name()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if artifact["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
