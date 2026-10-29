from dataclasses import replace

import pytest
from sqlalchemy import text
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.services import rag_evidence_service as service

from mech_chatbot.domain.structured_evidence import TextFact, SourceLocator
from mech_chatbot.db.repositories.text_evidence import (
    save_extracted_text_facts, list_document_text_facts, review_text_fact,
)
from .test_structured_evidence_repository import PROFILE, store


@pytest.fixture
def text_store(store):
    with store.begin() as conn:
        conn.execute(text('CREATE TABLE StructuredTextFact(FactID TEXT PRIMARY KEY,SourceDocID INTEGER,SourceVersion INTEGER,SnapshotID TEXT,SourceHash TEXT,PayloadJson TEXT,ReviewedPayloadJson TEXT,ReviewStatus TEXT,ReviewedBy INTEGER,ReviewNote TEXT,UpdatedAt TEXT)'))
        conn.execute(text('CREATE TABLE ReviewedAssemblySequences(DocID INTEGER PRIMARY KEY,SourceVersion INTEGER,SnapshotID TEXT,SourceHash TEXT,ManifestJson TEXT,CompletenessReviewed INTEGER,ReviewedBy INTEGER,ReviewNote TEXT,UpdatedAt TEXT)'))
    return store


def material(snapshot='s1'):
    return TextFact('material-' + snapshot, 'material', 'Part A', 'Inox 304', 'Inox 304',
                    SourceLocator('1', '2', page=1, row_key='table-1:row-7', bbox=(1, 2, 3, 4)),
                    conflicts=('material_conflict',), candidates=('Description: Inox 201',),
                    source_hash='a' * 64, extraction_method='pdf_table')


def test_material_correction_requires_explicit_review_and_preserves_raw_source(text_store):
    candidate = replace(material(), review_status='approved')
    save_extracted_text_facts((candidate,), snapshot_id='s1', db_engine=text_store)
    page = list_document_text_facts(1, PROFILE, db_engine=text_store)
    assert page['items'][0]['review_status'] == 'pending'
    assert page['items'][0]['locator']['bbox'] == [1, 2, 3, 4]
    with pytest.raises(ValueError, match='Resolve conflicts'):
        review_text_fact(candidate.fact_id, PROFILE, action='approve', note='Checked', db_engine=text_store)
    result = review_text_fact(candidate.fact_id, PROFILE, action='approve', value_text='Inox 201',
                              note='Checked original drawing and confirmed grade', db_engine=text_store)
    assert result['raw_value'] == 'Inox 304' and result['value'] == 'Inox 201'
    assert result['review_status'] == 'approved' and result['conflicts'] == []
    assert result['candidates'] == ['Description: Inox 201']
    assert list_document_text_facts(1, PROFILE, db_engine=text_store)['items'][0] == result
    with bind_repository_runtime(db_engine=text_store):
        assert service.list_document_text_facts(1, PROFILE)['items'][0] == result
        rejected = service.review_text_fact(candidate.fact_id, PROFILE, action='reject', note='Further source review needed')
        assert rejected['review_status'] == 'rejected' and rejected['raw_value'] == 'Inox 304'


@pytest.mark.parametrize('change', [{'fact_id': ''}, {'source_hash': 'not-a-hash'}, {'extraction_method': ''}])
def test_unbound_text_cannot_be_saved(text_store, change):
    with pytest.raises(ValueError):
        save_extracted_text_facts((replace(material(), **change),), snapshot_id='s1', db_engine=text_store)


def test_invalid_or_duplicate_text_review_does_not_overwrite_extraction(text_store):
    with pytest.raises(ValueError):
        save_extracted_text_facts((material(), material()), snapshot_id='s1', db_engine=text_store)
    save_extracted_text_facts((material(),), snapshot_id='s1', db_engine=text_store)
    with pytest.raises(ValueError):
        review_text_fact('material-s1', PROFILE, action='approve', value_text='', note='Checked', db_engine=text_store)
    with pytest.raises(ValueError):
        review_text_fact('material-s1', PROFILE, action='approve', value_text='Inox 201', sequence_number=1, note='Checked', db_engine=text_store)
    with pytest.raises(PermissionError):
        review_text_fact('missing', PROFILE, action='reject', note='Checked', db_engine=text_store)


