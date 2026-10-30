from dataclasses import replace

import pytest
from sqlalchemy import text

from mech_chatbot.db.repositories.text_evidence import (
    list_document_text_facts, review_text_fact, save_extracted_text_facts,
    review_assembly_sequence, load_assembly_sequence,
)
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.services import rag_evidence_service as service
from .test_structured_evidence_repository import PROFILE, store
from .test_text_evidence_repository import material, save_steps, text_store


def drawing_note(**changes):
    return replace(material(), fact_id='note-1', kind='drawing_note', raw_value='Hàn đầy',
                   value='Hàn đầy', conflicts=(), candidates=(), **changes)


def test_note_review_requires_explicit_assignment_and_preserves_source(text_store):
    step = save_steps(text_store, (1,))[0]
    candidate = drawing_note(review_status='approved', linked_step_fact_id=step.fact_id)
    save_extracted_text_facts((candidate,), snapshot_id='s1', db_engine=text_store)
    item = next(x for x in list_document_text_facts(1, PROFILE, db_engine=text_store)['items'] if x['kind'] == 'drawing_note')
    assert item['review_status'] == 'pending' and item['linked_step_fact_id'] is None
    with pytest.raises(ValueError, match='explicit'):
        review_text_fact(candidate.fact_id, PROFILE, action='approve', note='Checked', db_engine=text_store)
    result = review_text_fact(candidate.fact_id, PROFILE, action='approve',
                             linked_step_fact_id=step.fact_id, note='Checked leader', db_engine=text_store)
    assert result['linked_step_fact_id'] == step.fact_id
    assert result['raw_value'] == candidate.raw_value
    assert result['locator'] == item['locator'] and result['extraction_method'] == candidate.extraction_method
    global_note = review_text_fact(candidate.fact_id, PROFILE, action='approve', linked_step_fact_id=None,
                                  note='Applies to whole drawing', db_engine=text_store)
    assert global_note['linked_step_fact_id'] is None


def attest(text_store):
    return review_assembly_sequence(1, PROFILE, snapshot_id='s1', expected_step_numbers=(1, 2),
                                   definition='Whole drawing', note='Checked all notes', db_engine=text_store)


def test_pending_notes_block_sequence_and_reviewed_notes_are_source_bound(text_store):
    steps = save_steps(text_store, (1, 2))
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=text_store)
    with pytest.raises(ValueError):
        attest(text_store)
    review_text_fact('note-1', PROFILE, action='approve', linked_step_fact_id=steps[0].fact_id,
                     note='Checked leader', db_engine=text_store)
    attest(text_store)
    with bind_repository_runtime(db_engine=text_store):
        result = service.assembly_sequence(1, PROFILE)
        assert result['status'] == 'complete'
        assert result['manifest']['steps'][0]['notes'][0]['value'] == 'Hàn đầy'
        assert result['edges'][0]['sources'][1]['row_key'] == 'table-1:row-7'
        assert result['edges'][0]['sources'][1]['bbox'] == [1, 2, 3, 4]
        assert service.list_assembly_sequence(1, PROFILE)['step_options'] == [
            {'fact_id': step.fact_id, 'sequence_number': i, 'label': f'Operation {i}', 'review_status': 'approved'}
            for i, step in enumerate(steps, 1)]
    review_text_fact('note-1', PROFILE, action='approve', linked_step_fact_id=None,
                     value_text='Hàn đầy toàn bản vẽ', note='Corrected scope', db_engine=text_store)
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store)['status'] == 'unknown'
    attest(text_store)
    manifest = load_assembly_sequence(1, PROFILE, db_engine=text_store)['manifest']
    assert manifest['notes'][0]['value'] == 'Hàn đầy toàn bản vẽ'
    assert all('notes' not in step for step in manifest['steps'])
    review_text_fact('note-1', PROFILE, action='reject', note='Not applicable', db_engine=text_store)
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store)['status'] == 'unknown'
    assert 'notes' not in attest(text_store)['manifest']


def test_legacy_sequence_fingerprint_is_preserved_without_notes(text_store):
    # Frozen legacy payload from the existing two-step fixture, before adding note links.
    save_steps(text_store, (1, 2))
    assert attest(text_store)['manifest']['step_fingerprint'] == '601f5f390e9d452757115b1422dee5ed91fef4bb2377e884ac628abacc98e13d'


@pytest.mark.parametrize('field,value', [('page', None), ('page', True), ('bbox', None),
    ('bbox', (0, 1, 0, 2)), ('bbox', (0, 0, float('inf'), 2)), ('bbox', (0, False, 1, 2))])
def test_note_requires_a_finite_page_region(field, value):
    with pytest.raises(ValueError):
        drawing_note(locator=replace(material().locator, **{field: value}))


@pytest.mark.parametrize('link', ['', ' ', 'x' * 451, 1, False])
def test_invalid_note_assignment_is_rejected(text_store, link):
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=text_store)
    with pytest.raises(ValueError):
        review_text_fact('note-1', PROFILE, action='approve', linked_step_fact_id=link,
                         note='Checked', db_engine=text_store)


