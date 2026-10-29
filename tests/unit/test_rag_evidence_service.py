"""Offline service integration through real review storage and Math kernels."""
from dataclasses import replace
from decimal import Decimal
import pytest

from sqlalchemy import text

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope
from mech_chatbot.services import rag_evidence_service as service
from .test_structured_evidence_repository import PROFILE, scope, store
from .test_graph_v2_repository import connection
from .test_rag_bom_chat import bom_store


def other_source(store, *, unit='VND', currency='VND'):
    with store.begin() as conn:
        conn.execute(text("INSERT INTO TaiLieu SELECT 4,7,TenFile,FilePath,ThuMuc,Site,SecurityLevel,"
                          "LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,"
                          "EffectiveDate,ExpiryDate,KnowledgeApproverUserID FROM TaiLieu WHERE DocID=1"))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES (4,'ENG')"))
    original = scope('other', Decimal('6.28'))
    fact = replace(original.facts[0], locator=replace(original.facts[0].locator, doc_id='4', version='7'),
                   unit=unit, currency=currency)
    return replace(original, facts=(fact,), source_revisions=(('4', '7'),), unit=unit, currency=currency)


def approve(candidate, *, scale=2):
    service.review_fact(candidate.facts[0].fact_id, PROFILE, action='approve', note='Checked source cell')
    service.review_scope(candidate.scope_id, PROFILE, expected_row_ids=('row1',),
                         rounding_scale=scale, rounding_mode='ROUND_HALF_UP',
                         definition='reviewed source quantity', note='Checked complete source')


def test_reviewed_detail_selection_reaches_math_and_paginated_sources(store):
    original = scope()
    first = original.facts[0]
    facts = tuple(replace(first, fact_id=f'detail-{i}', value=Decimal(value), raw_value=value,
                          locator=replace(first.locator, row_key=f'row{i}'))
                  for i, value in enumerate(('100', '200', '300'), start=1))
    candidate = replace(original, facts=facts)
    comparison = other_source(store)
    save_extracted_scope(candidate, db_engine=store)
    save_extracted_scope(comparison, db_engine=store)
    with bind_repository_runtime(db_engine=store):
        approve(comparison)
        for fact in facts:
            service.review_fact(fact.fact_id, PROFILE, action='approve', note='Checked synthetic cell')
        service.review_scope(candidate.scope_id, PROFILE, expected_row_ids=('row1','row2'),
            excluded_row_reasons={'row3':'Subtotal already represented by detail rows'},
            rounding_scale=2, rounding_mode='ROUND_HALF_UP', definition='reviewed source quantity', note='All rows reconciled')
        result = service.compare_scopes(candidate.scope_id, comparison.scope_id, PROFILE)
        assert result['status'] == 'complete'
        assert Decimal(result['steps'][0]['value']) == Decimal('300')
        page = service.list_approved_scope_facts(candidate.scope_id, PROFILE, limit=1)
        assert page['has_more'] and page['items'][0]['locator']['row_key'] == 'row1'
        last = service.list_approved_scope_facts(candidate.scope_id, PROFILE, offset=1, limit=1)
        assert not last['has_more'] and last['items'][0]['locator']['row_key'] == 'row2'
        assert len(service.list_scope_facts(candidate.scope_id, PROFILE)['items']) == 3


def test_review_to_comparison_preserves_independent_revisions_and_denies_other_site(store):
    a, b = scope(), other_source(store)
    save_extracted_scope(a, db_engine=store)
    save_extracted_scope(b, db_engine=store)
    with bind_repository_runtime(db_engine=store):
        assert service.compare_scopes(a.scope_id, b.scope_id, PROFILE)['status'] == 'blocked'
        approve(a)
        approve(b)
        assert service.list_document_scopes(1, PROFILE)[0]['completeness_reviewed']
        assert service.list_scope_facts(a.scope_id, PROFILE, limit=1)['items'][0]['raw_value'] == '3.14'
        result = service.compare_scopes(a.scope_id, b.scope_id, PROFILE)
        assert result['status'] == 'complete' and result['domain_step_count'] == 4
        assert result['steps'][2]['value'] == '3.14'
        assert result['steps'][3]['display_value'] == '100.00'
        assert result['sources'][1]['source_revisions'] == [['4', '7']]
        denied = service.compare_scopes(a.scope_id, b.scope_id, {**PROFILE, 'allowed_sites':['other']})
        assert denied['status'] == 'blocked' and denied['sources'] == []


