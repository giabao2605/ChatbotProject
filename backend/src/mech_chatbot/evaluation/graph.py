"""Deterministic evaluator for governed graph retrieval evidence."""

from __future__ import annotations

from mech_chatbot.evaluation.schema import is_valid_relation_contract


def evaluate_graph_case(case: dict, debug: dict) -> dict:
    expected_many = case.get("expected_relations")
    if isinstance(expected_many, list) and expected_many:
        expected_relations = [
            item for item in expected_many if is_valid_relation_contract(item)
        ]
        invalid_relation_count = len(expected_many) - len(expected_relations)
    else:
        expected = case.get("expected_relation")
        expected_relations = [expected] if is_valid_relation_contract(expected) else []
        invalid_relation_count = int(expected is not None and not expected_relations)
    evidence = debug.get("graph_evidence")
    if evidence is None:
        evidence = debug.get("retrieved_docs") or []
    graph_docs = [
        item for item in evidence
        if item.get("graph_edge_id") is not None
    ]
    applicable = bool(expected_relations) or bool(invalid_relation_count)
    relation_matches = [
        any(
            str(item.get("graph_source_key") or "").casefold()
            == str(expected.get("source_key") or "").casefold()
            and str(item.get("graph_relation_type") or "").upper()
            == str(expected.get("relation_type") or "").upper()
            and str(item.get("graph_target_key") or "").casefold()
            == str(expected.get("target_key") or "").casefold()
            for item in graph_docs
        )
        for expected in expected_relations
    ]
    matched = (
        not invalid_relation_count
        and bool(relation_matches)
        and all(relation_matches)
    )
    matched_relation_count = sum(relation_matches)
    routed = bool(debug.get("graph_routed"))
    max_hops = int(debug.get("graph_max_hops") or 0)
    edge_count = int(debug.get("graph_edge_count") or 0)
    budget_ok = not routed or (1 <= max_hops <= 2 and edge_count <= 50)
    relational = (case.get("evaluation_group") or case.get("scenario")) in {
        "relational", "graphrag",
    }
    return {
        "applicable": applicable,
        "passed": not applicable or matched,
        "relation_matched": matched,
        "matched_relation_count": matched_relation_count,
        "expected_relation_count": len(expected_relations),
        "invalid_relation_count": invalid_relation_count,
        "edge_count": edge_count,
        "max_hops": max_hops,
        "budget_ok": budget_ok,
        "routed": routed,
        "non_relational_graph_call": routed and not relational,
    }


def summarize_graph_evaluation(rows: list[dict]) -> dict:
    applicable = [row for row in rows if row.get("graph_evaluation", {}).get("applicable")]
    relational_answer_passes = sum(
        bool(row["graph_evaluation"].get("relational_answer_passed"))
        for row in applicable
    )
    return {
        "applicable_cases": len(applicable),
        "passed_cases": sum(bool(row["graph_evaluation"].get("passed")) for row in applicable),
        "relation_accuracy": (
            sum(bool(row["graph_evaluation"].get("passed")) for row in applicable) / len(applicable)
            if applicable else None
        ),
        "relational_answer_accuracy": (
            relational_answer_passes / len(applicable) if applicable else None
        ),
        "budget_violations": sum(
            not bool(row.get("graph_evaluation", {}).get("budget_ok", True)) for row in rows
        ),
        "non_relational_graph_calls": sum(
            bool(row.get("graph_evaluation", {}).get("non_relational_graph_call")) for row in rows
        ),
    }