@pytest.mark.parametrize('change', [{'kind': 'material', 'sequence_number': None}, {'review_status': 'pending'}])
def test_note_cannot_link_wrong_kind_or_unapproved_step(text_store, change):
    step = save_steps(text_store, (1,))[0]
    with text_store.begin() as conn:
        from mech_chatbot.db.repositories.structured_evidence import _json
        conn.execute(text('UPDATE StructuredTextFact SET ReviewedPayloadJson=:payload WHERE FactID=:id'),
                     {'id': step.fact_id, 'payload': _json(replace(step, **{'snapshot_id': 's1', 'review_status': 'approved', **change}))})
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=text_store)
    with pytest.raises(ValueError):
        review_text_fact('note-1', PROFILE, action='approve', linked_step_fact_id=step.fact_id,
                         note='Checked', db_engine=text_store)


@pytest.mark.parametrize('column,value', [('SourceDocID', 2), ('SourceVersion', 3),
    ('SnapshotID', 'other'), ('SourceHash', 'b' * 64)])
def test_note_cannot_link_another_or_stale_source(text_store, column, value):
    step = save_steps(text_store, (1,))[0]
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=text_store)
    with text_store.begin() as conn:
        conn.execute(text(f'UPDATE StructuredTextFact SET {column}=:value WHERE FactID=:id'),
                     {'value': value, 'id': step.fact_id})
    with pytest.raises(PermissionError, match='^Evidence source unavailable$'):
        review_text_fact('note-1', PROFILE, action='approve', linked_step_fact_id=step.fact_id,
                         note='Checked', db_engine=text_store)


def test_note_link_rechecks_acl_and_step_rejection_invalidates_sequence(text_store):
    steps = save_steps(text_store, (1, 2))
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=text_store)
    with pytest.raises(PermissionError):
        review_text_fact('note-1', {**PROFILE, 'allowed_sites': ['other']}, action='approve',
                         linked_step_fact_id=steps[0].fact_id, note='Checked', db_engine=text_store)
    review_text_fact('note-1', PROFILE, action='approve', linked_step_fact_id=steps[0].fact_id,
                     note='Checked', db_engine=text_store)
    attest(text_store)
    review_text_fact(steps[0].fact_id, PROFILE, action='reject', note='Rejected step', db_engine=text_store)
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store) == {'status': 'unknown', 'manifest': None}
    with pytest.raises(ValueError):
        replace(material(), linked_step_fact_id=steps[0].fact_id)


def test_new_pending_note_invalidates_previous_complete_sequence(text_store):
    save_steps(text_store, (1, 2))
    attest(text_store)
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=text_store)
    assert load_assembly_sequence(1, PROFILE, db_engine=text_store)['manifest'] is None
    with bind_repository_runtime(db_engine=text_store):
        assert len(service.list_assembly_sequence(1, PROFILE)['step_options']) == 2


def test_combined_note_and_step_budget_cannot_attest_truncated_source(text_store):
    save_steps(text_store, (1, 2))
    notes = tuple(replace(drawing_note(), fact_id=f'note-{i}') for i in range(499))
    save_extracted_text_facts(notes, snapshot_id='s1', db_engine=text_store)
    with pytest.raises(ValueError):
        attest(text_store)


def test_unapproved_steps_remain_visible_as_review_options(text_store):
    steps = save_steps(text_store, (1, 2))
    candidate = replace(steps[0], fact_id='new-step', sequence_number=3)
    save_extracted_text_facts((candidate,), snapshot_id='s1', db_engine=text_store)
    with bind_repository_runtime(db_engine=text_store):
        options = service.list_assembly_sequence(1, PROFILE)['step_options']
    assert options[-1]['fact_id'] == 'new-step' and options[-1]['review_status'] == 'pending'


@pytest.mark.parametrize('tampered_id', ['note-1', 'step-s1-0'])
@pytest.mark.parametrize('field,value', [('snapshot_id', 'other'), ('source_hash', 'b' * 64),
    ('locator', {'doc_id': '2', 'version': '3', 'page': 1, 'bbox': [1, 2, 3, 4]})])
def test_assignment_never_accepts_a_payload_that_drifted_from_source_row(text_store, tampered_id, field, value):
    import json

    step = save_steps(text_store, (1,))[0]
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=text_store)
    with text_store.begin() as conn:
        row = conn.execute(text('SELECT COALESCE(ReviewedPayloadJson,PayloadJson) FROM StructuredTextFact WHERE FactID=:id'),
                           {'id': tampered_id}).scalar()
        payload = {**json.loads(row), field: value}
        conn.execute(text('UPDATE StructuredTextFact SET ReviewedPayloadJson=:payload WHERE FactID=:id'),
                     {'id': tampered_id, 'payload': json.dumps(payload)})
    with pytest.raises(PermissionError, match='^Evidence source unavailable$'):
        review_text_fact('note-1', PROFILE, action='approve', linked_step_fact_id=step.fact_id,
                         note='Checked', db_engine=text_store)
