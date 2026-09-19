"""Deterministic manifest and result evaluation for query decomposition."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

from mech_chatbot.evaluation.grounding import extract_claims
from mech_chatbot.rag.answer_checks import extract_source_ids


class DecompositionManifestError(ValueError):
    pass


IDENTITY_FIELDS = (
    "user_department",
    "user_roles",
    "allowed_departments",
    "allowed_sites",
    "max_security_level",
)
IDENTITY_FIELDS_FOR_CITATION = ("document", "doc_id", "page", "version", "source_id")
BRANCH_OUTCOMES = {"full_answer", "partial_answer", "insufficient_evidence", "access_denied"}
TERMINAL_OUTCOMES = {"insufficient_evidence", "access_denied"}
CITATION_MARKER_PATTERN = re.compile(r"\[(?:Nguồn|Source)\s*:", re.IGNORECASE)
TERMINAL_POLICY_NOTICE_PATTERNS = (
    re.compile(
        r"^tài liệu hiện tại không ghi thông tin đủ để trả lời câu hỏi này "
        r"\([^)]+\)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^mình sẽ không tự ước lượng hoặc tự bịa số liệu\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^để trả lời được, bạn cần bổ sung tài liệu có dữ kiện trực tiếp "
        r"liên quan, ví dụ .+$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^the current documents do not contain enough information to answer "
        r"this question \([^)]+\)\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^i will not estimate or fabricate data\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^to get an answer, please load documents with directly relevant "
        r"data, such as .+$",
        re.IGNORECASE,
    ),
)


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
        rendered = branch.get("expected_rendered_citations")
        scoped = case.get("evaluation_scope") in {
            "query_only", "math_query_interaction",
        }
        if scoped and not isinstance(rendered, list):
            raise DecompositionManifestError(
                f"{case_id}:{branch_id}: expected_rendered_citations is required"
            )
        if scoped and any(
            not isinstance(citation, Mapping)
            or not str(citation.get("source_id") or "").strip()
            for citation in branch["expected_citations"]
        ):
            raise DecompositionManifestError(
                f"{case_id}:{branch_id}: citation source_id is required"
            )
        if isinstance(rendered, list) and any(
            not isinstance(citation, Mapping)
            or not str(citation.get("source_id") or "").strip()
            for citation in rendered
        ):
            raise DecompositionManifestError(
                f"{case_id}:{branch_id}: rendered citation source_id is required"
            )
        if scoped:
            expected_source_ids = [
                str(citation["source_id"]).strip().upper()
                for citation in branch["expected_citations"]
            ]
            if len(expected_source_ids) != len(set(expected_source_ids)):
                raise DecompositionManifestError(
                    f"{case_id}:{branch_id}: duplicate citation source_id"
                )
    terminal_count_fields = (
        "expected_terminal_claim_count",
        "expected_terminal_rendered_source_count",
    )
    if any(field in case for field in terminal_count_fields) and (
        case.get("expected_outcome") not in TERMINAL_OUTCOMES
        or any(
            type(case.get(field)) is not int or case.get(field) != 0
            for field in terminal_count_fields
        )
    ):
        raise DecompositionManifestError(
            f"{case_id}: terminal answer count contract must be zero"
        )


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


def _citations_match_exact(actual: Sequence[Any], expected: Sequence[Any]) -> bool:
    unique_actual: list[Any] = []
    seen_actual: set[tuple[str, str, str, str, str]] = set()
    for item in actual:
        identity = _identity(item)
        key = tuple(identity[field] for field in IDENTITY_FIELDS_FOR_CITATION)
        if key not in seen_actual:
            seen_actual.add(key)
            unique_actual.append(item)
    if len(unique_actual) != len(expected):
        return False
    used: set[int] = set()
    for wanted in expected:
        match = next(
            (
                index
                for index, item in enumerate(unique_actual)
                if index not in used and _matches(item, wanted)
            ),
            None,
        )
        if match is None:
            return False
        used.add(match)
    return True


def _terminal_answer_contract(
    case: Mapping[str, Any],
    answer: Any,
) -> tuple[bool, list[dict[str, str]], int, int]:
    expected_claim_count = case.get("expected_terminal_claim_count")
    expected_rendered_source_count = case.get(
        "expected_terminal_rendered_source_count"
    )
    if expected_claim_count is None and expected_rendered_source_count is None:
        return True, [], 0, 0
    if case.get("expected_outcome") not in TERMINAL_OUTCOMES or answer is None:
        return False, [{"kind": "terminal_answer_missing", "value": ""}], 0, 0
    answer = str(answer or "")
    violations: list[dict[str, str]] = []
    fragments = [
        fragment.strip()
        for fragment in re.split(r"(?<=[.!?])\s+|[;\r\n]+", answer)
        if fragment.strip()
    ]
    terminal_claim_count = sum(
        len(extract_claims(fragment))
        for fragment in fragments
        if not any(
            pattern.fullmatch(fragment)
            for pattern in TERMINAL_POLICY_NOTICE_PATTERNS
        )
    )
    terminal_rendered_source_count = max(
        len(extract_source_ids(answer)),
        len(CITATION_MARKER_PATTERN.findall(answer)),
    )
    if (
        expected_claim_count is not None
        and terminal_claim_count != int(expected_claim_count)
    ):
        violations.append({
            "kind": "terminal_claim_count",
            "value": str(terminal_claim_count),
        })
    if (
        expected_rendered_source_count is not None
        and terminal_rendered_source_count != int(expected_rendered_source_count)
    ):
        violations.append({
            "kind": "terminal_rendered_source_count",
            "value": str(terminal_rendered_source_count),
        })
    return (
        not violations,
        violations,
        terminal_claim_count,
        terminal_rendered_source_count,
    )


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


def evaluate_decomposition_case(
    case: Mapping[str, Any],
    debug: Mapping[str, Any],
    *,
    answer: str | None = None,
) -> dict[str, Any]:
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
        rendered_source_ids = {
            str(source_id).strip().upper()
            for source_id in branch.get("rendered_source_ids") or []
            if source_id
        }
        if "expected_rendered_citations" in wanted:
            expected_rendered = wanted.get("expected_rendered_citations") or []
            expected_rendered_source_ids = {
                str(citation.get("source_id") or "").strip().upper()
                for citation in expected_rendered
                if citation.get("source_id")
            }
            rendered_contract_valid = len(expected_rendered_source_ids) == len(
                expected_rendered
            )
            citation_ok = (
                _citations_match_exact(
                    branch.get("citations") or [],
                    wanted.get("expected_citations") or [],
                )
                and rendered_contract_valid
                and rendered_source_ids == expected_rendered_source_ids
            )
        else:
            expected_source_ids = {
                str(citation.get("source_id") or "").strip().upper()
                for citation in wanted.get("expected_citations") or []
                if citation.get("source_id")
            }
            citation_ok = _citations_match(
                branch.get("citations") or [],
                wanted.get("expected_citations") or [],
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
    effective_answer = answer if answer is not None else debug.get("answer")
    (
        terminal_answer_passed,
        terminal_answer_violations,
        terminal_claim_count,
        terminal_rendered_source_count,
    ) = _terminal_answer_contract(
        case,
        effective_answer,
    )
    return {
        "applicable": bool(expected),
        "passed": bool(results) and branch_accuracy == 1.0 and citation_accuracy == 1.0 and budget_passed and terminal_answer_passed,
        "branch_accuracy": branch_accuracy,
        "citation_accuracy": citation_accuracy,
        "budget_passed": budget_passed,
        "budget_checks": budget_checks,
        "terminal_answer_passed": terminal_answer_passed,
        "terminal_answer_violations": terminal_answer_violations,
        "terminal_claim_count": terminal_claim_count,
        "terminal_rendered_source_count": terminal_rendered_source_count,
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
        "terminal_answer_violations": sum(
            (row.get("decomposition_evaluation") or {}).get(
                "terminal_answer_passed"
            ) is False
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
