"""Fail-closed contracts for roadmap milestone 2.9 integrated hardening."""

from __future__ import annotations

from collections import Counter


FEATURE_FLAGS = (
    "RAG_CRAG_ENABLED",
    "RAG_CLAIM_REPAIR_ENABLED",
    "RAG_GROUNDED_MATH_ENABLED",
    "RAG_LATE_INTERACTION_ENABLED",
    "RAG_QUERY_DECOMPOSITION_ENABLED",
    "RAG_GRAPH_RETRIEVAL_ENABLED",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
)
VERSION_FIELDS = (
    "RAG_PLANNER_VERSION",
    "RAG_LATE_INDEX_VERSION",
    "RAG_GRAPH_SERVING_EPOCH",
    "RAG_COMMUNITY_SERVING_EPOCH",
)
REQUIRED_COMBINATIONS = {
    "crag_repair": {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"},
    "crag_grounded_math": {"RAG_CRAG_ENABLED", "RAG_GROUNDED_MATH_ENABLED"},
    "crag_late_interaction": {"RAG_CRAG_ENABLED", "RAG_LATE_INTERACTION_ENABLED"},
    "crag_query_decomposition": {"RAG_CRAG_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED"},
    "crag_graph_retrieval": {"RAG_CRAG_ENABLED", "RAG_GRAPH_RETRIEVAL_ENABLED"},
    "decomposition_graph": {
        "RAG_CRAG_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED",
        "RAG_GRAPH_RETRIEVAL_ENABLED",
    },
    "decomposition_late_interaction": {
        "RAG_CRAG_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED",
        "RAG_LATE_INTERACTION_ENABLED",
    },
}
REQUIRED_SECURITY_DIMENSIONS = {
    "role", "department", "site", "clearance", "lifecycle", "publication",
    "current_version",
}
REQUEST_LIMITS = {
    "planner_count": 1,
    "subquery_count": 3,
    "correction_count": 1,
    "repair_count": 1,
    "calculation_count": 1,
    "graph_edge_count": 50,
    "provider_retries": 2,
    "final_generation_count": 1,
}
REQUIRED_PREREQUISITES = {
    "crag", "grounded_math", "late_interaction", "query_decomposition",
    "graph_retrieval",
}


def _enabled(value) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "on"}


def validate_combination_matrix(matrix: dict) -> dict:
    combinations = list(matrix.get("combinations") or [])
    ids = [str(item.get("id") or "").strip() for item in combinations]
    id_counts = Counter(ids)
    by_id = {str(item.get("id") or "").strip(): item for item in combinations}
    flags_explicit = all(
        set((item.get("flags") or {}).keys()) == set(FEATURE_FLAGS)
        and all(isinstance(value, (str, bool, int)) for value in item["flags"].values())
        for item in combinations
    )
    versions_explicit = all(
        set((item.get("versions") or {}).keys()) == set(VERSION_FIELDS)
        and all(str(value or "").strip() for value in item["versions"].values())
        for item in combinations
    )
    required_flags_correct = all(
        combination_id in by_id
        and required == {
            name for name, value in (by_id[combination_id].get("flags") or {}).items()
            if _enabled(value)
        }
        for combination_id, required in REQUIRED_COMBINATIONS.items()
    )
    dependencies_complete = all(
        isinstance(item.get("prerequisites"), list)
        and item["prerequisites"]
        and set(item["prerequisites"]) <= REQUIRED_PREREQUISITES
        for item in combinations
    )
    checks = {
        "schema_valid": matrix.get("schema") == "integrated-feature-matrix-v1",
        "required_combinations_exact": set(ids) == set(REQUIRED_COMBINATIONS),
        "combination_ids_unique": bool(ids) and all(count == 1 for count in id_counts.values()),
        "all_flags_explicit": flags_explicit,
        "all_versions_explicit": versions_explicit,
        "required_flags_enabled": required_flags_correct,
        "dependencies_complete": dependencies_complete,
    }
    return {
        "schema": "integrated-feature-matrix-validation-v1",
        "passed": all(checks.values()),
        "checks": checks,
        "combination_ids": ids,
        "combination_count": len(combinations),
    }