@pytest.mark.parametrize('profile', [
    {**PROFILE, 'allowed_sites': ['other']}, {**PROFILE, 'allowed_departments': ['HR']},
    {**PROFILE, 'user_id': 99}, {**PROFILE, 'username': 'forged'},
])
def test_text_review_checks_actual_reviewer_and_source_permissions(text_store, profile):
    save_extracted_text_facts((material(),), snapshot_id='s1', db_engine=text_store)
    with pytest.raises(PermissionError):
        list_document_text_facts(1, profile, db_engine=text_store)
    with pytest.raises(PermissionError):
        review_text_fact('material-s1', profile, action='approve', value_text='Inox 201',
                         note='Checked', db_engine=text_store)


def test_new_source_snapshot_invalidates_approved_text_and_stale_review(text_store):
    save_extracted_text_facts((material(),), snapshot_id='s1', db_engine=text_store)
    review_text_fact('material-s1', PROFILE, action='approve', value_text='Inox 201', note='Checked', db_engine=text_store)
    save_extracted_text_facts((material('s2'),), snapshot_id='s2', db_engine=text_store)
    page = list_document_text_facts(1, PROFILE, limit=1, db_engine=text_store)
    assert [f['fact_id'] for f in page['items']] == ['material-s2']
    assert page['items'][0]['review_status'] == 'pending'
    with pytest.raises(PermissionError):
        review_text_fact('material-s1', PROFILE, action='reject', note='Old version', db_engine=text_store)


def test_step_review_and_pagination_follow_recorded_order(text_store):
    base = replace(material(), kind='assembly_step', conflicts=(), candidates=(),
                   raw_value='Bước 03: Hàn', value='Hàn', sequence_number=3, locator=SourceLocator('1','2',page=1))
    first = replace(base, fact_id='z-first', raw_value='Bước 01: Gá', value='Gá', sequence_number=1)
    save_extracted_text_facts((base, first), snapshot_id='s1', db_engine=text_store)
    page = list_document_text_facts(1, PROFILE, limit=1, db_engine=text_store)
    assert page['items'][0]['sequence_number'] == 1 and page['has_more']
    with pytest.raises(ValueError):
        review_text_fact(base.fact_id, PROFILE, action='approve', sequence_number=0, note='Checked', db_engine=text_store)
    reviewed = review_text_fact(base.fact_id, PROFILE, action='approve', sequence_number=2,
                               value_text='Kiểm tra', note='Checked recorded order', db_engine=text_store)
    assert reviewed['sequence_number'] == 2 and reviewed['raw_value'] == 'Bước 03: Hàn'


def save_steps(text_store, numbers=(3, 1, 2), snapshot='s1'):
    facts = tuple(replace(material(snapshot), fact_id=f'step-{snapshot}-{i}', kind='assembly_step',
                          raw_value=f'Bước {number}: original', value=f'Operation {number}',
                          sequence_number=number, conflicts=(), candidates=())
                  for i, number in enumerate(numbers))
    save_extracted_text_facts(facts, snapshot_id=snapshot, db_engine=text_store)
    for fact in facts:
        review_text_fact(fact.fact_id, PROFILE, action='approve', note='Checked drawing', db_engine=text_store)
    return facts


def test_whole_sequence_requires_separate_attestation_and_keeps_operations_out_of_bom(text_store):
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence, load_assembly_sequence
    from mech_chatbot.rag.graph_retrieval_v2 import assembly_sequence_result

    save_steps(text_store)
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store)['status'] == 'unknown'
    review_assembly_sequence(1, PROFILE, snapshot_id='s1', expected_step_numbers=(1, 2, 3),
                             definition='Whole assembly on page 1', note='Checked every step', db_engine=text_store)
    loaded = load_assembly_sequence(1, PROFILE, db_engine=text_store)
    result = assembly_sequence_result(loaded)
    assert result['status'] == 'complete'
    assert [step['sequence_number'] for step in result['manifest']['steps']] == [1, 2, 3]
    assert [(edge['source_step'], edge['target_step']) for edge in result['edges']] == [(1, 2), (2, 3)]
    assert {edge['relation_type'] for edge in result['edges']} == {'PRECEDES_OPERATION'}
    assert result['edges'][0]['sources'][0]['source_quote'] == 'Bước 1: original'
    assert result['edges'][0]['sources'][0]['doc_id'] == 1
    assert result['edges'][0]['sources'][0]['source_hash'] == 'a' * 64
    assert 'occurrences' not in result['manifest'] and 'quantity' not in str(result)
    with bind_repository_runtime(db_engine=text_store):
        assert service.assembly_sequence(1, PROFILE)['status'] == 'complete'


