from types import SimpleNamespace

import pytest

from mech_chatbot.rag.rerank import diversify_candidates


pytestmark = pytest.mark.unit


def _doc(doc_id, text):
    return SimpleNamespace(page_content=text, metadata={"doc_id": doc_id})


def test_diversify_deduplicates_and_caps_per_document():
    docs = [
        _doc(1, "same"),
        _doc(1, "same"),
        _doc(1, "second"),
        _doc(1, "third"),
        _doc(2, "other"),
    ]

    result = diversify_candidates(docs, max_per_document=2, cap=10)

    assert [(d.metadata["doc_id"], d.page_content) for d in result] == [
        (1, "same"),
        (1, "second"),
        (2, "other"),
    ]


def test_diversify_respects_global_cap():
    docs = [_doc(index, f"text-{index}") for index in range(10)]
    assert len(diversify_candidates(docs, max_per_document=4, cap=3)) == 3


def test_diversify_retains_identical_text_with_different_source_provenance():
    docs = [_doc(1, "same"), _doc(2, "same"), _doc(1, "same")]
    docs[0].metadata.update(version_no=1, trang_so=1, parent_section="same section")
    docs[1].metadata.update(version_no=1, trang_so=1, parent_section="same section")
    docs[2].metadata.update(version_no=2, trang_so=1, parent_section="same section")

    assert diversify_candidates(docs, max_per_document=4, cap=4) == docs


def test_diversify_and_shadow_keys_do_not_conflate_snapshots_or_rows():
    from mech_chatbot.rag.late_interaction import candidate_key
    docs = [SimpleNamespace(page_content='same', metadata={
        'doc_id': 1, 'version_no': 1, 'trang_so': 1, 'snapshot_id': snapshot,
        'source_hash': 'a' * 64, 'row_key': row,
    }) for snapshot, row in [('a', 'row1'), ('b', 'row1'), ('b', 'row2')]]
    assert diversify_candidates(docs, max_per_document=4, max_per_section=4) == docs
    assert len({candidate_key(doc) for doc in docs}) == 3


def test_diversify_keeps_separate_pages_with_same_parent_section():
    docs = [
        SimpleNamespace(page_content=text, metadata={
            "doc_id": 1148, "parent_section": "QA_B", "parent_page": page,
        })
        for text, page in (("page 2", 2), ("page 2 summary", 2), ("page 1", 1))
    ]

    assert diversify_candidates(
        docs, max_per_document=4, max_per_section=1, cap=4,
    ) == [docs[0], docs[2]]


def test_diversify_gives_each_relevant_document_a_candidate_before_long_file_chunks():
    long_file = [_doc(1, f"long-{index}") for index in range(8)]
    short_files = [_doc(index, f"short-{index}") for index in (2, 3)]

    result = diversify_candidates(long_file + short_files, max_per_document=4, cap=6)

    assert [doc.metadata["doc_id"] for doc in result] == [1, 1, 1, 1, 2, 3]
