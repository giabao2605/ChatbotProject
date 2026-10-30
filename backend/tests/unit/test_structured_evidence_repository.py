from dataclasses import asdict, replace
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, list_document_scopes, list_scope_facts, review_numeric_fact, review_scope_completeness, load_approved_scope
from mech_chatbot.domain.structured_evidence import NumericFact, SourceLocator, ScopeManifest, RoundingRule


@pytest.fixture
def store():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        for sql in (
            'CREATE TABLE TaiLieu(DocID INTEGER PRIMARY KEY,VersionNo INTEGER,TenFile TEXT,FilePath TEXT,ThuMuc TEXT,Site TEXT,SecurityLevel TEXT,LifecycleStatus TEXT,ReviewStatus TEXT,PublicationState TEXT,Servable INTEGER,IsCurrent INTEGER,EffectiveStatus TEXT,EffectiveDate TEXT,ExpiryDate TEXT,KnowledgeApproverUserID INTEGER)',
            'CREATE TABLE PhongBanChiaSe(DocID INTEGER,DeptCode TEXT)',
            'CREATE TABLE Users(UserID INTEGER,Username TEXT,IsActive INTEGER)',
            'CREATE TABLE Roles(RoleID INTEGER,RoleName TEXT)',
            'CREATE TABLE UserRoles(UserID INTEGER,RoleID INTEGER)',
            'CREATE TABLE AuditLog(UserID INTEGER,Username TEXT,Action TEXT,EntityType TEXT,EntityID TEXT,Details TEXT)',
            'CREATE TABLE StructuredSourceSnapshot(DocID INTEGER,VersionNo INTEGER,SnapshotID TEXT,SourceHash TEXT,CurrentSnapshot INTEGER,PRIMARY KEY(DocID,VersionNo,SnapshotID))',
            'CREATE TABLE StructuredEvidenceScope(ScopeID TEXT PRIMARY KEY,SnapshotID TEXT,ManifestJson TEXT,CompletenessReviewed INTEGER,ReviewedBy INTEGER,ReviewNote TEXT,UpdatedAt TEXT)',
            'CREATE TABLE StructuredNumericFact(FactID TEXT PRIMARY KEY,ScopeID TEXT,SourceDocID INTEGER,SourceVersion INTEGER,SourceHash TEXT,PayloadJson TEXT,ReviewedPayloadJson TEXT,ReviewStatus TEXT,ReviewedBy INTEGER,ReviewNote TEXT,UpdatedAt TEXT)',
            "INSERT INTO TaiLieu VALUES(1,2,'export.csv',NULL,'ENG','factory','internal','published','approved','published',1,1,'effective',NULL,NULL,7)",
            "INSERT INTO PhongBanChiaSe VALUES(1,'ENG')",
            "INSERT INTO Users VALUES(7,'approver',1)",
            "INSERT INTO Roles VALUES(1,'reviewer')",
            'INSERT INTO UserRoles VALUES(7,1)',
        ):
            conn.execute(text(sql))
    return engine


PROFILE = {'user_id': 7, 'username': 'approver', 'roles': ['reviewer'], 'allowed_departments': ['ENG'], 'allowed_sites': ['factory'], 'max_security_level': 'internal'}


def empty_scope():
    return ScopeManifest('empty', 'empty-snapshot', (), source_revisions=(('1','2'),),
                         empty_source_locator=SourceLocator('1','2',sheet='CSV',cell='B1',table_index=1),
                         empty_source_hash='a'*64, empty_source_extraction_method='csv')


