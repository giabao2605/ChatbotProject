import ast
from pathlib import Path

import pytest
from langchain_core.documents import Document

from mech_chatbot.rag.corrective import (
    correction_enabled,
    metadata_correction_query,
    merge_corrected_documents,
    run_corrected_retrieval,
    should_attempt_correction,
)
from mech_chatbot.rag.answer_policy import AnswerDecision, AnswerOutcome
from mech_chatbot.rag.evidence_gate import EvidenceState


def test_correction_budget_allows_exactly_one_ambiguous_retry():
    decision = AnswerDecision(
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        EvidenceState.AMBIGUOUS,
        reason="missing coverage",
        correction_allowed=True,
    )

    assert should_attempt_correction(decision, attempts=0, enabled=True) is True
    assert should_attempt_correction(decision, attempts=1, enabled=True) is False
    assert should_attempt_correction(decision, attempts=0, enabled=False) is False


def test_crag_rollback_flag_disables_correction_runtime():
    decision = AnswerDecision(
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        EvidenceState.AMBIGUOUS,
        reason="missing coverage",
        correction_allowed=True,
    )

    assert correction_enabled(False) is False
    assert should_attempt_correction(
        decision, attempts=0, enabled=correction_enabled(False),
    ) is False


def test_ambiguous_state_does_not_override_policy_that_forbids_correction():
    decision = AnswerDecision(
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        EvidenceState.AMBIGUOUS,
        reason="explicit negative evidence",
        correction_allowed=False,
    )

    assert should_attempt_correction(decision, attempts=0, enabled=True) is False


def test_corrected_documents_are_deduplicated_without_changing_metadata():
    original = Document(page_content="same", metadata={"doc_id": 1, "trang_so": 2, "site": "HQ"})
    duplicate = Document(page_content="same updated", metadata={"doc_id": 1, "trang_so": 2, "site": "HQ"})
    added = Document(page_content="new", metadata={"doc_id": 2, "trang_so": 1, "site": "HQ"})

    merged = merge_corrected_documents([original], [duplicate, added])

    assert merged == [original, added]
    assert merged[1].metadata["site"] == "HQ"


def test_metadata_correction_query_adds_top_governed_document_code_locally():
    documents = [
        Document(
            page_content="Mắt cú xanh là biệt danh đã phê duyệt.",
            metadata={"base_code": "crag-eval-alias-001"},
        ),
        Document(
            page_content="lower-ranked evidence",
            metadata={"base_code": "crag-eval-other-001"},
        ),
    ]

    corrected = metadata_correction_query(
        "Mắt cú xanh cần kiểm tra theo chu kỳ bao lâu?",
        documents,
    )

    assert corrected == (
        "Mắt cú xanh cần kiểm tra theo chu kỳ bao lâu? "
        "crag-eval-alias-001"
    )


@pytest.mark.parametrize(
    ("question", "documents"),
    [
        ("query", []),
        (
            "query",
            [Document(page_content="x", metadata={"base_code": "unsafe code"})],
        ),
        (
            "Mắt cú xanh kiểm tra khi nào?",
            [
                Document(
                    page_content="Tài liệu hoàn toàn không liên quan.",
                    metadata={"base_code": "crag-eval-alias-001"},
                )
            ],
        ),
        (
            "Thông số CRAG-EVAL-ALIAS-001",
            [
                Document(
                    page_content="x",
                    metadata={"base_code": "crag-eval-alias-001"},
                )
            ],
        ),
    ],
)
def test_metadata_correction_query_falls_back_when_no_new_safe_code(
    question,
    documents,
):
    assert metadata_correction_query(question, documents) is None


def test_corrected_retrieval_reuses_governance_filters_unchanged():
    strict_filter = object()
    broad_filter = object()
    rbac_filter = object()
    observed = {}

    def retrieve(**kwargs):
        observed.update(kwargs)
        return [Document(page_content="result")], 30, "general", 0, object()

    result = run_corrected_retrieval(
        retrieve,
        corrected_query="rewritten query",
        new_part_ids=["P-1"],
        strict_filter=strict_filter,
        broad_filter=broad_filter,
        rbac_filter=rbac_filter,
        is_bom_query=False,
        trace_id="trace-1",
    )

    assert result[0][0].page_content == "result"
    assert observed["strict_filter"] is strict_filter
    assert observed["broad_filter"] is broad_filter
    assert observed["rbac_filter"] is rbac_filter


def test_corrective_query_rewrites_use_approved_disambiguation_surface():
    rag_root = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "mech_chatbot"
        / "rag"
    )
    surfaces = []
    for source_path in rag_root.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "surface" and isinstance(keyword.value, ast.Constant):
                    surfaces.append(keyword.value.value)

    assert "corrective_retrieval" not in surfaces
    assert surfaces.count("query_disambiguation") >= 2
