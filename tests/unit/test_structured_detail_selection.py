from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.unit.test_structured_evidence_repository import PROFILE, scope, store
from mech_chatbot.db.repositories.structured_evidence import (
    save_extracted_scope, review_numeric_fact, review_scope_completeness,
    load_approved_scope, list_scope_facts, list_document_scopes,
)
from mech_chatbot.domain.structured_evidence import RoundingRule, scope_payload, reviewed_scope_fingerprint


def prepare(store, separate_subtotal_source=False):
    original = scope()
    facts = tuple(replace(original.facts[0], fact_id=f'fact-{i}', value=Decimal(value),
                          raw_value=value, locator=replace(original.facts[0].locator, row_key=f'row-{i}'))
                  for i, value in enumerate(('100', '200', '300')))
    if separate_subtotal_source:
        with store.begin() as conn:
            conn.execute(text("INSERT INTO TaiLieu SELECT 2,VersionNo,TenFile,FilePath,ThuMuc,Site,SecurityLevel,LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,EffectiveDate,ExpiryDate,KnowledgeApproverUserID FROM TaiLieu WHERE DocID=1"))
            conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(2,'ENG')"))
        facts = (*facts[:2], replace(facts[2], locator=replace(facts[2].locator, doc_id='2')))
        original = replace(original, source_revisions=(('1', '2'), ('2', '2')))
    save_extracted_scope(replace(original, facts=facts), db_engine=store)
    for fact in facts[:2]:
        review_numeric_fact(fact.fact_id, PROFILE, action='approve', note='Checked detail', db_engine=store)


def review(store, selected=('row-0', 'row-1'), excluded=None):
    return review_scope_completeness('scope-s1', PROFILE, expected_row_ids=selected,
        excluded_row_reasons={'row-2': 'Subtotal, already represented by details'} if excluded is None else excluded,
        rounding_rule=RoundingRule(2), definition='Detail amounts', note='Checked all rows', db_engine=store)


def test_reviewed_selection_preserves_source_and_invalidates_on_excluded_review(store):
    prepare(store)
    review(store)
    approved = load_approved_scope('scope-s1', PROFILE, db_engine=store)
    assert sum(f.value for f in approved.facts) == Decimal('300')
    assert approved.expected_row_count == 2
    assert len(list_scope_facts('scope-s1', PROFILE, db_engine=store)['items']) == 3
    assert list_document_scopes(1, PROFILE, db_engine=store)[0]['excluded_row_reasons']['row-2']
    first = reviewed_scope_fingerprint(approved)
    review(store, excluded={'row-2': 'Reviewed subtotal'})
    assert reviewed_scope_fingerprint(load_approved_scope('scope-s1', PROFILE, db_engine=store)) != first
    review_numeric_fact('fact-2', PROFILE, action='reject', note='Not a detail', db_engine=store)
    assert load_approved_scope('scope-s1', PROFILE, db_engine=store) is None


@pytest.mark.parametrize('selected,excluded', [
    (('row-0',), {'row-2': 'subtotal'}), (('row-0', 'row-1'), {'missing': 'subtotal'}),
    (('row-0', 'row-0', 'row-1'), {'row-2': 'subtotal'}),
    (('row-0', 'row-1'), {'row-1': 'overlap', 'row-2': 'subtotal'}),
    ((), {'row-0': 'omit', 'row-1': 'omit', 'row-2': 'omit'}),
    (('row-0', 'row-1'), {'row-2': ' '}),
])
def test_selection_must_account_for_every_source_row(store, selected, excluded):
    prepare(store)
    review(store)
    before = load_approved_scope('scope-s1', PROFILE, db_engine=store)
    with pytest.raises(ValueError):
        review(store, selected, excluded)
    assert load_approved_scope('scope-s1', PROFILE, db_engine=store) == before


def test_selected_pending_row_is_rejected_and_legacy_payload_unchanged(store):
    prepare(store)
    with pytest.raises(ValueError):
        review(store, ('row-0', 'row-2'), {'row-1': 'not in scope'})
    assert 'excluded_row_reasons' not in scope_payload(scope())


def test_excluded_source_pin_is_still_checked(store):
    prepare(store)
    review(store)
    with store.begin() as conn:
        conn.execute(text("UPDATE StructuredNumericFact SET SourceHash=:hash WHERE FactID='fact-2'"), {'hash': 'b'*64})
    assert load_approved_scope('scope-s1', PROFILE, db_engine=store) is None


@pytest.mark.parametrize('sql', [
    "UPDATE TaiLieu SET SecurityLevel='confidential' WHERE DocID=2",
    "UPDATE TaiLieu SET VersionNo=3 WHERE DocID=2",
    "UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=2",
])
def test_excluded_document_remains_part_of_source_authorization(store, sql):
    prepare(store, separate_subtotal_source=True)
    review(store)
    assert load_approved_scope('scope-s1', PROFILE, db_engine=store) is not None
    with store.begin() as conn:
        conn.execute(text(sql))
    assert load_approved_scope('scope-s1', PROFILE, db_engine=store) is None


@pytest.mark.parametrize('excluded', [[], {1: 'reason'}, {'row-2': False}, {'row-2': 'a' * 2001}])
def test_malformed_exclusion_reasons_are_rejected(store, excluded):
    prepare(store)
    with pytest.raises(ValueError):
        review(store, excluded=excluded)