def test_approved_fact_pages_recheck_source_access_and_never_require_reviewer_write_rights(store):
    import pytest
    candidate = scope()
    save_extracted_scope(candidate, db_engine=store)
    reader = {**PROFILE, 'user_id':8, 'username':'reader', 'roles':['viewer']}
    with bind_repository_runtime(db_engine=store):
        with pytest.raises(PermissionError):
            service.list_approved_scope_facts(candidate.scope_id, reader)
        approve(candidate)
        page = service.list_approved_scope_facts(candidate.scope_id, reader, offset=0, limit=1)
        assert page['items'][0]['value'] == '3.14' and page['items'][0]['locator']['cell'] == 'B2'
        assert page['snapshot_id'] == 's1' and page['has_more'] is False
        assert service.list_approved_scope_facts(candidate.scope_id, reader, offset=1, limit=1)['items'] == []
        with store.begin() as conn:
            conn.execute(text('UPDATE TaiLieu SET VersionNo=3'))
        with pytest.raises(PermissionError):
            service.list_approved_scope_facts(candidate.scope_id, reader)


def test_scope_picker_returns_only_complete_current_readable_scopes(store):
    candidate = scope()
    save_extracted_scope(candidate, db_engine=store)
    reader = {**PROFILE, 'user_id':8, 'username':'reader', 'roles':['viewer']}
    with bind_repository_runtime(db_engine=store):
        assert service.list_approved_document_scopes(1, reader)['items'] == []
        approve(candidate)
        page = service.list_approved_document_scopes(1, reader, limit=1)
        assert page['items'] == [{'scope_id':'scope-s1', 'definition':'reviewed source quantity',
                                 'period':'', 'unit':'VND', 'currency':'VND'}]
        assert page['has_more'] is False
        assert service.list_approved_document_scopes(1, reader, offset=1)['items'] == []
        assert service.list_approved_document_scopes(1, {**reader, 'allowed_sites':['other']})['items'] == []
        with store.begin() as conn:
            conn.execute(text('UPDATE TaiLieu SET VersionNo=3'))
        assert service.list_approved_document_scopes(1, reader)['items'] == []


def test_scope_picker_pages_over_visible_approved_scopes_without_hidden_count(store):
    candidate = scope()
    candidates = [replace(candidate, scope_id=f'picker-{n}',
                          facts=(replace(candidate.facts[0], fact_id=f'picker-fact-{n}'),)) for n in range(4)]
    for item in candidates:
        save_extracted_scope(item, db_engine=store)
    reader = {**PROFILE, 'user_id':8, 'username':'reader', 'roles':['viewer']}
    with bind_repository_runtime(db_engine=store):
        for item in candidates[1:]:
            approve(item)
        first = service.list_approved_document_scopes(1, reader, limit=1)
        assert [item['scope_id'] for item in first['items']] == ['picker-1'] and first['has_more']
        second = service.list_approved_document_scopes(1, reader, offset=1, limit=2)
        assert [item['scope_id'] for item in second['items']] == ['picker-2', 'picker-3']
        assert not second['has_more']
        hidden = service.list_approved_document_scopes(1, {**reader, 'allowed_departments':['HR']}, limit=1)
        assert hidden == {'items':[], 'offset':0, 'limit':1, 'has_more':False}
        with store.begin() as conn:
            conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0'))
        assert service.list_approved_document_scopes(1, reader, limit=1)['items'] == []


@pytest.mark.parametrize('values', [{'doc_id':0}, {'doc_id':True}, {'offset':-1}, {'offset':100001},
                                  {'limit':0}, {'limit':51}, {'limit':True}])
def test_scope_picker_rejects_unbounded_inputs(store, values):
    with bind_repository_runtime(db_engine=store), pytest.raises(ValueError):
        service.list_approved_document_scopes(profile=PROFILE, **{'doc_id':1, **values})