@pytest.mark.parametrize('fault', ['header', 'populated', 'revision'])
def test_persisted_scope_tampering_never_becomes_available_evidence(store, fault):
    import json
    candidate = empty_scope() if fault == 'header' else scope()
    save_extracted_scope(candidate, db_engine=store)
    with store.begin() as conn:
        payload = json.loads(conn.execute(text('SELECT ManifestJson FROM StructuredEvidenceScope')).scalar())
        if fault == 'header':
            payload['empty_source_locator']['cell'] = None
        elif fault == 'populated':
            payload.update({key: value for key, value in asdict(empty_scope()).items()
                            if key.startswith('empty_source_')})
        else:
            payload['source_revisions'] = [['1', '3']]
        conn.execute(text('UPDATE StructuredEvidenceScope SET ManifestJson=:payload'), {'payload': json.dumps(payload)})
    with pytest.raises(PermissionError, match='Evidence source unavailable'):
        list_scope_facts(candidate.scope_id, PROFILE, db_engine=store)
    assert load_approved_scope(candidate.scope_id, PROFILE, db_engine=store) is None


@pytest.mark.parametrize('fault', ['duplicate_fact', 'revision', 'hash', 'conflicting_hashes', 'duplicate_scope'])
def test_extraction_registration_rejects_ambiguous_or_reused_identity(store, fault):
    candidate = scope()
    fact = candidate.facts[0]
    if fault == 'duplicate_fact':
        candidate = replace(candidate, facts=(fact, fact))
    elif fault == 'revision':
        candidate = replace(candidate, source_revisions=(('1', '3'),))
    elif fault == 'hash':
        candidate = replace(candidate, facts=(replace(fact, source_hash='invalid'),))
    elif fault == 'conflicting_hashes':
        candidate = replace(candidate, facts=(fact, replace(fact, fact_id='second', source_hash='b'*64)))
    else:
        save_extracted_scope(candidate, db_engine=store)
    with pytest.raises(ValueError):
        save_extracted_scope(candidate, db_engine=store)
    with store.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM StructuredEvidenceScope')).scalar() == (fault == 'duplicate_scope')


def test_approved_empty_discovery_pages_only_reviewed_scopes(store):
    from mech_chatbot.db.repositories.structured_evidence import list_approved_document_scopes
    for index in range(4):
        candidate = replace(empty_scope(), scope_id=f'empty-{index}')
        save_extracted_scope(candidate, db_engine=store)
        review_scope_completeness(candidate.scope_id, PROFILE, expected_row_ids=(), rounding_rule=RoundingRule(2),
            unit='VND', currency='VND', definition=f'Checked export {index}', note='Synthetic review', db_engine=store)
    page = list_approved_document_scopes(1, PROFILE, offset=1, limit=1, db_engine=store)
    assert [item['scope_id'] for item in page['items']] == ['empty-1'] and page['has_more']
    assert list_approved_document_scopes(1, PROFILE, offset=4, db_engine=store)['items'] == []
    with pytest.raises(ValueError, match='Invalid scope selection request'):
        list_approved_document_scopes(True, PROFILE, db_engine=store)


@pytest.mark.parametrize('change', [
    {'empty_source_hash':False}, {'empty_source_hash':'not-a-hash'}, {'empty_source_extraction_method':''},
    {'source_revisions':(('2','2'),)}, {'empty_source_locator':SourceLocator('1','2',row_key='missing')},
    {'empty_source_locator':SourceLocator(True,'2',sheet='CSV',cell='B1',table_index=1)},
    {'empty_source_locator':SourceLocator('1','2',sheet='CSV',cell='B1',table_index=True)},
    {'facts':'populated'},
])
def test_malformed_empty_declarations_never_reach_persistence(store, change):
    if change.get('facts') == 'populated':change={'facts':scope().facts}
    with pytest.raises(ValueError):
        save_extracted_scope(replace(empty_scope(), **change), db_engine=store)
    with store.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM StructuredEvidenceScope')).scalar() == 0


@pytest.mark.parametrize('changes', [{'unit':None,'currency':''}, {'unit':'VND','currency':None},
                                     {'unit':'','currency':''}, {'unit':True,'currency':''}])
def test_empty_review_requires_explicit_unit_and_currency(store, changes):
    save_extracted_scope(empty_scope(), db_engine=store)
    with pytest.raises(ValueError):
        review_scope_completeness('empty', PROFILE, expected_row_ids=(), rounding_rule=RoundingRule(2),
                                  definition='Checked empty export', note='Synthetic review', db_engine=store, **changes)
    assert load_approved_scope('empty', PROFILE, db_engine=store) is None