def evaluate_request_budgets(cases) -> dict:
    violations = []
    maxima = {field: 0 for field in REQUEST_LIMITS}
    for case in cases or ():
        case_id = str(case.get("id") or "<missing>")
        combination_id = str(case.get("combination_id") or "")
        if combination_id not in REQUIRED_COMBINATIONS:
            violations.append({
                "case_id": case_id, "combination_id": combination_id,
                "field": "combination_id", "value": combination_id,
                "limit": "known matrix combination",
            })
        for field, limit in REQUEST_LIMITS.items():
            raw_value = case.get(field)
            if not isinstance(raw_value, int) or isinstance(raw_value, bool):
                violations.append({
                    "case_id": case_id, "combination_id": combination_id,
                    "field": field, "value": raw_value, "limit": limit,
                    "reason": "required integer telemetry is missing or invalid",
                })
                value = 0
            else:
                value = raw_value
            maxima[field] = max(maxima[field], value)
            if value < 0 or value > limit:
                violations.append({
                    "case_id": case_id, "combination_id": combination_id,
                    "field": field, "value": value, "limit": limit,
                })
        if not isinstance(case.get("deadline_exceeded"), bool):
            violations.append({
                "case_id": case_id, "combination_id": combination_id,
                "field": "deadline_exceeded", "value": case.get("deadline_exceeded"),
                "limit": False, "reason": "required boolean telemetry is missing or invalid",
            })
        elif case["deadline_exceeded"] is True:
            violations.append({
                "case_id": case_id, "combination_id": combination_id,
                "field": "deadline_exceeded", "value": True, "limit": False,
            })
        enabled = REQUIRED_COMBINATIONS.get(combination_id, set())
        inactive_budgets = {
            "planner_count": "RAG_QUERY_DECOMPOSITION_ENABLED",
            "subquery_count": "RAG_QUERY_DECOMPOSITION_ENABLED",
            "repair_count": "RAG_CLAIM_REPAIR_ENABLED",
            "calculation_count": "RAG_GROUNDED_MATH_ENABLED",
            "graph_edge_count": "RAG_GRAPH_RETRIEVAL_ENABLED",
        }
        for field, flag in inactive_budgets.items():
            raw_value = case.get(field)
            if flag not in enabled and isinstance(raw_value, int) and raw_value != 0:
                violations.append({
                    "case_id": case_id, "combination_id": combination_id,
                    "field": field, "value": int(case.get(field) or 0),
                    "limit": 0, "reason": f"{flag} is disabled",
                })
    return {
        "schema": "integrated-request-budget-v1",
        "passed": bool(cases) and not violations,
        "case_count": len(cases or ()),
        "combination_ids": sorted({
            str(case.get("combination_id") or "") for case in cases or ()
            if str(case.get("combination_id") or "")
        }),
        "limits": dict(REQUEST_LIMITS),
        "maxima": maxima,
        "violations": violations,
    }


