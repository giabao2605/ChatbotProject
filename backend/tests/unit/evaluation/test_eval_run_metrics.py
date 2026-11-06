from scripts.eval.run_eval import _execution_metrics
from mech_chatbot.evaluation.decomposition import summarize_decomposition_usage


def _usage(*, final_calls: int, final_cost: float) -> dict:
    return {
        "schema": "rag-decomposition-usage-v1",
        "planner": {},
        "branches": [],
        "final_context": {
            "estimated_input_tokens": 40,
            "estimated_input_cost": 0.0001,
            "included_in_final_generation": True,
        },
        "final_generation": {
            "calls": final_calls,
            "input_tokens": 40 if final_calls else 0,
            "output_tokens": 5 if final_calls else 0,
            "estimated_cost": final_cost,
        },
    }


def test_execution_metrics_prefers_current_generation_usage_over_stale_snapshot():
    stale_usage = _usage(final_calls=0, final_cost=0.0)
    current_usage = _usage(final_calls=1, final_cost=0.000175)

    metrics = _execution_metrics({
        "decomposition_usage": stale_usage,
        "generation_metrics": {
            "estimated_cost": 0.000175,
            "decomposition_usage": current_usage,
        },
    })
    summary = summarize_decomposition_usage([metrics])

    assert summary["final_generation"] == {
        "calls": 1,
        "input_tokens": 40,
        "output_tokens": 5,
        "estimated_cost": 0.000175,
    }
    assert summary["cost_reconciled"] is True
