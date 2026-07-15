"""Retrieval-only evaluation seam for the Late Interaction rollout.

The evaluator receives candidates that have already crossed the production
governance filters.  Rerankers may reorder that closed set, but can never add a
document.  This makes provider and shadow-index comparisons reproducible
without invoking answer generation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import mean
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from mech_chatbot.evaluation.metrics import nearest_rank


class LateInteractionManifestError(ValueError):
    pass


IDENTITY_FIELDS = (
    "user_department",
    "user_roles",
    "allowed_departments",
    "allowed_sites",
    "max_security_level",
)
SCENARIOS = {
    "exact_code",
    "near_code_family",
    "rare_term",
    "alias_mismatch",
    "ocr_noise",
    "near_meaning",
    "rbac_site_denial",
    "lifecycle_negative",
}


@dataclass(frozen=True)
class VariantResult:
    documents: list[Any]
    used_backend: str
    candidate_count: int
    shadow_hits: int = 0
    coverage: float = 0.0
    latency_ms: float = 0.0
    fallback_reason: str | None = None


def load_manifest(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LateInteractionManifestError(f"line {line_number}: invalid JSON: {exc}") from exc
        case_id = str(case.get("case_id") or "").strip()
        if not case_id or case_id in seen:
            raise LateInteractionManifestError(f"line {line_number}: case_id missing or duplicate")
        seen.add(case_id)
        if case.get("scenario") not in SCENARIOS:
            raise LateInteractionManifestError(f"{case_id}: unsupported scenario")
        if not str(case.get("query") or "").strip():
            raise LateInteractionManifestError(f"{case_id}: query is required")
        identity = case.get("identity") or {}
        missing = [field for field in IDENTITY_FIELDS if field not in identity]
        if missing:
            raise LateInteractionManifestError(f"{case_id}: identity missing {', '.join(missing)}")
        if not case.get("expected_sources") and case.get("scenario") not in {
            "rbac_site_denial", "lifecycle_negative"
        }:
            raise LateInteractionManifestError(f"{case_id}: expected_sources is required")
        if case.get("scenario") in {"rbac_site_denial", "lifecycle_negative"} and not case.get("forbidden_sources"):
            raise LateInteractionManifestError(f"{case_id}: forbidden_sources is required")
        cases.append(case)
    if not cases:
        raise LateInteractionManifestError("manifest is empty")
    return cases


def _identity(value: Any) -> dict[str, str]:
    metadata = getattr(value, "metadata", None)
    if isinstance(metadata, Mapping):
        value = metadata
    value = value if isinstance(value, Mapping) else {"document": value}
    fields = {
        "document": value.get("document") or value.get("file_goc") or "",
        "doc_id": value.get("doc_id") or value.get("DocID") or "",
        "page": value.get("page") or value.get("trang_so") or value.get("trang") or "",
        "version": value.get("version") or value.get("version_no") or "",
        "source_id": value.get("source_id") or value.get("source_point_id") or "",
    }
    return {key: str(val).strip().casefold() if val not in (None, "") else "" for key, val in fields.items()}


def _matches(actual: Any, expected: Any) -> bool:
    left, right = _identity(actual), _identity(expected)
    return all(not right[field] or left[field] == right[field] for field in right)


def _source_label(source: Mapping[str, Any]) -> str:
    return str(source.get("document") or source.get("file_goc") or source.get("doc_id") or "unknown")


def preflight_manifest(
    cases: Sequence[Mapping[str, Any]],
    *,
    available_sources: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    missing: list[str] = []
    for case in cases:
        for source in [*(case.get("expected_sources") or []), *(case.get("forbidden_sources") or [])]:
            if not any(_matches(actual, source) for actual in available_sources):
                missing.append(f"{case['case_id']}:{_source_label(source)}")
    required_snapshot = ("source_collection", "shadow_index_version")
    missing_snapshot = [field for field in required_snapshot if not snapshot.get(field)]
    return {
        "schema": "late-interaction-preflight-v1",
        "passed": not missing and not missing_snapshot,
        "case_count": len(cases),
        "missing_expected_sources": missing,
        "missing_snapshot_fields": missing_snapshot,
        "snapshot": dict(snapshot),
    }


def _closed_set(original: Sequence[Any], proposed: Sequence[Any]) -> list[Any] | None:
    remaining = list(original)
    accepted: list[Any] = []
    for item in proposed:
        match = next((doc for doc in remaining if _matches(doc, item)), None)
        if match is None:
            return None
        accepted.append(match)
        remaining.remove(match)
    accepted.extend(remaining)
    return accepted


def _value(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def evaluate_variant(
    case: Mapping[str, Any],
    candidates: Sequence[Any],
    *,
    variant: str,
    voyage_rerank: Callable[[Sequence[Any], str], Sequence[Any]] | None = None,
    shadow_rerank: Callable[[Sequence[Any], str], Any] | None = None,
) -> VariantResult:
    original = list(candidates)
    if variant == "rrf":
        return VariantResult(original, "rrf", len(original))
    started = time.perf_counter()
    try:
        if variant == "voyage":
            if voyage_rerank is None:
                raise RuntimeError("voyage_reranker_unavailable")
            proposed = list(voyage_rerank(original, str(case["query"])))
            closed = _closed_set(original, proposed)
            if closed is None:
                return VariantResult(original, "rrf", len(original), fallback_reason="governance_escape")
            return VariantResult(
                closed, "voyage", len(original),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        if variant == "maxsim":
            if shadow_rerank is None:
                raise RuntimeError("shadow_reranker_unavailable")
            result = shadow_rerank(original, str(case["query"]))
            used = bool(_value(result, "used_shadow", False))
            hits = int(_value(result, "shadow_hits", 0) or 0)
            coverage = float(_value(result, "coverage", 0.0) or 0.0)
            latency = float(_value(result, "total_latency_ms", 0.0) or 0.0)
            reason = _value(result, "fallback_reason")
            if not used:
                return VariantResult(original, "rrf", len(original), hits, coverage, latency, reason or "shadow_not_used")
            closed = _closed_set(original, list(_value(result, "documents", [])))
            if closed is None:
                return VariantResult(original, "rrf", len(original), hits, coverage, latency, "governance_escape")
            return VariantResult(closed, "maxsim", len(original), hits, coverage, latency)
    except Exception as exc:
        return VariantResult(
            original, "rrf", len(original),
            latency_ms=(time.perf_counter() - started) * 1000,
            fallback_reason=f"{variant}_error:{type(exc).__name__}",
        )
    raise ValueError(f"unknown variant: {variant}")


def _graded_metrics(ranked: Sequence[Any], expected: Sequence[Mapping[str, Any]], cutoffs=(5, 10)):
    grades = []
    matched: set[int] = set()
    for actual in ranked:
        index = next((i for i, item in enumerate(expected) if i not in matched and _matches(actual, item)), None)
        if index is None:
            grades.append(0.0)
        else:
            matched.add(index)
            grades.append(float(expected[index].get("relevance", 1)))
    ideal = sorted((float(item.get("relevance", 1)) for item in expected), reverse=True)
    result = {}
    for cutoff in cutoffs:
        observed = grades[:cutoff]
        recall = sum(1 for grade in observed if grade > 0) / len(expected) if expected else 1.0
        dcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(observed))
        idcg = sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(ideal[:cutoff]))
        result[f"recall_at_{cutoff}"] = recall
        result[f"ndcg_at_{cutoff}"] = dcg / idcg if idcg else 1.0
    return result


def build_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    variant: str,
    run_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    metric_rows = []
    case_rows = []
    wrong_answers = leakage = 0
    latencies = []
    coverages = []
    fallback_count = 0
    for row in rows:
        case = row["case"]
        ranked = list(row.get("ranked_sources") or [])
        expected = list(case.get("expected_sources") or [])
        forbidden = list(case.get("forbidden_sources") or [])
        metrics = _graded_metrics(ranked, expected)
        leaked = any(_matches(actual, denied) for actual in ranked for denied in forbidden)
        required_missing = bool(expected) and metrics["recall_at_10"] < 1.0
        wrong = leaked or required_missing
        leakage += int(leaked)
        wrong_answers += int(wrong)
        latency = float(row.get("latency_ms") or 0.0)
        latencies.append(latency)
        coverage = float(row.get("coverage") or 0.0)
        coverages.append(coverage)
        fallback_count += int(bool(row.get("fallback_reason")))
        if expected:
            metric_rows.append(metrics)
        case_rows.append({
            "case_id": case["case_id"],
            "scenario": case["scenario"],
            **metrics,
            "wrong_answer": wrong,
            "leakage": leaked,
            "latency_ms": latency,
            "coverage": coverage,
            "fallback_reason": row.get("fallback_reason"),
            "ranked_sources": ranked,
        })
    aggregate = {
        name: mean(row[name] for row in metric_rows) if metric_rows else 0.0
        for name in ("recall_at_5", "ndcg_at_5", "recall_at_10", "ndcg_at_10")
    }
    return {
        "schema": "late-interaction-eval-v1",
        "variant": variant,
        "run_metadata": dict(run_metadata),
        "ranked_retrieval": aggregate,
        "outcome_confusion": {"wrong_answer": wrong_answers, "leakage": leakage},
        "latency_p50_ms": nearest_rank(latencies, 0.50) or 0.0,
        "latency_p95_ms": nearest_rank(latencies, 0.95) or 0.0,
        "provider_retries": int(run_metadata.get("provider_retries") or 0),
        "fallback_coverage": {
            "fallback_count": fallback_count,
            "fallback_rate": fallback_count / len(rows) if rows else 0.0,
            "shadow_coverage": mean(coverages) if coverages else 0.0,
        },
        "cases": case_rows,
    }


def snapshot_fingerprint(points: Iterable[Mapping[str, Any]], *, index_version: str) -> str:
    canonical = [dict(point) for point in points]
    canonical.sort(key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))
    payload = json.dumps(
        {"index_version": index_version, "points": canonical},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