@pytest.mark.parametrize('fault',['rights','reviewer','revision','hash','count','policy'])
def test_empty_approved_scope_rechecks_permissions_revision_hash_and_exact_zero(store, fault):
    import json
    save_extracted_scope(empty_scope(), db_engine=store)
    review_scope_completeness('empty', PROFILE, expected_row_ids=(), rounding_rule=RoundingRule(2),
                              unit='VND',currency='VND',definition='Checked empty export',note='Synthetic review',db_engine=store)
    profile = {**PROFILE, **({'allowed_sites':['other']} if fault=='rights' else {'user_id':9} if fault=='reviewer' else {})}
    with store.begin() as conn:
        if fault=='revision':conn.execute(text('UPDATE TaiLieu SET VersionNo=3'))
        if fault=='hash':conn.execute(text("UPDATE StructuredSourceSnapshot SET SourceHash=:hash"), {'hash':'b'*64})
        if fault in {'count','policy'}:
            payload=json.loads(conn.execute(text("SELECT ManifestJson FROM StructuredEvidenceScope WHERE ScopeID='empty'")).scalar())
            if fault=='count':payload['expected_row_count']=False
            else:payload['rounding_rule']['scale']=True
            conn.execute(text("UPDATE StructuredEvidenceScope SET ManifestJson=:payload"),{'payload':json.dumps(payload)})
    if fault != 'reviewer':assert load_approved_scope('empty', profile, db_engine=store) is None
    with pytest.raises((PermissionError,ValueError)):
        review_scope_completeness('empty', profile, expected_row_ids=() if fault!='count' else ('invented',),
            rounding_rule=RoundingRule(True if fault=='policy' else 2),unit='VND',currency='VND',
            definition='Checked empty export',note='Must recheck',db_engine=store)


def test_absent_empty_marker_cannot_turn_missing_rows_into_reviewed_zero(store):
    with pytest.raises(ValueError):save_extracted_scope(replace(scope(),facts=()),db_engine=store)
    save_extracted_scope(scope(),db_engine=store)
    review_numeric_fact('fact-s1',PROFILE,action='approve',note='Synthetic review',db_engine=store)
    with pytest.raises(ValueError):
        review_scope_completeness('scope-s1',PROFILE,expected_row_ids=(),rounding_rule=RoundingRule(2),
            unit='VND',currency='VND',definition='Not an empty export',note='Synthetic',db_engine=store)


def test_nonempty_scope_review_explicit_units_must_match_and_old_payload_is_unchanged(store):
    import json
    save_extracted_scope(scope(),db_engine=store)
    review_numeric_fact('fact-s1',PROFILE,action='approve',note='Synthetic review',db_engine=store)
    with pytest.raises(ValueError):
        review_scope_completeness('scope-s1',PROFILE,expected_row_ids=('row1',),rounding_rule=RoundingRule(2),
            unit='kg',currency='',definition='net value',note='Synthetic',db_engine=store)
    review_scope_completeness('scope-s1',PROFILE,expected_row_ids=('row1',),rounding_rule=RoundingRule(2),
        unit='VND',currency='VND',definition='net value',note='Synthetic',db_engine=store)
    with store.connect() as conn:
        payload=json.loads(conn.execute(text("SELECT ManifestJson FROM StructuredEvidenceScope")).scalar())
    assert not any(key.startswith('empty_source') for key in payload)


