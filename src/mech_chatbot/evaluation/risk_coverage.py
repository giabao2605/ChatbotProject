"""Risk-coverage curves for labeled answer/refusal evaluation."""

from __future__ import annotations

from collections import Counter

from mech_chatbot.evaluation.outcomes import ANSWER_OUTCOMES, REFUSAL_OUTCOMES


def build_risk_coverage_report(
    rows: list[dict],
    *,
    operating_points=(0.0, 0.25, 0.5, 0.75, 1.0),
    baseline_wrong_answers: int = 0,
) -> dict:
    """Report explicit operating points; never auto-select a serving threshold."""
    normalized = []
    for row in rows:
        confidence = float(row.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        expected = row.get("expected_outcome")
        if expected not in ANSWER_OUTCOMES | REFUSAL_OUTCOMES:
            raise ValueError(f"invalid expected_outcome: {expected}")
        normalized.append({**row, "confidence": confidence})

    points = []
    denominator = len(normalized)
    for raw_threshold in operating_points:
        threshold = float(raw_threshold)
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("operating point must be between 0 and 1")
        served_rows = [row for row in normalized if row["confidence"] >= threshold]
        refused_rows = [row for row in normalized if row["confidence"] < threshold]
        leakage = sum(bool(row.get("leaked")) for row in served_rows)
        wrong_answer = sum(
            1
            for row in served_rows
            if row["expected_outcome"] in REFUSAL_OUTCOMES
            or not bool(row.get("answer_correct"))
        )
        wrong_refusal = sum(
            1 for row in refused_rows if row["expected_outcome"] in ANSWER_OUTCOMES
        )
        points.append(
            {
                "threshold": threshold,
                "served": len(served_rows),
                "coverage": len(served_rows) / denominator if denominator else 0.0,
                "wrong_refusal": wrong_refusal,
                "wrong_answer": wrong_answer,
                "leakage": leakage,
                "serving_allowed": (
                    leakage == 0 and wrong_answer <= int(baseline_wrong_answers)
                ),
            }
        )

    return {
        "schema": "risk-coverage-v1",
        "cases": denominator,
        "evidence_states": dict(
            sorted(Counter(str(row.get("evidence_state") or "UNKNOWN") for row in normalized).items())
        ),
        "baseline_wrong_answers": int(baseline_wrong_answers),
        "automatic_threshold_selection": False,
        "selected_threshold": None,
        "operating_points": points,
    }
