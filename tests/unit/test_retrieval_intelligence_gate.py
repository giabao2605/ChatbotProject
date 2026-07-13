import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _module():
    path = Path("scripts/eval/retrieval_intelligence_gate.py")
    spec = importlib.util.spec_from_file_location("retrieval_intelligence_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(*, wrong=0, leakage=0, p95=100, cost=1, recall10=1, ndcg10=0.5, groups=None):
    return {
        "outcome_confusion": {"wrong_answer": wrong, "leakage": leakage},
        "latency_p95_ms": p95,
        "total_estimated_cost": cost,
        "ranked_retrieval": {"recall_at_10": recall10, "ndcg_at_10": ndcg10},
        "evaluation_groups": groups or {},
    }


def test_late_gate_enforces_quality_latency_and_storage():
    gate = _module()
    baseline = report(ndcg10=0.50)
    candidate = report(ndcg10=0.53, p95=120)

    passed = gate.compare("late_interaction", baseline, candidate, {"shadow_storage_ratio": 20})
    failed = gate.compare("late_interaction", baseline, candidate, {"shadow_storage_ratio": 30})

    assert passed["passed"] is True
    assert failed["checks"]["storage_within_budget"] is False


def test_decomposition_gate_requires_complex_gain_and_zero_simple_planner_calls():
    gate = _module()
    baseline = report(groups={"complex": {"pass_rate": 0.60}})
    candidate = report(
        groups={"complex": {"pass_rate": 0.71}},
        p95=140,
        cost=1.4,
    )

    result = gate.compare("query_decomposition", baseline, candidate, {"simple_planner_calls": 0})

    assert result["passed"] is True
