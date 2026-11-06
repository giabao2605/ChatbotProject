"""Source identity survives retrieval transports without conflating revisions."""
from langchain_core.documents import Document
import pytest

from mech_chatbot.rag.corrective import merge_corrected_documents
from mech_chatbot.rag.query_decomposition import merge_branch_documents


@pytest.mark.parametrize('merge', [
    lambda docs: merge_corrected_documents(docs[:1], docs[1:]),
    lambda docs: merge_branch_documents([docs[:1], docs[1:]]),
])
def test_merges_keep_revisions_locations_and_full_content(merge):
    base = {'doc_id': 7, 'version_no': 1, 'trang_so': 1, 'chunk_index': 0,
            'snapshot_id': 'snapshot-a', 'source_hash': 'a' * 64}
    first = Document(page_content='x' * 300 + ' first', metadata=base)
    distinct = [Document(page_content=first.page_content, metadata={**base, **change})
                for change in ({'doc_id': 8}, {'version_no': 2}, {'snapshot_id': 'snapshot-b'},
                               {'source_hash': 'b' * 64}, {'chunk_index': 1})]
    tail = Document(page_content='x' * 300 + ' different tail', metadata=base)
    duplicate = Document(page_content=first.page_content, metadata={**base, 'score': 0.9})
    assert merge([first, duplicate, *distinct, tail]) == [first, *distinct, tail]


def test_citation_transport_preserves_revisions_and_chunk_locations():
    from mech_chatbot.rag.phases.diagnostics import serialize_debug_documents
    from mech_chatbot.application.chat_citations import build_citation_list, attributed_rag_citations

    base = {'doc_id': 7, 'version_no': 1, 'trang_so': 1, 'chunk_index': 0,
            'file_goc': 'same.pdf', 'snapshot_id': 'snapshot-a', 'source_hash': 'a' * 64}
    docs = [Document(page_content='same text', metadata={**base, **change}) for change in (
        {}, {'version_no': 2}, {'snapshot_id': 'snapshot-b'}, {'chunk_index': 1}, {},
    )]
    records = serialize_debug_documents(docs)
    assert records[0]['snapshot_id'] == 'snapshot-a'
    assert records[0]['chunk_index'] == 0
    cards = build_citation_list(records, page_has_vision=lambda *_: False)
    assert len(cards) == 4
    assert [card['version_no'] for card in cards] == [1, 2, 1, 1]
    assert len({card['evidence_identity'] for card in cards}) == 4
    # A legacy page-only marker cannot choose between incompatible revisions.
    assert attributed_rag_citations({'citation_docs': records}, '[SRC:D7P1]') == ()


def test_page_citation_rejects_ambiguous_revision_but_keeps_same_page_chunks():
    from mech_chatbot.rag.answer_checks import has_valid_source_citation

    base = {'doc_id': 7, 'version_no': 1, 'trang_so': 1, 'snapshot_id': 'a', 'source_hash': 'a' * 64}
    docs = [Document(page_content='one', metadata=base),
            Document(page_content='two', metadata={**base, 'chunk_index': 2})]
    assert has_valid_source_citation('[SRC:D7P1]', docs, require_version=False)
    docs.append(Document(page_content='new', metadata={**base, 'snapshot_id': 'b'}))
    assert not has_valid_source_citation('[SRC:D7P1]', docs, require_version=False)


@pytest.mark.parametrize('change', ['digest', 'document', 'schema', 'oversize', 'missing_page'])
def test_history_reference_cannot_change_identity_or_invent_a_pin(change):
    import json
    from mech_chatbot.application.chat_citations import source_reference_record, restore_source_reference

    value = source_reference_record({'doc_id': 7, 'version_no': 2, 'trang': 1, 'chunk_index': 0})
    assert 'source_hash' not in value and 'snapshot_id' not in value
    if change == 'digest': value['chunk_index'] = 1
    if change == 'schema': value['schema'] = 'source-locator-v2'
    if change == 'missing_page': value['page_no'] = None
    payload = 'x' * 16385 if change == 'oversize' else json.dumps(value)
    with pytest.raises(ValueError):
        restore_source_reference(payload, doc_id=8 if change == 'document' else 7, version=2, page=1)


def test_source_reference_rejects_unreadable_size_before_persistence():
    from mech_chatbot.application.chat_citations import source_reference_record

    with pytest.raises(ValueError):
        source_reference_record({'doc_id': 7, 'version_no': 2, 'page_no': 1,
                                 'parent_section': 'x' * 16385})
