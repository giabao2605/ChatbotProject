"""Deterministic manifest and result evaluation for query decomposition."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence


class DecompositionManifestError(ValueError):
    pass


IDENTITY_FIELDS = (
    "user_department",
    "user_roles",
    "allowed_departments",
    "allowed_sites",
    "max_security_level",
)
BRANCH_OUTCOMES = {"full_answer", "partial_answer", "insufficient_evidence", "access_denied"}


def validate_decomposition_case(case: Mapping[str, Any]) -> None:
    case_id = str(case.get("id") or "").strip()
    if not case_id:
        raise DecompositionManifestError("id is required")
    missing_identity = [field for field in IDENTITY_FIELDS if field not in case]
    if missing_identity:
        raise DecompositionManifestError(f"{case_id}: missing {', '.join(missing_identity)}")
    if case.get("evaluation_group") not in {"simple", "complex"}:
        raise DecompositionManifestError(f"{case_id}: evaluation_group must be simple or complex")
    branches = case.get("expected_branches")
    if not isinstance(branches, list):
        raise DecompositionManifestError(f"{case_id}: expected_branches must be a list")
    if case.get("evaluation_group") == "complex" and not branches:
        raise DecompositionManifestError(f"{case_id}: expected_branches is required")
    branch_ids: set[str] = set()
    for index, branch in enumerate(branches, 1):
        branch_id = str(branch.get("branch_id") or "").strip()
        if not branch_id or branch_id in branch_ids:
            raise DecompositionManifestError(f"{case_id}: branch {index} id missing or duplicate")
        branch_ids.add(branch_id)
        if branch.get("expected_outcome") not in BRANCH_OUTCOMES:
            raise DecompositionManifestError(f"{case_id}:{branch_id}: invalid expected_outcome")
        if "expected_citations" not in branch or not isinstance(branch["expected_citations"], list):
            raise DecompositionManifestError(f"{case_id}:{branch_id}: expected_citations is required")


def load_decomposition_manifest(path: str | Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DecompositionManifestError(f"line {line_number}: invalid JSON: {exc}") from exc
        case_id = str(case.get("id") or "").strip()
        if not case_id or case_id in seen:
            raise DecompositionManifestError(f"line {line_number}: id missing or duplicate")
        seen.add(case_id)
        validate_decomposition_case(case)
        cases.append(case)
    if not cases:
        raise DecompositionManifestError("manifest is empty")
    return cases


def _identity(value: Any) -> dict[str, str]:
    value = value if isinstance(value, Mapping) else {}
    fields = {
        "document": value.get("document") or value.get("file_goc") or "",
        "doc_id": value.get("doc_id") or "",
        "page": value.get("page") or value.get("trang") or value.get("trang_so") or "",
        "version": value.get("version") or value.get("version_no") or "",
        "source_id": value.get("source_id") or "",
    }
    return {
        field: str(raw).strip().casefold() if raw not in (None, "") else ""
        for field, raw in fields.items()
    }


def _matches(actual: Any, expected: Any) -> bool:
    left, right = _identity(actual), _identity(expected)
    return all(not right[field] or left[field] == right[field] for field in right)


def _citations_match(actual: Sequence[Any], expected: Sequence[Any]) -> bool:
    if not expected:
        return not actual
    return all(any(_matches(item, wanted) for item in actual) for wanted in expected)


def _usage_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def _usage_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return parsed


def normalize_decomposition_usage(value: Any) -> dict[str, Any] | None:
    """Keep only the declared metadata-only decomposition usage contract."""

    if value in (None, {}):
        return None
    if not isinstance(value, Mapping):
        raise ValueError("decomposition_usage must be an object")
    if value.get("schema") != "rag-decomposition-usage-v1":
        raise ValueError("decomposition_usage schema is invalid")
    planner = value.get("planner") or {}
    final_context = value.get("final_context") or {}
    final_generation = value.get("final_generation") or {}
    if not all(
        isinstance(item, Mapping)
        for item in (planner, final_context, final_generation)
    ):
        raise ValueError("decomposition_usage stage buckets must be objects")
    branches = []
    for index, raw in enumerate(value.get("branches") or (), 1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"decomposition_usage branch {index} must be an object")
        retrieval = raw.get("retrieval") or {}
        correction = raw.get("correction") or {}
        if not isinstance(retrieval, Mapping) or not isinstance(correction, Mapping):
            raise ValueError(f"decomposition_usage branch {index} buckets are invalid")
        cost_status = str(retrieval.get("cost_status") or "unpriced")
        if cost_status not in {"unpriced", "priced"}:
            raise ValueError(f"decomposition_usage branch {index} cost_status is invalid")
        retrieval_cost = retrieval.get("estimated_cost")
        if retrieval_cost is not None:
            retrieval_cost = _usage_float(
                retrieval_cost,
                f"branches[{index}].retrieval.estimated_cost",
            )
        branches.append(
            {
                "branch_id": str(raw.get("branch_id") or f"branch-{index}"),
                "retrieval": {
                    "latency_ms": _usage_int(
                        retrieval.get("latency_ms"),
                        f"branches[{index}].retrieval.latency_ms",
                    ),
                    "document_count": _usage_int(
                        retrieval.get("document_count"),
                        f"branches[{index}].retrieval.document_count",
                    ),
                    "estimated_input_tokens": _usage_int(
                        retrieval.get("estimated_input_tokens"),
                        f"branches[{index}].retrieval.estimated_input_tokens",
                    ),
                    "estimated_cost": retrieval_cost,
                    "cost_status": cost_status,
                },
                "correction": {
                    "attempted": bool(correction.get("attempted")),
                    "input_tokens": _usage_int(
                        correction.get("input_tokens"),
                        f"branches[{index}].correction.input_tokens",
                    ),
                    "output_tokens": _usage_int(
                        correction.get("output_tokens"),
                        f"branches[{index}].correction.output_tokens",
                    ),
                    "estimated_cost": _usage_float(
                        correction.get("estimated_cost"),
                        f"branches[{index}].correction.estimated_cost",
                    ),
                },
            }
        )
    return {
        "schema": "rag-decomposition-usage-v1",
        "planner": {
            "calls": _usage_int(planner.get("calls"), "planner.calls"),
            "input_tokens": _usage_int(
                planner.get("input_tokens"), "planner.input_tokens"
            ),
            "output_tokens": _usage_int(
                planner.get("output_tokens"), "planner.output_tokens"
            ),
            "estimated_cost": _usage_float(
                planner.get("estimated_cost"), "planner.estimated_cost"
            ),
        },
        "branches": branches,
        "final_context": {
            "estimated_input_tokens": _usage_int(
                final_context.get("estimated_input_tokens"),
                "final_context.estimated_input_tokens",
            ),
            "estimated_input_cost": _usage_float(
                final_context.get("estimated_input_cost"),
                "final_context.estimated_input_cost",
            ),
            "included_in_final_generation": bool(
                final_context.get("included_in_final_generation")
            ),
        },
        "final_generation": {
            "calls": _usage_int(
                final_generation.get("calls"), "final_generation.calls"
            ),
            "input_tokens": _usage_int(
                final_generation.get("input_tokens"),
                "final_generation.input_tokens",
            ),
            "output_tokens": _usage_int(
                final_generation.get("output_tokens"),
                "final_generation.output_tokens",
            ),
            "estimated_cost": _usage_float(
                final_generation.get("estimated_cost"),
                "final_generation.estimated_cost",
            ),
        },
    }


def evaluate_decomposition_case(case: Mapping[str, Any], debug: Mapping[str, Any]) -> dict[str, Any]:
    expected = list(case.get("expected_branches") or [])
    actual = list(debug.get("decomposition_branches") or [])
    results = []
    used: set[int] = set()
    for wanted in expected:
        wanted_id = str(wanted.get("branch_id") or "")
        actual_index = next(
            (index for index, branch in enumerate(actual) if index not in used and branch.get("branch_id") == wanted_id),
            None,
        )
        branch = actual[actual_index] if actual_index is not None else {}
        if actual_index is not None:
            used.add(actual_index)
        outcome_ok = branch.get("outcome") == wanted.get("expected_outcome")
        expected_source_ids = {
            str(citation.get("source_id") or "").strip().upper()
            for citation in wanted.get("expected_citations") or []
            if citation.get("source_id")
        }
        rendered_source_ids = {
            str(source_id).strip().upper()
            for source_id in branch.get("rendered_source_ids") or []
            if source_id
        }
        citation_ok = _citations_match(
            branch.get("citations") or [], wanted.get("expected_citations") or [],
        ) and expected_source_ids <= rendered_source_ids
        results.append({
            "branch_id": wanted_id,
            "outcome_passed": outcome_ok,
            "citation_passed": citation_ok,
        })
    branch_accuracy = mean(row["outcome_passed"] for row in results) if results else 0.0
    citation_accuracy = mean(row["citation_passed"] for row in results) if results else 0.0
    intent_coverage = debug.get("decomposition_intent_coverage")
    if intent_coverage is None:
        intent_coverage_ok = True
    else:
        expected_intent_count = int(
            debug.get("decomposition_intent_count") or len(intent_coverage)
        )
        intent_coverage_ok = bool(
            isinstance(intent_coverage, (list, tuple))
            and len(intent_coverage) == expected_intent_count
            and all(bool(value) for value in intent_coverage)
        )
    budget_checks = {
        "planner_count": int(debug.get("planner_count") or 0) <= (0 if case.get("evaluation_group") == "simple" else 1),
        "subquery_count": int(debug.get("subquery_count") or 0) <= 3,
        "correction_count": int(debug.get("correction_count") or 0) <= 1,
        "final_generation_count": int(debug.get("final_generation_count") or 0) <= 1,
        "deadline": not bool(debug.get("deadline_exceeded")),
        "intent_coverage": intent_coverage_ok,
    }
    budget_passed = all(budget_checks.values())
    return {
        "applicable": bool(expected),
        "passed": bool(results) and branch_accuracy == 1.0 and citation_accuracy == 1.0 and budget_passed,
        "branch_accuracy": branch_accuracy,
        "citation_accuracy": citation_accuracy,
        "budget_passed": budget_passed,
        "budget_checks": budget_checks,
        "branches": results,
    }


def summarize_decomposition_evaluation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    applicable = [row for row in rows if (row.get("decomposition_evaluation") or {}).get("applicable")]
    return {
        "applicable_cases": len(applicable),
        "passed_cases": sum(bool((row.get("decomposition_evaluation") or {}).get("passed")) for row in applicable),
        "branch_accuracy": mean(
            float((row.get("decomposition_evaluation") or {}).get("branch_accuracy") or 0.0)
            for row in applicable
        ) if applicable else None,
        "citation_accuracy": mean(
            float((row.get("decomposition_evaluation") or {}).get("citation_accuracy") or 0.0)
            for row in applicable
        ) if applicable else None,
        "budget_violations": sum(
            not bool((row.get("decomposition_evaluation") or {}).get("budget_passed"))
            for row in applicable
        ),
        "simple_planner_calls": sum(
            int(row.get("planner_count") or 0)
            for row in rows if row.get("evaluation_group") == "simple"
        ),
    }


def summarize_decomposition_usage(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    usages = [
        usage
        for row in rows
        if (usage := normalize_decomposition_usage(row.get("decomposition_usage")))
        is not None
    ]
    planner = {
        "calls": sum(item["planner"]["calls"] for item in usages),
        "input_tokens": sum(item["planner"]["input_tokens"] for item in usages),
        "output_tokens": sum(item["planner"]["output_tokens"] for item in usages),
        "estimated_cost": sum(
            item["planner"]["estimated_cost"] for item in usages
        ),
    }
    branch_rows = [branch for item in usages for branch in item["branches"]]
    branch_retrieval = {
        "branches": len(branch_rows),
        "document_count": sum(
            branch["retrieval"]["document_count"] for branch in branch_rows
        ),
        "estimated_input_tokens": sum(
            branch["retrieval"]["estimated_input_tokens"]
            for branch in branch_rows
        ),
        "priced_branches": sum(
            branch["retrieval"]["estimated_cost"] is not None
            for branch in branch_rows
        ),
        "unpriced_branches": sum(
            branch["retrieval"]["estimated_cost"] is None
            for branch in branch_rows
        ),
    }
    branch_correction = {
        "attempts": sum(
            bool(branch["correction"]["attempted"])
            for branch in branch_rows
        ),
        "input_tokens": sum(
            branch["correction"]["input_tokens"] for branch in branch_rows
        ),
        "output_tokens": sum(
            branch["correction"]["output_tokens"] for branch in branch_rows
        ),
        "estimated_cost": sum(
            branch["correction"]["estimated_cost"] for branch in branch_rows
        ),
    }
    final_context = {
        "estimated_input_tokens": sum(
            item["final_context"]["estimated_input_tokens"] for item in usages
        ),
        "estimated_input_cost": sum(
            item["final_context"]["estimated_input_cost"] for item in usages
        ),
        "included_in_final_generation": all(
            item["final_context"]["included_in_final_generation"]
            for item in usages
        ) if usages else True,
    }
    final_generation = {
        "calls": sum(item["final_generation"]["calls"] for item in usages),
        "input_tokens": sum(
            item["final_generation"]["input_tokens"] for item in usages
        ),
        "output_tokens": sum(
            item["final_generation"]["output_tokens"] for item in usages
        ),
        "estimated_cost": sum(
            item["final_generation"]["estimated_cost"] for item in usages
        ),
    }
    legacy_total = sum(float(row.get("estimated_cost") or 0.0) for row in rows)
    attributed = (
        planner["estimated_cost"]
        + branch_correction["estimated_cost"]
        + final_generation["estimated_cost"]
    )
    return {
        "schema": "rag-decomposition-usage-summary-v1",
        "cases": len(usages),
        "planner": planner,
        "branch_retrieval": branch_retrieval,
        "branch_correction": branch_correction,
        "final_context": final_context,
        "final_generation": final_generation,
        "legacy_total_estimated_cost": legacy_total,
        "attributed_estimated_cost": attributed,
        "cost_reconciled": math.isclose(
            legacy_total,
            attributed,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ),
    }