def validate_security_manifest(cases) -> dict:
    cases = list(cases or ())
    ids = [str(case.get("id") or "").strip() for case in cases]
    dimensions = {str(case.get("dimension") or "").strip() for case in cases}
    access_by_dimension = {
        dimension: {case.get("expected_access") for case in cases if case.get("dimension") == dimension}
        for dimension in dimensions
    }
    checks = {
        "ids_unique_nonempty": bool(ids) and len(set(ids)) == len(ids) and all(ids),
        "dimensions_complete": dimensions == REQUIRED_SECURITY_DIMENSIONS,
        "allow_and_deny_per_dimension": all(
            values == {True, False} for values in access_by_dimension.values()
        ),
        "identity_explicit": all(
            isinstance(case.get("identity"), dict)
            and all(
                isinstance(case["identity"].get(field), list)
                and bool(case["identity"][field])
                for field in ("roles", "allowed_departments", "allowed_sites")
            )
            and str(case["identity"].get("max_security_level") or "")
            in {"public", "internal", "confidential"}
            for case in cases
        ),
        "document_state_explicit": all(
            isinstance(case.get("document_state"), dict)
            and all(field in case["document_state"] for field in (
                "department", "site", "security_level", "lifecycle_status",
                "publication_state", "is_current",
            ))
            for case in cases
        ),
    }
    return {
        "schema": "integrated-security-manifest-validation-v1",
        "passed": all(checks.values()),
        "checks": checks,
        "dimensions": sorted(dimensions),
        "case_count": len(cases),
    }


def _valid_admin_exception(case: dict) -> bool:
    identity = case.get("identity") or {}
    state = case.get("document_state") or {}
    return (
        case.get("admin_exception") is True
        and "admin" in {str(role).strip().casefold() for role in identity.get("roles") or []}
        and state.get("lifecycle_status") == "published"
        and state.get("publication_state") == "published"
        and state.get("is_current") is True
        and state.get("review_status", "approved") == "approved"
        and state.get("effective_status", "effective") == "effective"
        and state.get("servable", True) is True
    )


def evaluate_security_results(cases) -> dict:
    cases = list(cases or ())
    mismatches = []
    leakage = []
    admin_exceptions = []
    for case in cases:
        case_id = str(case.get("id") or "<missing>")
        if not isinstance(case.get("observed_access"), bool):
            mismatches.append({"case_id": case_id, "reason": "observed_access_missing"})
        elif case["observed_access"] is not case.get("expected_access"):
            mismatches.append({"case_id": case_id, "reason": "access_outcome_mismatch"})
        valid_admin_exception = _valid_admin_exception(case)
        if valid_admin_exception and case.get("observed_access") is True:
            admin_exceptions.append(case_id)
        if case.get("leaked") is True:
            if valid_admin_exception:
                continue
            else:
                leakage.append(case_id)
    coverage = validate_security_manifest(cases)
    return {
        "schema": "integrated-security-results-v1",
        "passed": coverage["passed"] and not mismatches and not leakage,
        "manifest_coverage_passed": coverage["passed"],
        "case_count": len(cases),
        "outcome_mismatches": mismatches,
        "leakage_count": len(leakage),
        "leakage_case_ids": leakage,
        "admin_exception_count": len(admin_exceptions),
        "admin_exception_case_ids": admin_exceptions,
    }


def execute_security_manifest(cases) -> dict:
    """Exercise the browser/RAG-equivalent document serving policy in memory."""
    from mech_chatbot.api.file_access import (
        DocumentAccessRecord,
        evaluate_document_access,
    )

    results = []
    for index, case in enumerate(cases or (), 1):
        identity = case.get("identity") or {}
        state = case.get("document_state") or {}
        record = DocumentAccessRecord(
            doc_id=index,
            ten_file=f"integrated-security-{index}.md",
            file_path=None,
            thu_muc=state.get("department"),
            security_level=state.get("security_level") or "confidential",
            site=state.get("site"),
            lifecycle_status=state.get("lifecycle_status"),
            review_status=state.get("review_status", "approved"),
            departments=(str(state.get("department") or ""),),
            servable=state.get("servable", True),
            publication_state=state.get("publication_state") or "",
            is_current=state.get("is_current") is True,
            effective_status=state.get("effective_status", "effective"),
        )
        profile = {
            "roles": identity.get("roles") or [],
            "allowed_departments": identity.get("allowed_departments") or [],
            "allowed_sites": identity.get("allowed_sites") or [],
            "max_security_level": identity.get("max_security_level") or "public",
        }
        decision = evaluate_document_access(profile, record)
        results.append({
            **case,
            "observed_access": decision.allowed,
            "decision_reason": decision.reason,
            "leaked": False,
        })
    return evaluate_security_results(results)


