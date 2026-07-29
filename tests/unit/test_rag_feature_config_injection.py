from mech_chatbot.rag import (
    claim_repair,
    community_summaries,
    conversation_state,
    corrective,
    evidence_gate,
    graph_retrieval,
    interaction_router,
    route_config,
    route_llm,
    route_safety,
)


def test_feature_switches_use_explicit_process_values():
    assert claim_repair.claim_repair_enabled(False) is False
    assert claim_repair.claim_repair_enabled(True) is True
    assert corrective.correction_enabled(False) is False
    assert corrective.correction_enabled(True) is True
    assert graph_retrieval.enabled(False) is False
    assert graph_retrieval.enabled(True) is True
    assert community_summaries.enabled(False) is False
    assert community_summaries.enabled(True) is True
    assert conversation_state.is_enabled(False) is False
    assert conversation_state.is_enabled(True) is True
    assert conversation_state.history_summary_enabled(False) is False
    assert conversation_state.history_summary_enabled(True) is True


def test_evidence_gate_uses_explicit_crag_and_verifier_switches(monkeypatch):
    decision = evidence_gate.evaluate_answerability(
        "Chi phí gia công là bao nhiêu?",
        "Tài liệu kỹ thuật không có thông tin giá.",
        crag_enabled=True,
    )
    assert decision.state is evidence_gate.EvidenceState.AMBIGUOUS

    monkeypatch.setattr(
        evidence_gate,
        "cohere_invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verifier must remain disabled")
        ),
    )
    disabled = evidence_gate.evaluate_answerability(
        "Nội quy lao động quy định gì?",
        "Nội quy quy định thời giờ làm việc.",
        verifier_enabled=False,
    )
    assert disabled.telemetry_status == "verifier_disabled"


def test_low_risk_evidence_can_disable_strict_mode_explicitly():
    decision = evidence_gate.evaluate_answerability(
        "Xin chào",
        "",
        strict_answer_mode=False,
    )
    assert decision.state is evidence_gate.EvidenceState.SUFFICIENT


def test_router_policies_use_explicit_process_values():
    assert route_config.semantic_enabled(False) is False
    assert route_config.semantic_threshold(0.71) == 0.71
    assert route_config.semantic_margin(0.09) == 0.09
    assert route_llm.classify_llm("question", enabled=False) is None
    assert route_safety.detect(
        "custom forbidden phrase",
        extra_injection=("custom forbidden phrase",),
    ) == route_safety.REASON_PROMPT_INJECTION

    result = interaction_router.classify(
        "Mã cấu hình A-B-C-D là gì?",
        crag_fast_routes_enabled=True,
        semantic_enabled=False,
    )
    assert result.reason == "internal_configuration_code"