def caption_steps():
    return tuple(replace(material(), fact_id=f'caption-{number}', kind='assembly_step',
                         raw_value=f'Bước {number}: caption original', value=f'Caption {number}',
                         sequence_number=number, conflicts=(), candidates=(),
                         locator=SourceLocator('1', '2', page=1, row_key=f'page-1:step-{number}',
                             bbox=(17.123456789, 25.987654321, 103.123456789, 78.89056720000008)))
                 for number in (6, 2, 5, 1, 4, 3))


def test_reviewed_six_step_sources_preserve_caption_regions_and_row_identity(text_store):
    facts = caption_steps()
    save_extracted_text_facts(facts, snapshot_id='s1', db_engine=text_store)
    with bind_repository_runtime(db_engine=text_store):
        assert service.assembly_sequence(1, PROFILE)['manifest'] is None
        for fact in facts:
            service.review_text_fact(fact.fact_id, PROFILE, action='approve',
                                     note='Synthetic caption fixture; not business acceptance')
        service.review_assembly_sequence(1, PROFILE, snapshot_id='s1',
            expected_step_numbers=(1, 2, 3, 4, 5, 6), definition='Six fixture captions',
            note='Explicit fixture completeness; not human acceptance')
        result = service.assembly_sequence(1, PROFILE)
    assert result['status'] == 'complete'
    assert [(edge['source_step'], edge['target_step']) for edge in result['edges']] == [
        (1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]
    assert {edge['relation_type'] for edge in result['edges']} == {'PRECEDES_OPERATION'}
    sources = result['edges'][-1]['sources']
    assert [source['row_key'] for source in sources] == ['page-1:step-5', 'page-1:step-6']
    assert all(source['bbox'] == [17.123456789, 25.987654321, 103.123456789, 78.89056720000008]
               for edge in result['edges'] for source in edge['sources'])
    assert all(source['doc_id'] == 1 and source['version'] == 2 and source['page'] == 1
               and source['source_hash'] == 'a' * 64 and source['snapshot_id'] == 's1'
               for edge in result['edges'] for source in edge['sources'])
    assert [source['source_quote'] for source in sources] == [
        'Bước 5: caption original', 'Bước 6: caption original']


def test_sequence_rejects_missing_duplicate_pending_and_stale_steps(text_store):
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence
    facts = save_steps(text_store)
    values = dict(snapshot_id='s1', definition='All assembly steps', note='Checked whole page', db_engine=text_store)
    for numbers in ((1, 2), (1, 2, 2), (1, 2, 4), (True, 2, 3), (), tuple(range(1, 502))):
        with pytest.raises(ValueError):
            review_assembly_sequence(1, PROFILE, expected_step_numbers=numbers, **values)
    with pytest.raises(PermissionError):
        review_assembly_sequence(1, PROFILE, expected_step_numbers=(1, 2, 3), **{**values, 'snapshot_id': 'old'})
    review_text_fact(facts[0].fact_id, PROFILE, action='reject', note='Not supported by source', db_engine=text_store)
    with pytest.raises(ValueError):
        review_assembly_sequence(1, PROFILE, expected_step_numbers=(1, 2, 3), **values)


def test_sequence_attestation_is_invalidated_by_step_edit_and_new_snapshot(text_store):
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence, load_assembly_sequence
    facts = save_steps(text_store)
    values = dict(snapshot_id='s1', expected_step_numbers=(1, 2, 3), definition='All steps', note='Checked source', db_engine=text_store)
    review_assembly_sequence(1, PROFILE, **values)
    review_text_fact(facts[0].fact_id, PROFILE, action='approve', value_text='Revised operation', note='Checked again', db_engine=text_store)
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store) == {'status': 'unknown', 'manifest': None}
    review_assembly_sequence(1, PROFILE, **values)
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store)['status'] == 'complete'
    save_steps(text_store, snapshot='s2')
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store)['status'] == 'unknown'


