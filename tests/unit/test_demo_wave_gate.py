import pytest
from unittest.mock import patch

from scripts.eval.run_demo_wave_gate import _aggregate, _execute_with_retry, _is_transient_error
from mech_chatbot.rag import interaction_router


pytestmark = pytest.mark.unit


def test_gate_aggregate_uses_answer_cases_for_source_rate():
    rows = [
        {"scenario": "positive_retrieval", "source_hit": True, "citation_or_refusal": True, "evidence_supported": True, "leakage": False, "passed": True},
        {"scenario": "department_denial", "source_hit": True, "citation_or_refusal": True, "evidence_supported": True, "leakage": False, "passed": True},
    ]
    result = _aggregate("Technical", rows)
    assert result["source_top5_rate"] == 1
    assert result["citation_or_refusal_rate"] == 1
    assert result["rbac_site_publication_leaks"] == 0


def test_transient_provider_error_is_retried_then_returns_success():
    case = {"id": "case-1", "department": "Technical", "scenario": "positive_retrieval"}
    success = {"case_id": "case-1", "passed": True}
    with patch("scripts.eval.run_demo_wave_gate._evaluate_one", side_effect=[RuntimeError("no_capacity"), success]) as evaluate:
        result = _execute_with_retry(case, "full", max_attempts=2, sleep=lambda _: None)

    assert result == success
    assert evaluate.call_count == 2


def test_non_transient_error_is_not_retried():
    case = {"id": "case-1", "department": "Technical", "scenario": "positive_retrieval"}
    with patch("scripts.eval.run_demo_wave_gate._evaluate_one", side_effect=ValueError("bad assertion")) as evaluate:
        result = _execute_with_retry(case, "full", max_attempts=4, sleep=lambda _: None)

    assert result["error"] == "bad assertion"
    assert evaluate.call_count == 1
    assert _is_transient_error("service_unavailable no_capacity") is True


def test_normalize_ignores_markdown_and_label_punctuation():
    from scripts.eval.run_demo_wave_gate import _contains_expected_evidence, _normalize

    assert _normalize("**Mức tồn tối thiểu: 25 đơn vị.**") == "mức tồn tối thiểu 25 đơn vị"
    assert _contains_expected_evidence("Thời gian thử việc tối đa là 60 ngày.", "thời gian thử việc 60 ngày")


def test_internal_regulation_question_uses_fast_route_without_embedding():
    calls = []
    result = interaction_router.classify(
        "Hãy nêu quy định chính của phòng Sales và dẫn nguồn",
        context={"allowed_departments": ["Sales"]},
        embedder=lambda text: calls.append(text) or [1.0, 0.0],
    )

    assert result.route == interaction_router.ROUTE_TECHNICAL
    assert result.layer == interaction_router.LAYER_RULE
    assert calls == []
