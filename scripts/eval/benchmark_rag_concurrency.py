"""Measure RAG latency at a safe set of concurrency levels.

The tool only sends operator-supplied evaluation questions to the endpoint.  It
never changes ``MAX_CONCURRENT_RAG`` or writes any source corpus.  Reports
contain question hashes instead of raw prompts and never contain service tokens.

When the RAG server exposes a ``trace_stages`` object in an SSE event (normally
the ``done`` payload), the tool reports P50/P95 per stage.  It can also derive
the same aggregation from an operator-supplied ``rag_trace.jsonl`` file.  JSONL
metrics are time-window correlated only unless the server supplies a trace ID;
the report records that limitation explicitly.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import requests


DEFAULT_CONCURRENCY = (1, 5, 10)
_STAGE_SUFFIXES = ("_ms", "_latency_ms")


def load_questions(path: Path) -> list[str]:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parsed = json.loads(line)
            question = parsed.get("question") if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            question = line
        if question and str(question).strip():
            values.append(str(question).strip())
    if not values:
        raise ValueError("Question file khong co cau hoi hop le")
    return values


def parse_concurrency_levels(raw: str) -> list[int]:
    levels: list[int] = []
    for item in str(raw or "").split(","):
        value = item.strip()
        if not value:
            continue
        try:
            level = int(value)
        except ValueError as exc:
            raise ValueError(f"Concurrency khong hop le: {value}") from exc
        if level < 1:
            raise ValueError("Concurrency phai lon hon 0")
        if level not in levels:
            levels.append(level)
    if not levels:
        raise ValueError("Can it nhat mot muc concurrency")
    return levels


def _safe_base_url(raw: str) -> str:
    """Do not persist credentials, query strings, or fragments in a report."""
    parts = urlsplit(str(raw or ""))
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


def _as_latency_ms(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        latency = float(value)
    except (TypeError, ValueError):
        return None
    return latency if latency >= 0 else None


def _normalise_stage_name(name: Any) -> str:
    stage = str(name or "").strip()
    for suffix in _STAGE_SUFFIXES:
        if stage.endswith(suffix):
            stage = stage[: -len(suffix)]
    return stage


def extract_trace_stages(payload: Any) -> dict[str, float]:
    """Extract event-backed stage latencies from a future-compatible payload.

    Preferred contract:
    ``{"trace_stages": {"dense_retrieval": {"latency_ms": 12}}}``.
    Numeric ``{"dense_retrieval": 12}`` values are also accepted so a server
    can add telemetry without changing the benchmark first.
    """
    if not isinstance(payload, dict):
        return {}
    candidates: list[dict[str, Any]] = []
    trace_stages = payload.get("trace_stages")
    if isinstance(trace_stages, dict):
        candidates.append(trace_stages)
    debug = payload.get("debug_info")
    if isinstance(debug, dict) and isinstance(debug.get("trace_stages"), dict):
        candidates.append(debug["trace_stages"])
    if isinstance(payload.get("stages"), dict):
        candidates.append(payload["stages"])

    stages: dict[str, float] = {}
    for candidate in candidates:
        for raw_name, raw_metric in candidate.items():
            stage = _normalise_stage_name(raw_name)
            if not stage:
                continue
            if isinstance(raw_metric, dict):
                metric = raw_metric.get("latency_ms", raw_metric.get("ms"))
            else:
                metric = raw_metric
            latency = _as_latency_ms(metric)
            if latency is not None:
                stages[stage] = latency
    return stages


def _iter_sse_events(response: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    event = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line or "")
        if not line:
            if data_lines:
                raw_data = "\n".join(data_lines)
                try:
                    payload = json.loads(raw_data)
                except json.JSONDecodeError:
                    payload = {}
                yield event, payload if isinstance(payload, dict) else {}
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {}
        yield event, payload if isinstance(payload, dict) else {}


def _question_id(question: str, sample_index: int) -> str:
    digest = hashlib.sha256(str(question).encode("utf-8")).hexdigest()[:16]
    return f"q{sample_index:04d}-{digest}"


def measure_one(
    base_url: str,
    token: str,
    question: str,
    timeout: int,
    *,
    sample_index: int = 0,
) -> dict[str, Any]:
    """Measure one request without returning prompt or server-error text."""
    started = time.perf_counter()
    first_token_ms: int | None = None
    server_done: dict[str, Any] = {}
    stage_metrics: dict[str, float] = {}
    headers = {"X-RAG-Service-Token": token} if token else {}
    sample: dict[str, Any] = {"sample_id": _question_id(question, sample_index)}
    try:
        response = requests.post(
            base_url.rstrip("/") + "/chat/stream",
            headers=headers,
            json={"user_question": question},
            stream=True,
            timeout=(10, timeout),
        )
        response.raise_for_status()
        for event, payload in _iter_sse_events(response):
            if event in {"token", "delta"} and first_token_ms is None:
                first_token_ms = int((time.perf_counter() - started) * 1000)
            stage_metrics.update(extract_trace_stages(payload))
            if event == "done":
                server_done = payload
            if event == "error":
                raise RuntimeError("RAG stream emitted an error event")
        sample.update(
            {
                "ok": True,
                "first_token_ms": first_token_ms,
                "complete_ms": int((time.perf_counter() - started) * 1000),
                "server_first_token_ms": _as_latency_ms(server_done.get("first_token_ms")),
                "server_complete_ms": _as_latency_ms(server_done.get("elapsed_ms")),
                "trace_id": str(server_done.get("trace_id") or "") or None,
                "stage_metrics": stage_metrics,
            }
        )
    except Exception as exc:
        sample.update(
            {
                "ok": False,
                "first_token_ms": first_token_ms,
                "complete_ms": int((time.perf_counter() - started) * 1000),
                "error_type": type(exc).__name__,
                "stage_metrics": stage_metrics,
            }
        )
    finally:
        try:
            response.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return sample


def percentile(values: list[int | float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return float(ordered[index])


def summarize_stage_metrics(
    samples: list[dict[str, Any]],
    *,
    trace_stage_metrics: dict[str, list[float]] | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    by_stage: dict[str, list[float]] = {}
    sse_stage_count = 0
    for sample in samples:
        metrics = sample.get("stage_metrics")
        if not isinstance(metrics, dict):
            continue
        for stage, raw_value in metrics.items():
            latency = _as_latency_ms(raw_value)
            if latency is None:
                continue
            by_stage.setdefault(str(stage), []).append(latency)
            sse_stage_count += 1
    trace_stage_metrics = trace_stage_metrics or {}
    for stage, values in trace_stage_metrics.items():
        for raw_value in values:
            latency = _as_latency_ms(raw_value)
            if latency is not None:
                by_stage.setdefault(str(stage), []).append(latency)
    if not by_stage:
        return None

    sources: list[str] = []
    if sse_stage_count:
        sources.append("sse_trace_stages")
    if trace_stage_metrics:
        sources.append("trace_jsonl_time_window")
    result: dict[str, Any] = {
        "sources": sources,
        "stages": {
            stage: {
                "samples": len(values),
                "p50_ms": percentile(values, 0.50),
                "p95_ms": percentile(values, 0.95),
            }
            for stage, values in sorted(by_stage.items())
        },
    }
    if trace_metadata:
        result["trace_jsonl"] = trace_metadata
    return result


def summarize(
    samples: list[dict[str, Any]],
    concurrency: int,
    *,
    trace_stage_metrics: dict[str, list[float]] | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    successful = [item for item in samples if item.get("ok")]
    first_tokens = [item["first_token_ms"] for item in successful if item.get("first_token_ms") is not None]
    complete = [item["complete_ms"] for item in successful if item.get("complete_ms") is not None]
    return {
        "concurrency": concurrency,
        "requests": len(samples),
        "successful_requests": len(successful),
        "failed_requests": len(samples) - len(successful),
        "first_token_p50_ms": percentile(first_tokens, 0.50),
        "first_token_p95_ms": percentile(first_tokens, 0.95),
        "complete_p50_ms": percentile(complete, 0.50),
        "complete_p95_ms": percentile(complete, 0.95),
        "complete_mean_ms": round(statistics.mean(complete), 1) if complete else None,
        "stage_latency": summarize_stage_metrics(
            samples,
            trace_stage_metrics=trace_stage_metrics,
            trace_metadata=trace_metadata,
        ),
    }


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_trace_jsonl(
    path: Path,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> tuple[dict[str, list[float]], dict[str, Any]]:
    """Read only numeric stage latencies from a trace file, never prompts."""
    stage_metrics: dict[str, list[float]] = {}
    trace_ids: set[str] = set()
    parsed_events = 0
    ignored_lines = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            ignored_lines += 1
            continue
        if not isinstance(event, dict):
            ignored_lines += 1
            continue
        timestamp = _parse_timestamp(event.get("ts"))
        if started_at and (timestamp is None or timestamp < started_at):
            continue
        if finished_at and (timestamp is None or timestamp > finished_at):
            continue
        stages = extract_trace_stages(event)
        event_name = _normalise_stage_name(event.get("event"))
        event_latency = _as_latency_ms(event.get("latency_ms"))
        if event_name and event_latency is not None:
            stages.setdefault(event_name, event_latency)
        if not stages:
            continue
        parsed_events += 1
        trace_id = event.get("trace_id")
        if trace_id:
            trace_ids.add(str(trace_id))
        for stage, latency in stages.items():
            stage_metrics.setdefault(stage, []).append(latency)
    metadata = {
        "source": str(path),
        "correlation": "time_window_only",
        "events_with_metrics": parsed_events,
        "trace_count": len(trace_ids),
        "ignored_malformed_lines": ignored_lines,
    }
    return stage_metrics, metadata


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure RAG latency only; no configuration or corpus is changed."
    )
    parser.add_argument("questions", type=Path, help="JSONL or one-question-per-line evaluation input")
    parser.add_argument("--base-url", default=os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8100"))
    parser.add_argument("--token-env", default="RAG_SERVICE_TOKEN")
    parser.add_argument("--concurrency", default="1,5,10", help="CSV levels (default: 1,5,10)")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--trace-jsonl", type=Path, default=None, help="Optional rag_trace.jsonl for stage P50/P95")
    parser.add_argument("--report", type=Path, default=Path("reports/rag_concurrency_benchmark.json"))
    args = parser.parse_args()
    if args.timeout < 1:
        raise SystemExit("--timeout phai lon hon 0")
    try:
        questions = load_questions(args.questions)
        levels = parse_concurrency_levels(args.concurrency)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.trace_jsonl is not None and not args.trace_jsonl.is_file():
        raise SystemExit(f"Khong tim thay trace JSONL: {args.trace_jsonl}")

    token = os.getenv(args.token_env, "")
    results = []
    for level in levels:
        started_at = datetime.now(timezone.utc)
        samples: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=level) as executor:
            futures = [
                executor.submit(
                    measure_one,
                    args.base_url,
                    token,
                    question,
                    args.timeout,
                    sample_index=index,
                )
                for index, question in enumerate(questions, start=1)
            ]
            for future in futures:
                samples.append(future.result())
        finished_at = datetime.now(timezone.utc)
        trace_metrics: dict[str, list[float]] | None = None
        trace_metadata: dict[str, Any] | None = None
        if args.trace_jsonl is not None:
            trace_metrics, trace_metadata = read_trace_jsonl(
                args.trace_jsonl,
                started_at=started_at,
                finished_at=finished_at,
            )
        results.append(
            {
                "summary": summarize(
                    samples,
                    level,
                    trace_stage_metrics=trace_metrics,
                    trace_metadata=trace_metadata,
                ),
                "samples": samples,
            }
        )

    payload = {
        "base_url": _safe_base_url(args.base_url),
        "concurrency_levels": levels,
        "question_count": len(questions),
        "stage_metric_contract": {
            "sse": "done.data.trace_stages.{stage}.latency_ms",
            "trace_jsonl": "event + latency_ms, correlated by benchmark time window only",
        },
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"report": str(args.report), "summaries": [item["summary"] for item in results]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