def test_sequence_never_exposes_denied_or_retired_source(text_store):
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence, load_assembly_sequence
    save_steps(text_store)
    values = dict(snapshot_id='s1', expected_step_numbers=(1, 2, 3), definition='All steps', note='Checked', db_engine=text_store)
    for profile in ({**PROFILE, 'allowed_sites': ['other']}, {**PROFILE, 'user_id': 99}, {**PROFILE, 'username': 'forged'}):
        with pytest.raises(PermissionError):
            review_assembly_sequence(1, profile, **values)
        with pytest.raises(PermissionError):
            load_assembly_sequence(1, profile, review=True, db_engine=text_store)
    review_assembly_sequence(1, PROFILE, **values)
    with bind_repository_runtime(db_engine=text_store):
        assert service.assembly_sequence(1, {**PROFILE, 'allowed_departments': ['HR']})['manifest'] is None
        with text_store.begin() as conn:
            conn.execute(text("UPDATE TaiLieu SET IsCurrent=0 WHERE DocID=1"))
        result = service.assembly_sequence(1, PROFILE)
        assert result['status'] == 'unknown' and result['edges'] == () and result['manifest'] is None


def test_generation_cap_does_not_cut_reviewed_sequence_manifest(text_store):
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence
    save_steps(text_store, tuple(range(1, 63)))
    with bind_repository_runtime(db_engine=text_store):
        service.review_assembly_sequence(1, PROFILE, snapshot_id='s1', expected_step_numbers=tuple(range(1, 63)),
                                         definition='All sixty-two steps', note='Checked all pages')
        result = service.assembly_sequence(1, PROFILE)
        assert result['status'] == 'complete' and result['context_truncated']
        assert len(result['edges']) == 50 and result['examined_relations'] == 61
        assert len(result['manifest']['steps']) == 62
        assert service.list_assembly_sequence(1, PROFILE)['expected_step_numbers'] == tuple(range(1, 63))


def test_sequence_requires_recorded_page_and_quote_even_after_field_approval(text_store):
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence
    candidate = replace(material(), kind='assembly_step', fact_id='step-no-quote', conflicts=(), candidates=(),
                        sequence_number=1, raw_value='', value='Operation', locator=SourceLocator('1','2',page=1))
    save_extracted_text_facts((candidate,), snapshot_id='s1', db_engine=text_store)
    review_text_fact(candidate.fact_id, PROFILE, action='approve', note='Checked', db_engine=text_store)
    with pytest.raises(ValueError):
        review_assembly_sequence(1, PROFILE, snapshot_id='s1', expected_step_numbers=(1,),
                                 definition='All steps', note='Checked source', db_engine=text_store)


def test_supplied_six_step_drawing_requires_review_before_serving_order(text_store):
    import hashlib
    import pdfplumber
    from .test_drawing_sample_evidence import sample
    from mech_chatbot.ingestion.pdf.bom import extract_assembly_steps, assembly_text_facts
    from mech_chatbot.db.repositories.text_evidence import review_assembly_sequence

    path = sample('9.3.04080', '-Model11.pdf')
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with pdfplumber.open(path) as drawing:
        steps = extract_assembly_steps(drawing.pages[0].extract_text(), SourceLocator('1','2',page=1))
    facts = assembly_text_facts(steps, snapshot_id='drawing-snapshot', source_hash=source_hash)
    save_extracted_text_facts(facts, snapshot_id='drawing-snapshot', db_engine=text_store)
    with bind_repository_runtime(db_engine=text_store):
        assert service.assembly_sequence(1, PROFILE)['status'] == 'unknown'
        for fact in facts:
            service.review_text_fact(fact.fact_id, PROFILE, action='approve', note='Offline fixture review of supplied page')
        service.review_assembly_sequence(1, PROFILE, snapshot_id='drawing-snapshot',
            expected_step_numbers=(1,2,3,4,5,6), definition='Six assembly steps on supplied Model11 page 1',
            note='Offline test fixture; not human business acceptance')
        result = service.assembly_sequence(1, PROFILE)
    assert result['status'] == 'complete'
    assert [(e['source_step'],e['target_step']) for e in result['edges']] == [(1,2),(2,3),(3,4),(4,5),(5,6)]
    assert 'Hàn nối cụm trên' in result['manifest']['steps'][0]['value']
    assert all(s['source_hash'] == source_hash for e in result['edges'] for s in e['sources'])
