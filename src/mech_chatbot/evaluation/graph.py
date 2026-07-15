"""Deterministic evaluator for governed graph retrieval evidence."""

from __future__ import annotations


def evaluate_graph_case(case: dict, debug: dict) -> dict:
    expected = case.get("expected_relation") or {}
    graph_docs = [
        item for item in debug.get("retrieved_docs") or []
        if item.get("graph_edge_id") is not None
    ]
    applicable = bool(expected)
    matched = any(
        str(item.get("graph_source_key") or "").casefold()
        == str(expected.get("source_key") or "").casefold()
        and str(item.get("graph_relation_type") or "").upper()
        == str(expected.get("relation_type") or "").upper()
        and str(item.get("graph_target_key") or "").casefold()
        == str(expected.get("target_key") or "").casefold()
        for item in graph_docs
    )
    routed = bool(debug.get("graph_routed"))
    max_hops = int(debug.get("graph_max_hops") or 0)
    edge_count = int(debug.get("graph_edge_count") or 0)
    budget_ok = not routed or (1 <= max_hops <= 2 and edge_count <= 50)
    relational = (case.get("evaluation_group") or case.get("scenario")) == "relational"
    return {
        "applicable": applicable,
        "passed": not applicable or matched,
        "relation_matched": matched,
        "edge_count": edge_count,
        "max_hops": max_hops,
        "budget_ok": budget_ok,
        "routed": routed,
        "non_relational_graph_call": routed and not relational,
    }


def summarize_graph_evaluation(rows: list[dict]) -> dict:
    applicable = [row for row in rows if row.get("graph_evaluation", {}).get("applicable")]
    return {
        "applicable_cases": len(applicable),
        "passed_cases": sum(bool(row["graph_evaluation"].get("passed")) for row in applicable),
        "relation_accuracy": (
            sum(bool(row["graph_evaluation"].get("passed")) for row in applicable) / len(applicable)
            if applicable else None
        ),
        "budget_violations": sum(
            not bool(row.get("graph_evaluation", {}).get("budget_ok", True)) for row in rows
        ),
        "non_relational_graph_calls": sum(
            bool(row.get("graph_evaluation", {}).get("non_relational_graph_call")) for row in rows
        ),
    }