def test_header_only_export_can_be_reviewed_as_a_pinned_empty_scope(store, tmp_path):
    from mech_chatbot.ingestion.pdf.structured import extract_structured_table_file
    from mech_chatbot.db.repositories.structured_evidence import list_approved_document_scopes

    path = tmp_path / 'empty-export.csv'
    path.write_text('code,amount [VND]\n', encoding='utf-8')
    extracted = extract_structured_table_file(path, doc_id='1', version='2', snapshot_id='empty-export')
    candidate = extracted.tables[0].scopes[0]
    save_extracted_scope(candidate, db_engine=store)
    assert load_approved_scope(candidate.scope_id, PROFILE, db_engine=store) is None
    assert [item['scope_id'] for item in list_document_scopes(1, PROFILE, db_engine=store)] == [candidate.scope_id]
    assert list_scope_facts(candidate.scope_id, PROFILE, db_engine=store)['items'] == []
    review_scope_completeness(candidate.scope_id, PROFILE, expected_row_ids=(), rounding_rule=RoundingRule(2),
                              unit='VND', currency='VND', definition='Net value in the checked export',
                              note='Synthetic reviewer explicitly checked the empty export', db_engine=store)
    approved = load_approved_scope(candidate.scope_id, PROFILE, db_engine=store)
    assert approved.authorized and approved.completeness_reviewed
    assert approved.expected_row_count == 0 and approved.expected_row_ids == () and approved.facts == ()
    assert approved.unit == 'VND' and approved.currency == 'VND'
    assert [item['scope_id'] for item in list_approved_document_scopes(1, PROFILE, db_engine=store)['items']] == [candidate.scope_id]
    with store.begin() as conn:
        conn.execute(text("UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=1"))
    assert load_approved_scope(candidate.scope_id, PROFILE, db_engine=store) is None
    assert list_approved_document_scopes(1, PROFILE, db_engine=store)['items'] == []


def test_review_and_read_check_shared_source_once_per_call_and_revalidate_later(store):
    from sqlalchemy import event

    candidate = scope()
    facts = tuple(replace(candidate.facts[0], fact_id=f'fact-{i}',
                          locator=replace(candidate.facts[0].locator, row_key=f'row-{i}'))
                  for i in range(4))
    save_extracted_scope(replace(candidate, facts=facts), db_engine=store)
    reads = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if 'SELECT SourceHash FROM StructuredSourceSnapshot' in statement:
            reads.append(statement)
    event.listen(store, 'before_cursor_execute', capture)
    try:
        for fact in facts:
            reads.clear()
            review_numeric_fact(fact.fact_id, PROFILE, action='approve', note='Synthetic row review', db_engine=store)
            assert len(reads) == 1
        reads.clear()
        review_scope_completeness(candidate.scope_id, PROFILE, expected_row_ids=tuple(f.locator.row_key for f in facts),
                                  rounding_rule=RoundingRule(2), definition='Synthetic complete scope',
                                  note='Synthetic scope review', db_engine=store)
        assert len(reads) == 1
        reads.clear()
        approved = load_approved_scope(candidate.scope_id, PROFILE, db_engine=store)
        assert len(approved.facts) == 4 and len(reads) == 1
        with store.begin() as conn:
            conn.execute(text("UPDATE StructuredSourceSnapshot SET SourceHash=:hash"), {'hash': 'b' * 64})
        assert load_approved_scope(candidate.scope_id, PROFILE, db_engine=store) is None
        with pytest.raises(PermissionError):
            review_numeric_fact(facts[0].fact_id, PROFILE, action='approve', note='Must revalidate', db_engine=store)
    finally:
        event.remove(store, 'before_cursor_execute', capture)


@pytest.mark.parametrize('bad_id', ['fact-s1', 'other'])
def test_shared_source_deduplication_cannot_hide_one_row_with_a_conflicting_hash(store, bad_id):
    candidate = scope()
    facts = (candidate.facts[0], replace(candidate.facts[0], fact_id='other',
              locator=replace(candidate.facts[0].locator, row_key='row2')))
    save_extracted_scope(replace(candidate, facts=facts), db_engine=store)
    with store.begin() as conn:
        conn.execute(text("UPDATE StructuredNumericFact SET SourceHash=:hash WHERE FactID=:id"), {'hash': 'b' * 64, 'id': bad_id})
    with pytest.raises(PermissionError):
        review_numeric_fact(facts[0].fact_id, PROFILE, action='approve', note='Must check all hashes', db_engine=store)
    assert load_approved_scope(candidate.scope_id, PROFILE, db_engine=store) is None
    with store.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM AuditLog')).scalar() == 0


