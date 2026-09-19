#!/usr/bin/env python
"""Run a bounded, rerank-only Voyage/Jina comparison."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mech_chatbot.composition.maintenance_runtime import configured_repository_runtime
from mech_chatbot.config.settings import ExternalAiSettings, load_settings
from mech_chatbot.evaluation.rerank_provider import (
    ManifestError,
    canonical_sha256,
    evaluate_gate,
    load_manifest,
    run_provider_ab,
)
from mech_chatbot.llm.external_ai import get_provider_runtime
from mech_chatbot.rag.rerank import (
    jina_rerank_documents,
    voyage_rerank_documents,
)


_APPROVED_MANIFEST_SHA256 = (
    "752d573997b525e06d95553fcf8657a420cadcc1ecdbb7b5b0d23531cbb2fb70"
)


def _assert_approved_manifest(cases) -> None:
    if canonical_sha256(cases) != _APPROVED_MANIFEST_SHA256:
        raise ManifestError(
            "real provider mode only accepts the predeclared governed manifest"
        )


@dataclass
class _Document:
    page_content: str
    metadata: dict[str, Any]


def _real_provider_calls(settings):
    external_settings = ExternalAiSettings.from_settings(settings)
    specifications = {
        "voyage": {
            "endpoint": "https://api.voyageai.com/v1",
            "model": settings.VOYAGE_RERANK_MODEL,
            "secret": "VOYAGE_API_KEY",
            "value": settings.VOYAGE_API_KEY,
            "timeout": settings.VOYAGE_RERANK_TIMEOUT_SECONDS,
            "adapter": voyage_rerank_documents,
        },
        "jina": {
            "endpoint": "https://api.jina.ai/v1",
            "model": settings.JINA_RERANK_MODEL,
            "secret": "JINA_API_KEY",
            "value": settings.JINA_API_KEY,
            "timeout": settings.JINA_RERANK_TIMEOUT_SECONDS,
            "adapter": jina_rerank_documents,
        },
    }
    calls = {}
    metadata = {}
    for provider, specification in specifications.items():
        runtime = get_provider_runtime(
            provider,
            fallback_endpoint=specification["endpoint"],
            fallback_model=specification["model"],
            settings=external_settings,
            resolved_secrets={
                specification["secret"]: specification["value"]
            },
        )
        metadata[provider] = {
            "provider": provider,
            "model": runtime.model,
            "profile_sha256": canonical_sha256(
                {
                    "provider": runtime.profile.provider,
                    "endpoint": runtime.profile.endpoint,
                    "model": runtime.profile.default_model,
                    "allowed_surfaces": runtime.profile.allowed_surfaces,
                    "retention_mode": runtime.profile.retention_mode,
                    "policy_version": runtime.profile.policy_version,
                    "risk_acceptance_ref": runtime.profile.risk_acceptance_ref,
                    "review_expires_at": (
                        runtime.profile.review_expires_at.isoformat()
                        if runtime.profile.review_expires_at
                        else None
                    ),
                    "is_active": runtime.profile.is_active,
                }
            ),
        }

        def call(
            case_id,
            query,
            candidates,
            *,
            _adapter=specification["adapter"],
            _runtime=runtime,
            _timeout=specification["timeout"],
        ):
            documents = [
                _Document(
                    page_content=str(candidate["text"]),
                    metadata={
                        "eval_candidate_id": candidate["candidate_id"],
                        "noi_dung_goc": candidate["text"],
                        "external_processing_policy": "all_external",
                        "security_level": "internal",
                        "servable": True,
                    },
                )
                for candidate in candidates
            ]
            return _adapter(
                documents,
                query,
                top_n=len(documents),
                trace_id=f"rerank-ab-{case_id}",
                runtime=_runtime,
                timeout_seconds=_timeout,
            )

        calls[provider] = call
    return calls, metadata


def _rate_limiter(
    *,
    minimum_interval_seconds: float,
    monotonic=time.monotonic,
    sleeper=time.sleep,
):
    last_started: float | None = None

    def wait():
        nonlocal last_started
        now = monotonic()
        if last_started is not None:
            sleeper(max(0.0, minimum_interval_seconds - (now - last_started)))
        last_started = monotonic()

    return wait


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Voyage and Jina on one governed candidate set. "
            "This evaluation never authorizes release."
        )
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/rerank_provider_eval_v1/manifest.jsonl"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    parser.add_argument("--max-cases", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--quality-margin", type=float, default=0.0)
    parser.add_argument("--max-latency-ratio", type=float, default=1.25)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_calls: Mapping[str, Any] | None = None,
    clock=time.perf_counter,
) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 1 <= args.max_cases <= 3:
        parser.error("--max-cases must be between 1 and 3 for Voyage 3 RPM")
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    if args.quality_margin < 0:
        parser.error("--quality-margin cannot be negative")
    if args.max_latency_ratio <= 0:
        parser.error("--max-latency-ratio must be positive")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")

    manifest_cases = load_manifest(args.manifest)
    if provider_calls is None:
        _assert_approved_manifest(manifest_cases)
    cases = manifest_cases[: args.max_cases]
    settings = load_settings(args.dotenv) if provider_calls is None else None
    runtime_context = (
        configured_repository_runtime(settings, include_qdrant=False)
        if settings is not None
        else nullcontext()
    )
    with runtime_context:
        if provider_calls is None:
            provider_calls, provider_metadata = _real_provider_calls(settings)
            before_provider_call = {
                "voyage": _rate_limiter(
                    minimum_interval_seconds=21.0,
                )
            }
        else:
            before_provider_call = None
            provider_metadata = {
                provider: {
                    "provider": provider,
                    "model": "injected-test-boundary",
                }
                for provider in ("voyage", "jina")
            }
        report = run_provider_ab(
            cases,
            providers=provider_calls,
            top_k=args.top_k,
            provider_metadata=provider_metadata,
            before_provider_call=before_provider_call,
            clock=clock,
        )
    gate = evaluate_gate(
        report,
        quality_margin=args.quality_margin,
        max_latency_ratio=args.max_latency_ratio,
    )
    artifact = {
        "schema": "rerank-provider-ab-artifact-v1",
        "report": report,
        "gate": gate,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            artifact,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if gate["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
