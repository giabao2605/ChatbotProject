"""Closed-set, rerank-only A/B evaluation for external providers."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
from statistics import mean
import time
from typing import Any, Callable, Mapping, Sequence

from mech_chatbot.evaluation.metrics import nearest_rank


ProviderCall = Callable[
    [str, str, Sequence[Mapping[str, Any]]],
    Sequence[Any],
]
PROVIDERS = ("voyage", "jina")
_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class ManifestError(ValueError):
    """The rerank-only manifest is incomplete or not a governed closed set."""


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen_cases: set[str] = set()
    for line_number, raw in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ManifestError(
                f"line {line_number}: invalid JSON"
            ) from exc
        case_id = str(case.get("case_id") or "").strip()
        if (
            not _SAFE_ID.fullmatch(case_id)
            or case_id in seen_cases
        ):
            raise ManifestError(
                f"line {line_number}: case_id invalid or duplicate"
            )
        query = str(case.get("query") or "").strip()
        if not query:
            raise ManifestError(f"{case_id}: query is required")
        candidates = case.get("candidates")
        if not isinstance(candidates, list) or len(candidates) < 2:
            raise ManifestError(
                f"{case_id}: candidates must define a closed set of at least 2"
            )
        seen_candidates: set[str] = set()
        relevant = 0
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                raise ManifestError(
                    f"{case_id}: candidate {index} must be an object"
                )
            candidate_id = str(
                candidate.get("candidate_id") or ""
            ).strip()
            if (
                not _SAFE_ID.fullmatch(candidate_id)
                or candidate_id in seen_candidates
            ):
                raise ManifestError(
                    f"{case_id}: candidate_id invalid or duplicate"
                )
            if not str(candidate.get("text") or "").strip():
                raise ManifestError(
                    f"{case_id}:{candidate_id}: text is required"
                )
            if candidate.get("governance_allowed") is not True:
                raise ManifestError(
                    f"{case_id}:{candidate_id}: governance must allow every candidate"
                )
            try:
                relevance = float(candidate.get("relevance", 0))
            except (TypeError, ValueError) as exc:
                raise ManifestError(
                    f"{case_id}:{candidate_id}: relevance must be numeric"
                ) from exc
            if relevance < 0:
                raise ManifestError(
                    f"{case_id}:{candidate_id}: relevance must be non-negative"
                )
            relevant += int(relevance > 0)
            seen_candidates.add(candidate_id)
        if not relevant:
            raise ManifestError(
                f"{case_id}: at least one candidate must be relevant"
            )
        seen_cases.add(case_id)
        cases.append(case)
    if not cases:
        raise ManifestError("manifest is empty")
    return cases


def _candidate_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return str(value.get("candidate_id") or "").strip()
    metadata = getattr(value, "metadata", None)
    if isinstance(metadata, Mapping):
        return str(metadata.get("eval_candidate_id") or "").strip()
    return ""


def _metrics(
    ranked_ids: Sequence[str],
    candidates: Sequence[Mapping[str, Any]],
    top_k: int,
) -> dict[str, float]:
    relevance = {
        str(candidate["candidate_id"]): float(candidate.get("relevance", 0))
        for candidate in candidates
    }
    relevant_count = sum(value > 0 for value in relevance.values())
    observed = [relevance.get(candidate_id, 0.0) for candidate_id in ranked_ids[:top_k]]
    recall = (
        sum(value > 0 for value in observed) / relevant_count
        if relevant_count
        else 0.0
    )
    dcg = sum(
        (2**grade - 1) / math.log2(rank + 2)
        for rank, grade in enumerate(observed)
    )
    ideal = sorted(relevance.values(), reverse=True)[:top_k]
    idcg = sum(
        (2**grade - 1) / math.log2(rank + 2)
        for rank, grade in enumerate(ideal)
    )
    best = max(relevance.values(), default=0.0)
    return {
        f"recall_at_{top_k}": recall,
        f"ndcg_at_{top_k}": dcg / idcg if idcg else 0.0,
        "top1_accuracy": float(
            bool(ranked_ids)
            and best > 0
            and relevance.get(ranked_ids[0], 0.0) == best
        ),
    }


def _safe_provider_metadata(
    provider_metadata: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, dict[str, str]]:
    metadata = provider_metadata or {}
    return {
        provider: {
            key: str(metadata.get(provider, {}).get(key) or "")
            for key in ("provider", "model", "profile_sha256")
        }
        for provider in PROVIDERS
    }


def run_provider_ab(
    cases: Sequence[Mapping[str, Any]],
    *,
    providers: Mapping[str, ProviderCall],
    top_k: int = 3,
    clock: Callable[[], float] = time.perf_counter,
    provider_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    before_provider_call: Mapping[str, Callable[[], None]] | None = None,
) -> dict[str, Any]:
    """Run one predeclared alternating pair per case, with no retry."""
    missing = [provider for provider in PROVIDERS if provider not in providers]
    if missing:
        raise ValueError(
            "provider call missing: " + ", ".join(missing)
        )
    top_k = max(1, int(top_k))
    case_rows: list[dict[str, Any]] = []
    arm_orders: list[list[str]] = []
    provider_rows: dict[str, list[dict[str, Any]]] = {
        provider: [] for provider in PROVIDERS
    }
    for case_index, case in enumerate(cases):
        case_id = str(case["case_id"])
        query = str(case["query"])
        candidates = [dict(candidate) for candidate in case["candidates"]]
        candidate_ids = [
            str(candidate["candidate_id"]) for candidate in candidates
        ]
        input_sha256 = canonical_sha256(
            {
                "query": query,
                "candidates": [
                    {
                        "candidate_id": candidate["candidate_id"],
                        "text": candidate["text"],
                    }
                    for candidate in candidates
                ],
            }
        )
        order = (
            ["voyage", "jina"]
            if case_index % 2 == 0
            else ["jina", "voyage"]
        )
        arm_orders.append(order)
        arms: dict[str, dict[str, Any]] = {}
        for provider in order:
            if before_provider_call and provider in before_provider_call:
                before_provider_call[provider]()
            started = clock()
            error_type = None
            error_count = fallback_count = governance_escape_count = 0
            try:
                proposed = list(
                    providers[provider](
                        case_id,
                        query,
                        [dict(candidate) for candidate in candidates],
                    )
                )
                proposed_ids = [_candidate_id(item) for item in proposed]
                valid_closed_set = (
                    len(proposed_ids) == len(candidate_ids)
                    and len(set(proposed_ids)) == len(candidate_ids)
                    and set(proposed_ids) == set(candidate_ids)
                )
                if not valid_closed_set:
                    governance_escape_count = 1
                    fallback_count = 1
                    ranked_ids = list(candidate_ids)
                else:
                    ranked_ids = proposed_ids
            except Exception as exc:
                error_type = type(exc).__name__
                error_count = 1
                fallback_count = 1
                ranked_ids = list(candidate_ids)
            latency_ms = max(0.0, (clock() - started) * 1000)
            metrics = _metrics(ranked_ids, candidates, top_k)
            arm = {
                "status": "success" if not error_count else "error",
                "input_sha256": input_sha256,
                "ranking_sha256": canonical_sha256(ranked_ids),
                "ranked_candidate_ids": ranked_ids,
                "latency_ms": latency_ms,
                "error_type": error_type,
                "error_count": error_count,
                "fallback_count": fallback_count,
                "retry_count": 0,
                "leakage_count": 0,
                "governance_escape_count": governance_escape_count,
                **metrics,
            }
            arms[provider] = arm
            provider_rows[provider].append(arm)
        case_rows.append(
            {
                "case_id": case_id,
                "input_sha256": input_sha256,
                "arm_order": order,
                "arms": arms,
            }
        )
    aggregates = {
        provider: _aggregate_provider(
            rows,
            top_k=top_k,
            metadata=_safe_provider_metadata(provider_metadata)[provider],
        )
        for provider, rows in provider_rows.items()
    }
    return {
        "schema": "rerank-provider-ab-v1",
        "evaluation_scope": "rerank_only_closed_set",
        "release_authorized": False,
        "top_k": top_k,
        "case_count": len(case_rows),
        "manifest_sha256": canonical_sha256(list(cases)),
        "arm_orders": arm_orders,
        "provider_configuration": _safe_provider_metadata(
            provider_metadata
        ),
        "providers": aggregates,
        "cases": case_rows,
    }


def _aggregate_provider(
    rows: Sequence[Mapping[str, Any]],
    *,
    top_k: int,
    metadata: Mapping[str, str],
) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in rows]
    metric_names = (
        f"recall_at_{top_k}",
        f"ndcg_at_{top_k}",
        "top1_accuracy",
    )
    return {
        **dict(metadata),
        "case_count": len(rows),
        **{
            name: mean(float(row[name]) for row in rows)
            if rows
            else 0.0
            for name in metric_names
        },
        "latency_p50_ms": nearest_rank(latencies, 0.50) or 0.0,
        "latency_p95_ms": nearest_rank(latencies, 0.95) or 0.0,
        **{
            name: sum(int(row[name]) for row in rows)
            for name in (
                "error_count",
                "fallback_count",
                "retry_count",
                "leakage_count",
                "governance_escape_count",
            )
        },
    }


def evaluate_gate(
    report: Mapping[str, Any],
    *,
    quality_margin: float = 0.0,
    max_latency_ratio: float = 1.25,
) -> dict[str, Any]:
    """Compare Jina with Voyage and remain explicitly non-authorizing."""
    top_k = max(1, int(report.get("top_k") or 1))
    providers = report.get("providers") or {}
    voyage = providers.get("voyage") or {}
    jina = providers.get("jina") or {}
    quality_metrics = (
        f"recall_at_{top_k}",
        f"ndcg_at_{top_k}",
        "top1_accuracy",
    )
    margin = max(0.0, float(quality_margin))
    latency_limit = max(0.0, float(max_latency_ratio))
    voyage_p95 = float(voyage.get("latency_p95_ms", 0.0))
    jina_p95 = float(jina.get("latency_p95_ms", 0.0))
    latency_ratio = (
        1.0
        if voyage_p95 == 0.0 and jina_p95 == 0.0
        else None
        if voyage_p95 <= 0.0
        else jina_p95 / voyage_p95
    )
    checks = {
        "quality_non_inferior": all(
            float(jina.get(metric, -1.0))
            >= float(voyage.get(metric, 0.0)) - margin
            for metric in quality_metrics
        ),
        "error_free": all(
            int(provider.get("error_count", 1)) == 0
            for provider in (voyage, jina)
        ),
        "fallback_free": all(
            int(provider.get("fallback_count", 1)) == 0
            for provider in (voyage, jina)
        ),
        "no_retry": all(
            int(provider.get("retry_count", 1)) == 0
            for provider in (voyage, jina)
        ),
        "no_leakage": all(
            int(provider.get("leakage_count", 1)) == 0
            for provider in (voyage, jina)
        ),
        "no_governance_escape": all(
            int(provider.get("governance_escape_count", 1)) == 0
            for provider in (voyage, jina)
        ),
        "identical_inputs": all(
            case.get("arms", {}).get("voyage", {}).get("input_sha256")
            == case.get("arms", {}).get("jina", {}).get("input_sha256")
            == case.get("input_sha256")
            for case in (report.get("cases") or [])
        )
        and bool(report.get("cases")),
        "latency_ratio": (
            latency_ratio is not None
            and latency_ratio <= latency_limit
        ),
    }
    return {
        "schema": "rerank-provider-ab-gate-v1",
        "passed": all(checks.values()),
        "release_authorized": False,
        "quality_margin": margin,
        "max_latency_ratio": latency_limit,
        "latency_ratio": latency_ratio,
        "checks": checks,
        "failed_checks": [
            name for name, passed in checks.items() if not passed
        ],
        "report_sha256": canonical_sha256(report),
    }


__all__ = [
    "ManifestError",
    "canonical_sha256",
    "evaluate_gate",
    "load_manifest",
    "run_provider_ab",
]
