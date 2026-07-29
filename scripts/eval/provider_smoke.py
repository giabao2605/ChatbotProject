"""Run a metadata-only provider capacity smoke before live evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, TypedDict

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for value in (ROOT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from mech_chatbot.evaluation.milestone_decisions import classify_provider_outcome
from mech_chatbot.config.settings import Settings
from mech_chatbot.governance.provider_smoke import (
    provider_smoke_artifact_valid,
    provider_smoke_fresh_for_arms,
)


_SMOKE_MESSAGES = [
    ("system", "Return only the word OK."),
    ("human", "Provider readiness probe."),
]


class ProviderConfiguration(TypedDict):
    endpoint: str
    model: str
    max_concurrent_rag: int


class _ProviderAdapterSettings(Protocol):
    base_url: str
    model_name: str


class _ProviderAdapter(Protocol):
    settings: _ProviderAdapterSettings

    def invoke(self, *args: Any, **kwargs: Any) -> object: ...


def _root_exception(exc: Exception) -> Exception:
    last_attempt = getattr(exc, "last_attempt", None)
    exception = getattr(last_attempt, "exception", None)
    if callable(exception):
        root = exception()
        if isinstance(root, Exception):
            return root
    return exc


def _status_code(exc: Exception) -> int | None:
    direct = getattr(exc, "status_code", None)
    try:
        if direct is not None:
            return int(direct)
    except (TypeError, ValueError):
        pass
    match = re.search(r"\b([1-5]\d\d)\b", str(exc))
    return int(match.group(1)) if match else None


def _error_category(exc: Exception) -> str:
    message = str(exc).casefold()
    status = _status_code(exc)
    if "no_capacity" in message or (
        status == 503 and "service_unavailable" in message
    ):
        return "capacity"
    if status in {401, 403}:
        return "authentication_or_authorization"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if status is not None:
        return "http_error"
    return "other"


def resolve_provider_configuration(
    settings: Settings,
    adapter: _ProviderAdapter | None = None,
) -> ProviderConfiguration:
    """Resolve provider identity from one explicit process settings snapshot."""
    from mech_chatbot.config.settings import LlmSettings
    from mech_chatbot.llm.llm_client import get_llm_endpoint

    llm_settings = LlmSettings.from_settings(settings)
    endpoint = (
        str(adapter.settings.base_url).strip()
        if adapter is not None
        else (
            str(llm_settings.base_url).strip()
            if llm_settings.base_url
            else get_llm_endpoint()
        )
    )
    model = (
        str(adapter.settings.model_name).strip()
        if adapter is not None
        else str(llm_settings.model_name).strip()
    )

    return {
        "endpoint": endpoint,
        "model": model,
        "max_concurrent_rag": settings.MAX_CONCURRENT_RAG,
    }


def provider_configuration_sha256(
    configuration: ProviderConfiguration,
) -> str:
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True).encode("utf-8")
    ).hexdigest()


def provider_configuration_sha256_for_settings(settings: Settings) -> str:
    """Hash the normalized provider identity from one settings snapshot."""

    return provider_configuration_sha256(
        resolve_provider_configuration(settings)
    )


def provider_environment_for_settings(settings: Settings) -> dict[str, str]:
    configuration = resolve_provider_configuration(settings)
    return {
        "PROXYLLM_API_KEY": str(settings.LLM_API_KEY or ""),
        "PROXYLLM_BASE_URL": configuration["endpoint"],
        "GPT_MODEL_NAME": configuration["model"],
        "MAX_CONCURRENT_RAG": str(configuration["max_concurrent_rag"]),
    }


def validate_provider_smoke_artifact(
    path: str | Path,
    *,
    expected_provider_sha256: str,
) -> dict[str, object]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if not provider_smoke_artifact_valid(
        artifact,
        expected_provider_sha256=expected_provider_sha256,
    ):
        raise ValueError("provider smoke artifact is invalid")
    return artifact


def validate_provider_smoke_for_baseline(
    path: str | Path,
    *,
    expected_provider_sha256: str,
    baseline_started_at: object,
) -> dict[str, object]:
    return validate_provider_smoke_for_arms(
        path,
        expected_provider_sha256=expected_provider_sha256,
        arm_started_at=(baseline_started_at,),
    )


def validate_provider_smoke_for_arms(
    path: str | Path,
    *,
    expected_provider_sha256: str,
    arm_started_at: tuple[object, ...],
) -> dict[str, object]:
    artifact = validate_provider_smoke_artifact(
        path,
        expected_provider_sha256=expected_provider_sha256,
    )
    if not provider_smoke_fresh_for_arms(
        artifact,
        arm_started_at=arm_started_at,
    ):
        raise ValueError("provider smoke artifact is older than 30 minutes")
    return artifact


def _percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def run_provider_smoke(
    invoke: Callable[..., object],
    *,
    request_count: int = 5,
) -> dict[str, object]:
    if request_count != 5:
        raise ValueError("controlled-demo provider smoke requires exactly five requests")
    latencies = []
    failures = []
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
            root = _root_exception(exc)
            failures.append({
                "classification_text": str(root),
                "error_type": type(exc).__name__,
                "root_error_type": type(root).__name__,
                "status_code": _status_code(root),
                "error_category": _error_category(root),
            })
        finally:
            retry_total += int(retry_counter.get("count") or 0)
            latencies.append((time.perf_counter() - started) * 1000)
    provider_outcome = classify_provider_outcome(
        [failure["classification_text"] for failure in failures]
    )
    passed = successful == request_count and retry_total == 0
    return {
        "schema": "provider-smoke-v1",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "request_count": request_count,
        "successful_requests": successful,
        "failed_requests": len(failures),
        "provider_retries": retry_total,
        "latency_p50_ms": statistics.median(latencies) if latencies else 0.0,
        "latency_p95_ms": _percentile(latencies, 0.95),
        "error_types": [failure["error_type"] for failure in failures],
        "root_error_types": [failure["root_error_type"] for failure in failures],
        "status_codes": [failure["status_code"] for failure in failures],
        "error_categories": [failure["error_category"] for failure in failures],
        "provider_outcome": provider_outcome,
        "passed": passed,
    }


def run_configured_provider_smoke(
    settings: Settings,
    *,
    adapter_builder: Callable[..., _ProviderAdapter] | None = None,
) -> tuple[dict[str, object], ProviderConfiguration]:
    """Compose and run the smoke from one immutable CLI settings snapshot."""
    from mech_chatbot.config.settings import ExternalAiSettings, LlmSettings
    from mech_chatbot.llm.llm_client import build_llm_adapter

    builder = adapter_builder or build_llm_adapter
    adapter = builder(
        LlmSettings.from_settings(settings),
        external_ai_settings=ExternalAiSettings.from_settings(settings),
    )
    configuration = resolve_provider_configuration(settings, adapter)
    return run_provider_smoke(adapter.invoke), configuration


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from mech_chatbot.composition.maintenance_runtime import (
        configured_repository_runtime,
    )
    from mech_chatbot.config.settings import load_settings

    settings = load_settings()
    with configured_repository_runtime(settings, include_qdrant=False):
        artifact, configuration = run_configured_provider_smoke(settings)
    artifact["model"] = configuration["model"]
    artifact["provider_configuration_sha256"] = provider_configuration_sha256(
        configuration
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if artifact["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
