from langchain_core.documents import Document

from mech_chatbot.rag.claim_repair import repair_grounded_answer


def _doc():
    return Document(
        page_content="approved",
        metadata={"doc_id": 7, "trang_so": 3, "version_no": 1, "file_goc": "bom.pdf"},
    )


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
    assert "UNSUPPORTED_NUMBERS: ['2500']" in calls[0]
    assert "ALLOWED_NUMBERS: ['1500']" in calls[0]
    assert "ALLOWED_SOURCE_IDS: ['D7P3']" in calls[0]


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
