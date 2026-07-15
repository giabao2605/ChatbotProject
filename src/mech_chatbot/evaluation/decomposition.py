"""Deterministic manifest and result evaluation for query decomposition."""

from __future__ import annotations

import json
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
    budget_checks = {
        "planner_count": int(debug.get("planner_count") or 0) <= (0 if case.get("evaluation_group") == "simple" else 1),
        "subquery_count": int(debug.get("subquery_count") or 0) <= 3,
        "correction_count": int(debug.get("correction_count") or 0) <= 1,
        "final_generation_count": int(debug.get("final_generation_count") or 0) <= 1,
        "deadline": not bool(debug.get("deadline_exceeded")),
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