def _ratio(candidate, baseline):
    if baseline == 0:
        return 1.0 if candidate == 0 else float("inf")
    return candidate / baseline


def compare_load_reports(baseline: dict, candidate: dict) -> dict:
    required = (
        "concurrency", "requests", "successful_requests", "first_token_p50_ms",
        "first_token_p95_ms", "complete_p50_ms", "complete_p95_ms",
        "cost_per_query", "provider_retry_rate", "fallback_rate",
    )
    complete = all(
        report.get("schema") == "integrated-load-report-v1"
        and all(report.get(field) is not None for field in required)
        for report in (baseline, candidate)
    )
    checks = {
        "metrics_complete": complete,
        "conditions_match": (
            baseline.get("concurrency") == candidate.get("concurrency")
            and baseline.get("requests") == candidate.get("requests")
        ),
        "all_requests_successful": (
            all(
                int(report.get("requests") or 0) > 0
                and int(report.get("successful_requests") or -1)
                == int(report.get("requests") or 0)
                for report in (baseline, candidate)
            )
        ),
        "first_token_p95_within_budget": complete and _ratio(
            float(candidate["first_token_p95_ms"]), float(baseline["first_token_p95_ms"])
        ) <= 1.5,
        "completion_p95_within_budget": complete and _ratio(
            float(candidate["complete_p95_ms"]), float(baseline["complete_p95_ms"])
        ) <= 1.5,
        "cost_within_budget": complete and _ratio(
            float(candidate["cost_per_query"]), float(baseline["cost_per_query"])
        ) <= 1.5,
        "retry_within_budget": complete and float(candidate["provider_retry_rate"]) <= max(
            float(baseline["provider_retry_rate"]), 0.05
        ),
        "fallback_within_budget": complete and float(candidate["fallback_rate"]) <= 0.10,
    }
    return {
        "schema": "integrated-load-comparison-v1",
        "passed": all(checks.values()),
        "checks": checks,
        "limits": {
            "max_first_token_p95_ratio": 1.5,
            "max_completion_p95_ratio": 1.5,
            "max_cost_ratio": 1.5,
            "max_provider_retry_rate": max(
                float(baseline.get("provider_retry_rate") or 0), 0.05
            ),
            "max_fallback_rate": 0.10,
        },
    }


def evaluate_integrated_readiness(
    *, matrix_report, security_manifest_report, cache_isolation_passed,
    strict_stream_passed, rollback_passed, prerequisites,
) -> dict:
    capability_checks = {
        "combination_matrix_valid": matrix_report.get("passed") is True,
        "security_manifest_valid": security_manifest_report.get("passed") is True,
        "cache_isolation_verified": cache_isolation_passed is True,
        "strict_buffered_stream_verified": strict_stream_passed is True,
        "rollback_verified": rollback_passed is True,
    }
    capability_passed = all(capability_checks.values())
    prerequisites_complete = (
        set(prerequisites or {}) == REQUIRED_PREREQUISITES
        and all(prerequisites.values())
    )
    blockers = []
    if not capability_passed:
        blockers.append("offline_capability_incomplete")
    if not prerequisites_complete:
        blockers.append("prerequisite_milestones_incomplete")
    return {
        "schema": "integrated-hardening-readiness-v1",
        "capability_passed": capability_passed,
        "ready_for_live_matrix": capability_passed and prerequisites_complete,
        "capability_checks": capability_checks,
        "prerequisites": dict(prerequisites or {}),
        "blockers": blockers,
    }


__all__ = [
    "compare_load_reports", "evaluate_integrated_readiness",
    "evaluate_request_budgets", "evaluate_security_results",
    "execute_security_manifest",
    "validate_combination_matrix", "validate_security_manifest",
]
