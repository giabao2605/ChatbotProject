from dataclasses import replace

import pytest

from mech_chatbot.db.repositories import text_evidence as repo
from .test_drawing_note_review import drawing_note, attest
from .test_text_evidence_repository import material, save_steps, text_store
from .test_structured_evidence_repository import PROFILE, store


def prepare(db):
    symbol = replace(material(), fact_id='symbol-19', kind='symbol', raw_value='P19',
                     value='P19', bom_position='19', conflicts=(), candidates=())
    repo.save_extracted_text_facts((symbol, drawing_note()), snapshot_id='s1', db_engine=db)
    repo.review_text_fact(symbol.fact_id, PROFILE, action='approve', note='Checked', db_engine=db)
    return symbol


def approve(db):
    return repo.review_text_fact('note-1', PROFILE, action='approve', linked_bom_fact_id='symbol-19',
                                note='Checked leader and row', db_engine=db)


def note(db):
    return next(x for x in repo.list_document_text_facts(1, PROFILE, db_engine=db)['items']
                if x['fact_id'] == 'note-1')


def test_review_and_paginated_options(text_store):
    prepare(text_store)
    result = approve(text_store)
    assert result['linked_bom_fact_id'] == 'symbol-19'
    assert note(text_store)['linked_bom_status'] == 'valid'
    page = repo.list_bom_reference_options(1, PROFILE, limit=1, db_engine=text_store)
    assert page['items'][0]['fact_id'] == 'symbol-19'
    assert page['items'][0]['bom_position'] == '19'
    assert not page['has_more']
    assert repo.list_bom_reference_options(1, PROFILE, offset=1, db_engine=text_store)['items'] == []


@pytest.mark.parametrize('change', [{'action':'reject'}, {'action':'approve', 'value_text':'P20'}])
def test_changed_target_is_not_served_as_approved_link(text_store, change):
    prepare(text_store)
    approve(text_store)
    repo.review_text_fact('symbol-19', PROFILE, note='Updated source decision', db_engine=text_store, **change)
    item = note(text_store)
    assert item['linked_bom_status'] == 'unknown'
    assert item['linked_bom_fact_id'] is None
    assert 'linked_bom_fingerprint' not in item


def test_link_cannot_be_step_and_bom_or_non_note(text_store):
    prepare(text_store)
    for fact, extra in [('note-1', {'linked_step_fact_id':'step-1'}), ('symbol-19', {})]:
        with pytest.raises(ValueError):
            repo.review_text_fact(fact, PROFILE, action='approve', linked_bom_fact_id='symbol-19',
                                 note='Checked', db_engine=text_store, **extra)


def test_bom_annotation_not_an_assembly_instruction(text_store):
    save_steps(text_store, (1, 2))
    prepare(text_store)
    approve(text_store)
    result = attest(text_store)
    assert 'notes' not in result['manifest']
    assert all('notes' not in step for step in result['manifest']['steps'])


@pytest.mark.parametrize('field,value', [('SourceDocID', 2), ('SourceVersion', 3),
    ('SnapshotID', 'other'), ('SourceHash', 'b' * 64)])
def test_cross_source_target_is_redacted_after_review(text_store, field, value):
    from sqlalchemy import text
    prepare(text_store)
    approve(text_store)
    with text_store.begin() as conn:
        conn.execute(text(f'UPDATE StructuredTextFact SET {field}=:value WHERE FactID=:id'),
                     {'value':value, 'id':'symbol-19'})
    item = note(text_store)
    assert item['linked_bom_status'] == 'unknown' and item['linked_bom_fact_id'] is None
    with pytest.raises(PermissionError):
        approve(text_store)


@pytest.mark.parametrize('change', [{'kind':'material'}, {'bom_position':''},
    {'locator':replace(material().locator, row_key='')}, {'review_status':'pending'},
    {'conflicts':('unresolved',)}])
def test_unusable_bom_symbol_cannot_be_linked(text_store, change):
    from sqlalchemy import text
    prepare(text_store)
    with text_store.begin() as conn:
        payload = conn.execute(text("SELECT ReviewedPayloadJson FROM StructuredTextFact WHERE FactID='symbol-19'")).scalar_one()
        target = replace(repo._fact(payload), **change)
        conn.execute(text("UPDATE StructuredTextFact SET ReviewedPayloadJson=:payload WHERE FactID='symbol-19'"),
                     {'payload':repo._json(target)})
    with pytest.raises(ValueError):
        approve(text_store)


def test_options_page_beyond_first_fifty_and_denied_reader(text_store):
    prepare(text_store)
    extra = tuple(replace(material(), fact_id=f'symbol-extra-{i:03}', kind='symbol',
        raw_value=f'P{i}', value=f'P{i}', bom_position=str(i), conflicts=(), candidates=()) for i in range(51))
    repo.save_extracted_text_facts(extra, snapshot_id='s1', db_engine=text_store)
    for fact in extra:
        repo.review_text_fact(fact.fact_id, PROFILE, action='approve', note='Fixture', db_engine=text_store)
    first = repo.list_bom_reference_options(1, PROFILE, db_engine=text_store)
    second = repo.list_bom_reference_options(1, PROFILE, offset=50, db_engine=text_store)
    assert len(first['items']) == 50 and first['has_more']
    assert len(second['items']) == 2 and not second['has_more']
    assert not {x['fact_id'] for x in first['items']} & {x['fact_id'] for x in second['items']}
    denied = {**PROFILE, 'allowed_sites':['other']}
    with pytest.raises(PermissionError):
        repo.list_bom_reference_options(1, denied, db_engine=text_store)
    with pytest.raises(PermissionError):
        repo.review_text_fact('note-1', denied, action='approve', linked_bom_fact_id='symbol-19',
                              note='Fixture', db_engine=text_store)


def test_legacy_sequence_attestation_omits_new_fields(text_store):
    from hashlib import sha256
    import json
    save_steps(text_store, (1, 2))
    result = attest(text_store)
    steps = result['manifest']['steps']
    assert all('linked_bom_fact_id' not in step and 'linked_bom_fingerprint' not in step for step in steps)
    legacy_hash = sha256(json.dumps(steps, sort_keys=True, ensure_ascii=False).encode('utf8')).hexdigest()
    assert result['manifest']['step_fingerprint'] == legacy_hash
    assert repo.load_assembly_sequence(1, PROFILE, db_engine=text_store)['status'] == 'complete'


@pytest.mark.parametrize('change', [
    {'linked_bom_fact_id':''}, {'linked_bom_fact_id':'x'*451}, {'linked_bom_fact_id':7},
    {'linked_bom_fact_id':'symbol', 'kind':'material'},
    {'linked_bom_fact_id':'symbol', 'linked_step_fact_id':'step'},
    {'linked_bom_fingerprint':'a'*64},
    {'linked_bom_fact_id':'symbol', 'linked_bom_fingerprint':7},
    {'linked_bom_fact_id':'symbol', 'linked_bom_fingerprint':'short'},
    {'linked_bom_fact_id':'symbol', 'linked_bom_fingerprint':'g'*64},
])
def test_invalid_link_identity_or_fingerprint_is_rejected(change):
    with pytest.raises(ValueError):
        replace(drawing_note(), **change)
