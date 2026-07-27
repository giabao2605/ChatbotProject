from langchain_core.documents import Document

from mech_chatbot.rag.claim_repair import claim_repair_enabled, repair_grounded_answer


def _doc():
    return Document(
        page_content="approved",
        metadata={"doc_id": 7, "trang_so": 3, "version_no": 1, "file_goc": "bom.pdf"},
    )


def test_claim_repair_rollback_flag_disables_runtime():
    assert claim_repair_enabled(False) is False


def test_claim_repair_accepts_one_grounded_rewrite():
    calls = []

    result = repair_grounded_answer(
        "Chi phí 2500 USD.",
        context_text="Chi phí 1500 USD.",
        question="Chi phí bao nhiêu?",
        documents=[_doc()],
        invoke=lambda prompt: calls.append(prompt) or "Chi phí 1,500 USD. [Nguồn: bom.pdf, Trang 3, Version 1, SourceID D7P3]",
        require_citation=True,
        enabled=True,
    )

    assert result.attempted is True
    assert result.accepted is True
    assert "1,500" in result.answer
    assert len(calls) == 1
    assert result.estimated_cost > 0
    assert "UNSUPPORTED_NUMBERS: ['2500']" in calls[0]
    assert "ALLOWED_NUMBERS: ['1500']" in calls[0]
    assert "ALLOWED_SOURCE_IDS: ['D7P3']" in calls[0]


def test_claim_repair_replaces_one_number_from_a_matching_source_sentence_without_provider():
    result = repair_grounded_answer(
        "Giá trị định mức là 1,501.",
        context_text=(
            "Giá trị định mức là 1,500 đơn vị. "
            "Khe hở chuẩn là 12,50 mm. Phiên bản hiện hành là 12."
        ),
        question="Giá trị định mức là bao nhiêu?",
        documents=[_doc()],
        invoke=lambda _prompt: (_ for _ in ()).throw(
            AssertionError("deterministic repair must not call the provider")
        ),
        require_citation=False,
        enabled=True,
        allow_deterministic=True,
    )

    assert result.attempted is True
    assert result.accepted is True
    assert result.answer == "Giá trị định mức là 1,500."
    assert result.estimated_cost == 0.0


def test_claim_repair_keeps_provider_fallback_when_source_number_is_ambiguous():
    calls = []
    result = repair_grounded_answer(
        "Chi phí là 2,500 USD.",
        context_text="Chi phí là 1,500 USD. Chi phí là 1,700 USD.",
        question="Chi phí là bao nhiêu?",
        documents=[_doc()],
        invoke=lambda prompt: calls.append(prompt) or "Chi phí là 1,500 USD.",
        require_citation=False,
        enabled=True,
        allow_deterministic=True,
    )

    assert result.attempted is True
    assert result.accepted is True
    assert len(calls) == 1
    assert result.estimated_cost > 0


def test_claim_repair_does_not_match_reordered_source_words():
    calls = []
    result = repair_grounded_answer(
        "Chi phí vận chuyển là 2,500 USD.",
        context_text="Chi phí là 1,500 USD, không gồm vận chuyển.",
        question="Chi phí vận chuyển là bao nhiêu?",
        documents=[_doc()],
        invoke=lambda prompt: calls.append(prompt) or "Không có chi phí vận chuyển.",
        require_citation=False,
        enabled=True,
        allow_deterministic=True,
    )

    assert result.attempted is True
    assert len(calls) == 1


def test_claim_repair_rejects_second_unsupported_answer_without_retrying():
    calls = []
    result = repair_grounded_answer(
        "Chi phí 2500 USD.",
        context_text="Chi phí 1500 USD.",
        question="Chi phí bao nhiêu?",
        documents=[_doc()],
        invoke=lambda prompt: calls.append(prompt) or "Chi phí 3000 USD.",
        require_citation=True,
        enabled=True,
    )

    assert result.attempted is True
    assert result.accepted is False
    assert result.violation_reason in {"numbers", "citation"}
    assert len(calls) == 1


def test_claim_repair_must_preserve_complete_rendered_citation():
    original_citation = "[Nguồn: bom.pdf, Trang 3, Version 1, SourceID D7P3]"
    result = repair_grounded_answer(
        f"Chi phí 2500 USD. {original_citation}",
        context_text="Chi phí 1500 USD.",
        question="Chi phí bao nhiêu?",
        documents=[_doc()],
        invoke=lambda _prompt: "Chi phí 1500 USD. [Nguồn: other.pdf, Trang 3, Version 1, SourceID D7P3]",
        require_citation=True,
        enabled=True,
    )

    assert result.accepted is False
    assert result.violation_reason == "citation"


def test_claim_repair_rejects_new_source_id_when_source_cards_are_deferred():
    result = repair_grounded_answer(
        "Chi phí 2500 USD.",
        context_text="Chi phí 1500 USD.",
        question="Chi phí bao nhiêu?",
        documents=[_doc()],
        invoke=lambda _prompt: (
            "Chi phí 1500 USD. "
            "[Nguồn: fake.pdf, Trang 9, Version 99, SourceID D999P9]"
        ),
        require_citation=False,
        enabled=True,
    )

    assert result.accepted is False
    assert result.violation_reason == "citation"


def test_claim_repair_rejects_wrong_version_for_allowed_source_id():
    result = repair_grounded_answer(
        "Chi phí 2500 USD.",
        context_text="Chi phí 1500 USD.",
        question="Chi phí bao nhiêu?",
        documents=[_doc()],
        invoke=lambda _prompt: (
            "Chi phí 1500 USD. "
            "[Nguồn: bom.pdf, Trang 3, Version 99, SourceID D7P3]"
        ),
        require_citation=False,
        enabled=True,
    )

    assert result.accepted is False
    assert result.violation_reason == "citation"


def test_claim_repair_rejects_invented_filename_for_allowed_source_id():
    result = repair_grounded_answer(
        "Chi phí 2500 USD.",
        context_text="Chi phí 1500 USD.",
        question="Chi phí bao nhiêu?",
        documents=[_doc()],
        invoke=lambda _prompt: (
            "Chi phí 1500 USD. "
            "[Nguồn: fake.pdf, Trang 3, Version 1, SourceID D7P3]"
        ),
        require_citation=False,
        enabled=True,
    )

    assert result.accepted is False
    assert result.violation_reason == "citation"


def test_claim_repair_rejects_source_card_without_source_id_when_deferred():
    result = repair_grounded_answer(
        "Chi phí 2500 USD.",
        context_text="Chi phí 1500 USD.",
        question="Chi phí bao nhiêu?",
        documents=[_doc()],
        invoke=lambda _prompt: (
            "Chi phí 1500 USD. [Nguồn: fake.pdf, Trang 9, Version 99]"
        ),
        require_citation=False,
        enabled=True,
    )

    assert result.accepted is False
    assert result.violation_reason == "citation"