def test_scope_picker_route_rechecks_actual_sql_and_hides_stale_scopes(store, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from .test_rag_evidence_api import client
    candidate = scope()
    save_extracted_scope(candidate, db_engine=store)
    with bind_repository_runtime(db_engine=store):
        approve(candidate)
    shared = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    with store.connect() as source, shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    api = client(monkeypatch)
    from mech_chatbot.api.dependencies import current_profile
    api.app.dependency_overrides[current_profile] = lambda: {**PROFILE, 'user_id':8, 'username':'another-reviewer'}
    try:
        with bind_repository_runtime(db_engine=shared):
            path = '/api/rag-evidence/documents/1/approved-scopes'
            assert api.get(path).json()['items'][0]['scope_id'] == 'scope-s1'
            with shared.begin() as conn:
                conn.execute(text('UPDATE TaiLieu SET Servable=0'))
            assert api.get(path).json() == {'items':[], 'offset':0, 'limit':50, 'has_more':False}
            assert api.get('/api/rag-evidence/documents/2/approved-scopes').json()['items'] == []
    finally:
        shared.dispose()


def test_graph_scope_picker_uses_request_snapshot_and_rechecks_source(bom_store):
    store, engine = bom_store
    profile = {**PROFILE, 'allowed_departments':['tech']}
    with bind_repository_runtime(db_engine=engine):
        page = service.list_approved_graph_scopes(1, profile, site='factory')
        assert page == {'items':[{'product_scope':'product','site':'factory'}], 'offset':0, 'limit':50, 'has_more':False}
        with pytest.raises(TimeoutError):
            service.list_approved_graph_scopes(1, profile, site='factory', deadline_seconds=0)
        with store.begin() as conn:
            conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=1'))
        assert service.list_approved_graph_scopes(1, profile, site='factory')['items'] == []


def test_graph_scope_picker_route_serves_only_readable_current_sql_declarations(bom_store, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.api.dependencies import current_profile
    from .test_rag_evidence_api import client
    from .test_rag_bom_chat import FixtureEngine
    store, _ = bom_store
    shared = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    with store.connect() as source, shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
        target.connection.driver_connection.create_function('CHARINDEX', 2, lambda needle, value:value.find(needle)+1)
    api = client(monkeypatch)
    api.app.dependency_overrides[current_profile] = lambda: {**PROFILE, 'allowed_departments':['tech']}
    try:
        with bind_repository_runtime(db_engine=FixtureEngine(shared)):
            path = '/api/rag-evidence/documents/1/graph-scopes?site=factory'
            assert api.get(path).json()['items'] == [{'product_scope':'product','site':'factory'}]
            assert api.get('/api/rag-evidence/documents/3/graph-scopes?site=factory').json() == {
                'items':[], 'offset':0, 'limit':50, 'has_more':False}
            with shared.begin() as conn:
                conn.execute(text('UPDATE TaiLieu SET VersionNo=2 WHERE DocID=1'))
            assert api.get(path).json()['items'] == []
    finally:
        shared.dispose()


def test_sql_graph_to_rollup_uses_full_reviewed_manifest_and_policy(bom_store):
    store,engine=bom_store
    policy = other_source(store, unit='piece', currency='')
    save_extracted_scope(policy, db_engine=store)
    profile = {**PROFILE, 'allowed_departments':['ENG','tech']}
    with bind_repository_runtime(db_engine=engine):
        blocked = service.rollup('document:1', profile, product_scope='product', site='factory',
                                 order_quantity='3', rounding_scope=policy.scope_id)
        assert blocked['status'] == 'blocked' and blocked['sources'] == []
        approve(policy, scale=0)
        result = service.rollup('document:1', profile, product_scope='product', site='factory',
                                order_quantity='3', rounding_scope=policy.scope_id)
        assert result['status'] == 'complete'
        assert result['steps'][0]['value'] == '6' and result['steps'][0]['unit'] == 'piece'
        assert result['sources'][0]['expected_occurrence_ids'] == ['1:row-1:1']
        assert result['sources'][0]['order_quantity'] == '3'
        assert result['sources'][1]['scope_id'] == policy.scope_id
        assert result['sources'][1]['source_revisions'] == [['4', '7']]
        expired = service.graph('document:1', profile, product_scope='product', site='factory', deadline_seconds=0)
        assert expired['status'] == 'unknown' and expired['edges'] == ()


def test_rollup_rejects_reviewed_money_policy_for_piece_quantities(bom_store):
    store,engine=bom_store
    policy = other_source(store)
    save_extracted_scope(policy, db_engine=store)
    profile = {**PROFILE, 'allowed_departments': ['ENG', 'tech']}
    with bind_repository_runtime(db_engine=engine):
        approve(policy)
        result = service.rollup('document:1', profile, product_scope='product', site='factory',
                                order_quantity='3', rounding_scope=policy.scope_id)
        assert result['status'] == 'blocked'
        assert result['reason'] == 'incompatible_rounding_scope'
        assert result['sources'] == [] and result['steps'] == []
