"""Measure RAG latency at a safe set of concurrency levels.

The tool only sends operator-supplied evaluation questions to the endpoint.  It
never changes ``MAX_CONCURRENT_RAG`` or writes any source corpus.  Reports
contain question hashes instead of raw prompts and never contain service tokens.

When the RAG server exposes a ``trace_stages`` object in an SSE event (normally
the ``done`` payload), the tool reports P50/P95 per stage.  It can also derive
the same aggregation from an operator-supplied ``rag_trace.jsonl`` file.  The
tool matches JSONL events to benchmark trace IDs when available, aggregates
each request/stage once, and never mixes SSE and JSONL values into one stage
distribution.
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


def load_benchmark_cases(
    path: Path, *, default_username: str | None = None,
) -> list[dict[str, Any]]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            parsed = {"question": line}
        if not isinstance(parsed, dict) or not str(parsed.get("question") or "").strip():
            continue
        case = dict(parsed)
        case["question"] = str(case["question"]).strip()
        if not case.get("username") and case.get("user_id") is None and default_username:
            case["username"] = default_username
        if not case.get("username") and case.get("user_id") is None:
            raise ValueError(
                f"Benchmark case {case.get('id') or '<missing>'} requires username or user_id"
            )
        cases.append(case)
    if not cases:
        raise ValueError("Question file khong co cau hoi hop le")
    return cases


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


def build_chat_payload(case: dict[str, Any]) -> dict[str, Any]:
    payload = {"user_question": str(case.get("question") or "")}
    if case.get("user_id") is not None:
        payload["user_id"] = case["user_id"]
    if case.get("username"):
        payload["username"] = case["username"]
    for field in (
        "user_department", "user_roles", "allowed_departments", "allowed_sites",
        "max_security_level",
    ):
        if field in case:
            payload[field] = case[field]
    return payload


def _sample_identity(case: dict[str, Any], sample_index: int) -> dict[str, Any]:
    return {"sample_id": _question_id(str(case.get("question") or ""), sample_index)}


def _redact_report_sample(sample: dict[str, Any]) -> dict[str, Any]:
    if "trace_id" not in sample:
        return dict(sample)
    return {
        **sample,
        "trace_id": "<redacted>" if sample.get("trace_id") else None,
    }


def _consume_stream(
    response: Any, started: float,
) -> tuple[int | None, dict[str, Any], dict[str, float], bool]:
    first_token_ms: int | None = None
    server_done: dict[str, Any] = {}
    stage_metrics: dict[str, float] = {}
    for event, payload in _iter_sse_events(response):
        if event in {"token", "delta"} and first_token_ms is None:
            first_token_ms = int((time.perf_counter() - started) * 1000)
        stage_metrics = {
            **stage_metrics,
            **extract_trace_stages(payload),
        }
        if event == "done":
            server_done = payload
        if event == "error":
            return first_token_ms, server_done, stage_metrics, True
    return first_token_ms, server_done, stage_metrics, False


def measure_one(
    base_url: str, token: str, case: dict[str, Any], timeout: int, *, sample_index: int = 0,
) -> dict[str, Any]:
    """Measure one request without returning prompt or server-error text."""
    started = time.perf_counter()
    first_token_ms: int | None = None
    stage_metrics: dict[str, float] = {}
    headers = {"X-RAG-Service-Token": token} if token else {}
    sample: dict[str, Any] = _sample_identity(case, sample_index)
    response = None
    try:
        response = requests.post(
            base_url.rstrip("/") + "/chat/stream",
            headers=headers,
            json=build_chat_payload(case),
            stream=True,
            timeout=(10, timeout),
        )
        response.raise_for_status()
        first_token_ms, server_done, stage_metrics, stream_error = (
            _consume_stream(response, started)
        )
        if stream_error:
            raise RuntimeError("RAG stream emitted an error event")
        return {
            **sample,
            "ok": True,
            "first_token_ms": first_token_ms,
            "complete_ms": int((time.perf_counter() - started) * 1000),
            "server_first_token_ms": _as_latency_ms(server_done.get("first_token_ms")),
            "server_complete_ms": _as_latency_ms(server_done.get("elapsed_ms")),
            "trace_id": str(server_done.get("trace_id") or "") or None,
            "stage_metrics": stage_metrics,
        }
    except Exception as exc:
        return {
            **sample,
            "ok": False,
            "first_token_ms": first_token_ms,
            "complete_ms": int((time.perf_counter() - started) * 1000),
            "error_type": type(exc).__name__,
            "stage_metrics": stage_metrics,
        }
    finally:
        try:
            if response is not None:
                response.close()
        except Exception:
            pass


def percentile(values: list[int | float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return float(ordered[index])


def _summarize_stage_values(
    values_by_stage: dict[str, list[float]],
) -> dict[str, dict[str, float | int | None]]:
    return {
        stage: {
            "samples": len(values),
            "p50_ms": percentile(values, 0.50),
            "p95_ms": percentile(values, 0.95),
        }
        for stage, values in sorted(values_by_stage.items())
        if values
    }


def _trace_source_name(trace_metadata: dict[str, Any] | None) -> str:
    if (trace_metadata or {}).get("correlation") == "exact_trace_id":
        return "trace_jsonl_exact_trace_id"
    return "trace_jsonl_time_window"


def _collect_sse_stage_values(
    samples: list[dict[str, Any]],
) -> dict[str, list[float]]:
    pairs = [
        (str(stage), latency)
        for sample in samples
        if isinstance(sample.get("stage_metrics"), dict)
        for stage, raw_value in sample["stage_metrics"].items()
        if (latency := _as_latency_ms(raw_value)) is not None
    ]
    return {
        stage: [latency for name, latency in pairs if name == stage]
        for stage in sorted({name for name, _latency in pairs})
    }


def _normalise_trace_stage_values(
    trace_stage_metrics: dict[str, list[float]] | None,
) -> dict[str, list[float]]:
    normalised = {
        str(stage): [
            latency
            for raw_value in values
            if (latency := _as_latency_ms(raw_value)) is not None
        ]
        for stage, values in (trace_stage_metrics or {}).items()
    }
    return {stage: values for stage, values in normalised.items() if values}


def summarize_stage_metrics(
    samples: list[dict[str, Any]],
    *,
    trace_stage_metrics: dict[str, list[float]] | None = None,
    trace_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    sse_by_stage = _collect_sse_stage_values(samples)
    trace_by_stage = _normalise_trace_stage_values(trace_stage_metrics)
    if not sse_by_stage and not trace_by_stage and not trace_metadata:
        return None

    trace_source = _trace_source_name(trace_metadata)
    sources = (
        (["sse_trace_stages"] if sse_by_stage else [])
        + ([trace_source] if trace_by_stage else [])
    )
    source_summaries = {
        **(
            {"sse_trace_stages": _summarize_stage_values(sse_by_stage)}
            if sse_by_stage else {}
        ),
        **(
            {trace_source: _summarize_stage_values(trace_by_stage)}
            if trace_by_stage else {}
        ),
    }
    stages = {
        stage: {
            "source": source,
            **source_summaries[source][stage],
        }
        for stage in sorted(set(sse_by_stage) | set(trace_by_stage))
        for source in [
            "sse_trace_stages" if stage in sse_by_stage else trace_source
        ]
    }
    return {
        "sources": sources,
        "selection": "prefer_sse_per_stage",
        "stages": stages,
        "source_summaries": source_summaries,
        **({"trace_jsonl": trace_metadata} if trace_metadata else {}),
    }


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


def _merge_trace_stage_value(
    trace_stages: dict[str, float], stage: str, latency: float,
) -> dict[str, float]:
    current = trace_stages.get(stage)
    if stage == "cache":
        value = (current or 0.0) + latency
    elif stage == "embed":
        value = max(current or 0.0, latency)
    else:
        value = latency
    return {**trace_stages, stage: value}


def _flatten_trace_stage_metrics(
    metrics_by_trace: dict[str, dict[str, float]],
) -> dict[str, list[float]]:
    stages = sorted({
        stage
        for trace_metrics in metrics_by_trace.values()
        for stage in trace_metrics
    })
    return {
        stage: [
            trace_metrics[stage]
            for trace_metrics in metrics_by_trace.values()
            if stage in trace_metrics
        ]
        for stage in stages
    }


def _trace_event_stages(
    event: dict[str, Any],
    *,
    started_at: datetime | None,
    finished_at: datetime | None,
) -> dict[str, float]:
    timestamp = _parse_timestamp(event.get("ts"))
    if started_at and (timestamp is None or timestamp < started_at):
        return {}
    if finished_at and (timestamp is None or timestamp > finished_at):
        return {}
    stages = extract_trace_stages(event)
    event_name = _normalise_stage_name(event.get("event"))
    event_latency = _as_latency_ms(event.get("latency_ms"))
    if not event_name or event_latency is None or event_name in stages:
        return stages
    return {**stages, event_name: event_latency}


def _with_trace_event(
    metrics_by_trace: dict[str, dict[str, float]],
    trace_key: str,
    stages: dict[str, float],
) -> dict[str, dict[str, float]]:
    trace_metrics = metrics_by_trace.get(trace_key, {})
    for stage, latency in stages.items():
        trace_metrics = _merge_trace_stage_value(
            trace_metrics, stage, latency,
        )
    return {**metrics_by_trace, trace_key: trace_metrics}


def _trace_correlation(allowed_trace_ids: set[str] | None) -> str:
    if allowed_trace_ids is None:
        return "time_window_only"
    if allowed_trace_ids:
        return "exact_trace_id"
    return "unavailable_missing_trace_ids"


def _decode_trace_line(
    line: str,
) -> tuple[dict[str, Any] | None, bool]:
    if not line.strip():
        return None, False
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(event, dict):
        return None, True
    return event, False


def read_trace_jsonl(
    path: Path,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    allowed_trace_ids: set[str] | None = None,
) -> tuple[dict[str, list[float]], dict[str, Any]]:
    """Read only numeric stage latencies from a trace file, never prompts."""
    metrics_by_trace: dict[str, dict[str, float]] = {}
    trace_ids: set[str] = set()
    parsed_events = 0
    ignored_lines = 0
    excluded_trace_events = 0
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        event, malformed = _decode_trace_line(line)
        if malformed:
            ignored_lines = ignored_lines + 1
        if event is None:
            continue
        stages = _trace_event_stages(
            event, started_at=started_at, finished_at=finished_at,
        )
        if not stages:
            continue
        trace_id = event.get("trace_id")
        trace_id = str(trace_id) if trace_id else ""
        if allowed_trace_ids is not None and trace_id not in allowed_trace_ids:
            excluded_trace_events = excluded_trace_events + 1
            continue
        parsed_events = parsed_events + 1
        if trace_id:
            trace_ids = trace_ids | {trace_id}
        trace_key = trace_id or f"event:{line_number}"
        metrics_by_trace = _with_trace_event(
            metrics_by_trace, trace_key, stages,
        )
    metadata = {
        "source": "<trace-jsonl>",
        "correlation": _trace_correlation(allowed_trace_ids),
        "events_with_metrics": parsed_events,
        "trace_count": len(trace_ids),
        "excluded_trace_events": excluded_trace_events,
        "ignored_malformed_lines": ignored_lines,
    }
    return _flatten_trace_stage_metrics(metrics_by_trace), metadata


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure RAG latency only; no configuration or corpus is changed."
    )
    parser.add_argument("questions", type=Path, help="JSONL or one-question-per-line evaluation input")
    parser.add_argument("--base-url", default=os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8100"))
    parser.add_argument("--token-env", default="RAG_SERVICE_TOKEN")
    parser.add_argument("--username", default=os.getenv("RAG_BENCHMARK_USERNAME"))
    parser.add_argument("--concurrency", default="1,5,10", help="CSV levels (default: 1,5,10)")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--trace-jsonl", type=Path, default=None, help="Optional rag_trace.jsonl for stage P50/P95")
    parser.add_argument("--report", type=Path, default=Path("reports/rag_concurrency_benchmark.json"))
    return parser


def _load_runtime_inputs(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[int]]:
    if args.timeout < 1:
        raise SystemExit("--timeout phai lon hon 0")
    try:
        cases = load_benchmark_cases(args.questions, default_username=args.username)
        levels = parse_concurrency_levels(args.concurrency)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.trace_jsonl is not None and not args.trace_jsonl.is_file():
        raise SystemExit(f"Khong tim thay trace JSONL: {args.trace_jsonl}")
    return cases, levels


def _measure_samples(
    cases: list[dict[str, Any]],
    *,
    level: int,
    args: argparse.Namespace,
    token: str,
) -> list[dict[str, Any]]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=level) as executor:
        futures = [
            executor.submit(
                measure_one,
                args.base_url,
                token,
                case,
                args.timeout,
                sample_index=index,
            )
            for index, case in enumerate(cases, start=1)
        ]
        return [future.result() for future in futures]


def _sample_trace_scope(samples: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [sample for sample in samples if sample.get("ok")]
    provided = [
        str(sample["trace_id"])
        for sample in successful
        if sample.get("trace_id")
    ]
    unique = set(provided)
    complete = (
        bool(successful)
        and len(provided) == len(successful)
        and len(unique) == len(provided)
    )
    return {
        "allowed_trace_ids": unique if complete else set(),
        "sample_ids_complete": complete,
        "expected_trace_count": len(successful),
        "provided_trace_count": len(provided),
        "unique_trace_count": len(unique),
    }


def _level_trace_correlation(
    scope: dict[str, Any], trace_count: int,
) -> str:
    if (
        scope["sample_ids_complete"]
        and trace_count == scope["expected_trace_count"]
    ):
        return "exact_trace_id"
    if scope["sample_ids_complete"]:
        return "unavailable_incomplete_trace_events"
    if not scope["provided_trace_count"]:
        return "unavailable_missing_trace_ids"
    return "unavailable_incomplete_trace_ids"


def _read_level_trace(
    args: argparse.Namespace,
    samples: list[dict[str, Any]],
    *,
    started_at: datetime,
    finished_at: datetime,
) -> tuple[dict[str, list[float]] | None, dict[str, Any] | None]:
    if args.trace_jsonl is None:
        return None, None
    scope = _sample_trace_scope(samples)
    trace_metrics, trace_metadata = read_trace_jsonl(
        args.trace_jsonl,
        started_at=started_at,
        finished_at=finished_at,
        allowed_trace_ids=scope["allowed_trace_ids"],
    )
    correlation = _level_trace_correlation(
        scope, trace_metadata["trace_count"],
    )
    return (trace_metrics if correlation == "exact_trace_id" else {}), {
        **trace_metadata,
        "correlation": correlation,
        "expected_trace_count": scope["expected_trace_count"],
        "provided_trace_count": scope["provided_trace_count"],
        "unique_trace_count": scope["unique_trace_count"],
    }


def _measure_level(
    cases: list[dict[str, Any]],
    *,
    level: int,
    args: argparse.Namespace,
    token: str,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc)
    samples = _measure_samples(cases, level=level, args=args, token=token)
    finished_at = datetime.now(timezone.utc)
    trace_metrics, trace_metadata = _read_level_trace(
        args, samples, started_at=started_at, finished_at=finished_at,
    )
    return {
        "summary": summarize(
            samples,
            level,
            trace_stage_metrics=trace_metrics,
            trace_metadata=trace_metadata,
        ),
        "samples": [_redact_report_sample(sample) for sample in samples],
    }


def _build_report_payload(
    args: argparse.Namespace,
    cases: list[dict[str, Any]],
    levels: list[int],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "rag-concurrency-benchmark-v1",
        "base_url": _safe_base_url(args.base_url),
        "concurrency_levels": levels,
        "question_count": len(cases),
        "stage_metric_contract": {
            "sse": "done.data.trace_stages.{stage}.latency_ms",
            "trace_jsonl": (
                "event + latency_ms; exact benchmark trace IDs required, "
                "otherwise JSONL stage values are excluded"
            ),
            "selection": (
                "prefer SSE per stage; use trace JSONL only when SSE omits that stage"
            ),
        },
        "results": results,
    }


def _write_report(
    path: Path, payload: dict[str, Any], results: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(
        {
            "report": str(path),
            "summaries": [item["summary"] for item in results],
        },
        ensure_ascii=False,
        indent=2,
    ))


def main() -> int:
    args = _argument_parser().parse_args()
    cases, levels = _load_runtime_inputs(args)
    token = os.getenv(args.token_env, "")
    results = [
        _measure_level(cases, level=level, args=args, token=token)
        for level in levels
    ]
    payload = _build_report_payload(args, cases, levels, results)
    _write_report(args.report, payload, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