def test_review_checks_each_distinct_source_pin_and_propagates_snapshot_read_failure(store):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    candidate = scope()
    with store.begin() as conn:
        conn.execute(text('INSERT INTO TaiLieu SELECT 2,VersionNo,TenFile,FilePath,ThuMuc,Site,SecurityLevel,LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,EffectiveDate,ExpiryDate,KnowledgeApproverUserID FROM TaiLieu WHERE DocID=1'))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(2,'ENG')"))
    other = replace(candidate.facts[0], fact_id='other', source_hash='b' * 64,
                    locator=replace(candidate.facts[0].locator, doc_id='2', row_key='row2'))
    save_extracted_scope(replace(candidate, facts=(*candidate.facts, other), source_revisions=(('1', '2'), ('2', '2'))), db_engine=store)
    reads = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        if 'SELECT SourceHash FROM StructuredSourceSnapshot' in statement:
            reads.append(parameters)
    event.listen(store, 'before_cursor_execute', capture)
    try:
        review_numeric_fact(candidate.facts[0].fact_id, PROFILE, action='approve', note='Synthetic review', db_engine=store)
        assert len(reads) == 2
    finally:
        event.remove(store, 'before_cursor_execute', capture)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if 'SELECT SourceHash FROM StructuredSourceSnapshot' in statement:
            raise OperationalError('Snapshot check failed', {}, None)
    event.listen(store, 'before_cursor_execute', fail)
    try:
        with pytest.raises(OperationalError):
            review_numeric_fact(other.fact_id, PROFILE, action='approve', note='Failed check', db_engine=store)
    finally:
        event.remove(store, 'before_cursor_execute', fail)
    with store.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM AuditLog')).scalar() == 1


def scope(snapshot='s1', value=Decimal('3.14')):
    fact = NumericFact('fact-'+snapshot, 'Amount', str(value), value, 'VND', SourceLocator('1','2',row_key='row1',sheet='CSV',cell='B2'), source_hash='a'*64, currency='VND', extraction_method='csv')
    return ScopeManifest('scope-'+snapshot,snapshot,(fact,),source_revisions=(('1','2'),),unit='VND',currency='VND')


def test_approval_and_completeness_are_real_separate_reviews(store):
    candidate = scope()
    save_extracted_scope(replace(candidate, completeness_reviewed=True,authorized=True), db_engine=store)
    assert load_approved_scope(candidate.scope_id, PROFILE, db_engine=store) is None
    review_numeric_fact(candidate.facts[0].fact_id,PROFILE, action='approve',note='Checked cell',db_engine=store)
    assert load_approved_scope(candidate.scope_id,PROFILE,db_engine=store) is None
    review_scope_completeness(candidate.scope_id,PROFILE,expected_row_ids=('row1',),rounding_rule=RoundingRule(2),definition='net value',note='Checked whole export',db_engine=store)
    approved = load_approved_scope(candidate.scope_id,PROFILE,db_engine=store)
    assert approved.authorized and approved.completeness_reviewed
    assert approved.facts[0].value == Decimal('3.14')
    assert approved.rounding_rule.reviewed
    with store.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM AuditLog')).scalar() == 2


def test_denied_actor_gets_no_metadata_and_forged_reviewer_cannot_review(store):
    save_extracted_scope(scope(), db_engine=store)
    with pytest.raises(PermissionError):
        list_scope_facts('scope-s1',{**PROFILE,'allowed_sites':['elsewhere']},db_engine=store)
    with pytest.raises(PermissionError):
        review_numeric_fact('fact-s1',{**PROFILE,'user_id':9},action='approve',note='fake',db_engine=store)


