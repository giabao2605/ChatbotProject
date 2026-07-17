from langchain_core.documents import Document

from mech_chatbot.rag.corrective import (
    correction_enabled,
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


def test_crag_rollback_flag_disables_correction_runtime(monkeypatch):
    monkeypatch.setenv("RAG_CRAG_ENABLED", "false")
    decision = AnswerDecision(
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        EvidenceState.AMBIGUOUS,
        reason="missing coverage",
        correction_allowed=True,
    )

    assert correction_enabled() is False
    assert should_attempt_correction(
        decision, attempts=0, enabled=correction_enabled(),
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