def test_reingestion_and_revision_changes_invalidate_prior_scope(store):
    first = scope()
    save_extracted_scope(first,db_engine=store)
    review_numeric_fact('fact-s1',PROFILE,action='approve',note='checked',db_engine=store)
    review_scope_completeness('scope-s1',PROFILE,expected_row_ids=('row1',),rounding_rule=RoundingRule(2),definition='net value',note='complete',db_engine=store)
    save_extracted_scope(scope('s2'),db_engine=store)
    assert load_approved_scope('scope-s1',PROFILE,db_engine=store) is None
    with store.begin() as conn:
        conn.execute(text('UPDATE TaiLieu SET VersionNo=3 WHERE DocID=1'))
    assert load_approved_scope('scope-s2',PROFILE,db_engine=store) is None


def test_missing_rows_and_numeric_conflicts_cannot_be_approved_as_complete(store):
    save_extracted_scope(scope(),db_engine=store)
    with pytest.raises(ValueError):
        review_scope_completeness('scope-s1',PROFILE,expected_row_ids=('row1','row2'),rounding_rule=RoundingRule(2),definition='net value',note='complete',db_engine=store)


def test_review_correction_preserves_raw_value_and_invalidates_completeness(store):
    save_extracted_scope(scope(),db_engine=store)
    payload = review_numeric_fact('fact-s1',PROFILE,action='approve',value_text='5.12',note='Rechecked',db_engine=store)
    assert payload['value'] == '5.12' and payload['raw_value'] == '3.14'
    assert list_scope_facts('scope-s1',PROFILE,limit=1,db_engine=store)['items'][0] == payload
    assert list_document_scopes(1,PROFILE,db_engine=store)[0]['scope_id'] == 'scope-s1'
    assert list_document_scopes(1,{**PROFILE,'allowed_departments':['HR']},db_engine=store) == []


def test_stale_registered_snapshot_cannot_be_resurrected(store):
    save_extracted_scope(scope(),db_engine=store)
    save_extracted_scope(scope('s2'),db_engine=store)
    stale = replace(scope(),scope_id='older-new-scope',facts=(replace(scope().facts[0],fact_id='older-fact'),))
    with pytest.raises(ValueError,match='stale'):
        save_extracted_scope(stale,db_engine=store)


def test_assigned_reviewer_can_review_draft_but_never_archived(store):
    with store.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET LifecycleStatus='draft',ReviewStatus='pending_review',PublicationState='draft',Servable=0,IsCurrent=0,EffectiveStatus='draft'"))
    save_extracted_scope(scope(),db_engine=store)
    assert list_scope_facts('scope-s1',PROFILE,db_engine=store)['items']
    assert load_approved_scope('scope-s1',PROFILE,db_engine=store) is None
    review_numeric_fact('fact-s1',PROFILE,action='approve',note='checked draft',db_engine=store)
    with store.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET LifecycleStatus='archived'"))
    with pytest.raises(PermissionError):
        list_scope_facts('scope-s1',PROFILE,db_engine=store)


def test_reviewed_repository_scope_can_be_used_in_math_with_source_row_ids(store):
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes
    save_extracted_scope(scope(), db_engine=store)
    review_numeric_fact('fact-s1', PROFILE, action='approve', note='checked', db_engine=store)
    review_scope_completeness('scope-s1', PROFILE, expected_row_ids=('row1',),
                             rounding_rule=RoundingRule(2), definition='net value', note='complete', db_engine=store)
    approved = load_approved_scope('scope-s1', PROFILE, db_engine=store)
    # A second independent pinned export can retain its own revision/snapshot.
    other_fact = replace(approved.facts[0], fact_id='other-fact', value=Decimal('6.28'),
                         locator=replace(approved.facts[0].locator, doc_id='2', version='7'))
    other = replace(approved, scope_id='other', snapshot_id='other-snapshot', facts=(other_fact,),
                    source_revisions=(('2','7'),))
    result = compare_two_scopes(approved, other)
    assert result.status == 'complete'
    assert result.steps[2].value == Decimal('3.14')
