from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from mech_chatbot.domain.serving_state import filter_currently_servable, is_history_servable
from mech_chatbot.db.repositories.feedback_access import pin_generation_sources
from mech_chatbot.rag.phases.evidence import _refusal_request_validation
from .test_feedback_scoped_flow import scoped_db


def metadata(**overrides):
    return {
        'servable': True, 'publication_state': 'published', 'lifecycle_status': 'published',
        'review_status': 'approved', 'is_current': True, 'effective_status': 'effective',
        'effective_date': None, 'expiry_date': None, 'site': 'HN',
        'security_level': 'internal', 'phong_ban_quyen': ['ENG'], **overrides,
    }


def history_actor(actor):
    return {**actor, 'roles': [*actor['roles'], 'document_history_reader']}


def excerpt():
    return {'doc_id': 1, 'version_no': 2, 'trang': 1, 'text': 'approved content',
            'is_current': True, 'effective_status': 'effective', 'lifecycle_status': 'published'}


def test_history_filter_includes_current_and_expired_by_date():
    current = SimpleNamespace(metadata=metadata())
    expired = SimpleNamespace(metadata=metadata(expiry_date='2000-01-01'))
    assert filter_currently_servable([current, expired]) == [current]
    assert filter_currently_servable([current, expired], allow_history=True) == [current, expired]


def test_history_filter_uses_the_same_explicit_day_as_current_filter():
    expired = SimpleNamespace(metadata=metadata(expiry_date='2099-01-01'))
    assert filter_currently_servable([expired], today=date(2099, 1, 2), allow_history=True) == [expired]


@pytest.mark.parametrize('status', ['draft', 'revoked'])
def test_history_rejects_unsafe_effective_states(status):
    assert not is_history_servable(metadata(is_current=False, effective_status=status))


@pytest.mark.parametrize('changes', [
    {'effective_date': '2999-01-01'}, {'expiry_date': 'malformed'},
    {'lifecycle_status': 'revoked'}, {'lifecycle_status': 'draft'},
    {'review_status': 'rejected'}, {'publication_state': 'draft'},
])
def test_history_keeps_existing_future_and_unpublished_guards(changes):
    assert not is_history_servable(metadata(is_current=False, **changes))


def test_generation_pin_marks_expiry_from_authoritative_sql(scoped_db):
    engine, actor = scoped_db
    with engine.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET ExpiryDate='2000-01-01' WHERE DocID=1"))
    with engine.connect() as conn:
        pinned = pin_generation_sources(conn, [excerpt()], history_actor(actor), allow_history=True)
    assert pinned[0]['evidence_pin']['historical'] is True


def test_generation_pin_does_not_infer_history_permission_from_role_only(scoped_db):
    engine, actor = scoped_db
    with engine.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET ExpiryDate='2000-01-01' WHERE DocID=1"))
    with engine.connect() as conn, pytest.raises(PermissionError):
        pin_generation_sources(conn, [excerpt()], history_actor(actor))


@pytest.mark.parametrize('role,department,site', [
    (False, 'ENG', 'HN'), (True, 'HR', 'HN'), (True, 'ENG', 'HCM'),
])
def test_history_pin_keeps_role_department_and_site_checks(scoped_db, role, department, site):
    engine, actor = scoped_db
    actor = history_actor(actor) if role else actor
    actor = {**actor, 'allowed_departments': [department], 'allowed_sites': [site]}
    with engine.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET ExpiryDate='2000-01-01' WHERE DocID=1"))
    with engine.connect() as conn, pytest.raises(PermissionError):
        pin_generation_sources(conn, [excerpt()], actor, allow_history=True)


def test_current_pin_stays_current_in_history_mode(scoped_db):
    engine, actor = scoped_db
    with engine.connect() as conn:
        pinned = pin_generation_sources(conn, [excerpt()], history_actor(actor), allow_history=True)
    assert 'historical' not in pinned[0]['evidence_pin']


@pytest.mark.parametrize('policy,role,allowed', [
    ('include_archived', True, True), ('compare_versions', True, True),
    ('current_only', True, False), ('include_archived', False, False),
])
def test_refusal_validation_requires_history_intent_and_role(policy, role, allowed):
    request = SimpleNamespace(user_department='ENG', user_roles=['viewer'] + (['document_history_reader'] if role else []),
                              allowed_departments=['ENG'], max_security_level='internal', allowed_sites=['HN'])
    document = SimpleNamespace(metadata=metadata(expiry_date='2000-01-01'))
    result = _refusal_request_validation(request, [document], version_policy=policy)
    assert result['access_scope_passed'] is True
    assert result['leakage_passed'] is allowed


@pytest.mark.parametrize('policy,roles,allowed', [
    ('include_archived', ['document_history_reader'], True),
    ('compare_versions', ['document_history_reader'], True),
    ('current_only', ['document_history_reader'], False),
    ('include_archived', ['admin'], False),
])
def test_evidence_phase_passes_request_scoped_history_permission(monkeypatch, policy, roles, allowed):
    from mech_chatbot.rag.phases.evidence import evaluate_evidence
    from mech_chatbot.services import feedback_service

    class PinCalled(Exception):
        pass

    def pin(*args, **kwargs):
        assert kwargs['allow_history'] is allowed
        raise PinCalled

    monkeypatch.setattr(feedback_service, 'pin_generation_evidence', pin)
    decision = SimpleNamespace(request=SimpleNamespace(user_roles=roles, image_analysis=None),
                               intent_data={'version_policy': policy})
    state = SimpleNamespace(request=SimpleNamespace(access=None), checkpoint=None,
                            budget=SimpleNamespace(deadline_monotonic=1))
    with pytest.raises(PinCalled):
        evaluate_evidence(decision, None, None, SimpleNamespace(documents=[]), state)


def test_service_preserves_expired_pin_only_for_explicit_history_request(scoped_db):
    from langchain_core.documents import Document
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import AccessScope
    from mech_chatbot.services.feedback_service import pin_generation_evidence, EvidenceCertificationError

    engine, _ = scoped_db
    with engine.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET ExpiryDate='2000-01-01' WHERE DocID=1"))
        conn.execute(text("INSERT INTO Roles VALUES(2,'document_history_reader')"))
        conn.execute(text('INSERT INTO UserRoles VALUES(9,2)'))
    access = AccessScope(user_id=9, username='reviewer', roles=frozenset({'reviewer', 'document_history_reader'}),
                         allowed_departments=frozenset({'ENG'}), allowed_sites=frozenset({'HN'}),
                         max_security_level='internal')
    document = Document(page_content='approved content', metadata=excerpt())
    with bind_repository_runtime(db_engine=engine):
        pinned = pin_generation_evidence([document], access, allow_history=True)
        assert pinned[0].metadata['evidence_pin']['historical'] is True
        with pytest.raises(EvidenceCertificationError):
            pin_generation_evidence([document], access)


@pytest.mark.parametrize('policy,roles,expired_site,allowed', [
    ('compare_versions', ['document_history_reader'], 'HN', True),
    ('include_archived', ['document_history_reader'], 'HN', True),
    ('current_only', ['document_history_reader'], 'HN', False),
    ('compare_versions', ['viewer'], 'HN', False),
    ('include_archived', ['viewer'], 'HN', False),
    ('compare_versions', ['document_history_reader'], 'HCM', False),
])
def test_generation_leakage_checks_current_and_expired_sources_together(
    monkeypatch, policy, roles, expired_site, allowed,
):
    from mech_chatbot.rag.phases import generation

    # Isolate unrelated diagnostics; retain real serving-state and access predicates.
    monkeypatch.setattr(generation, 'make_debug_info', lambda documents: {})
    monkeypatch.setattr(generation, 'make_phase_diagnostics', lambda *args: {})
    documents = [
        SimpleNamespace(metadata=metadata(doc_id=1)),
        SimpleNamespace(metadata=metadata(doc_id=2, expiry_date='2000-01-01', site=expired_site)),
    ]
    request = SimpleNamespace(user_department='ENG', user_roles=roles, allowed_departments=['ENG'],
                              max_security_level='internal', allowed_sites=['HN'])
    decision = SimpleNamespace(request=request, intent_data={'version_policy': policy})
    enrichment = SimpleNamespace(community_summary_used=False, community_summary_count=0,
                                 community_documents=[], community_fallback_reason=None)
    evidence = SimpleNamespace(answer_policy=None, evidence_decision=None, evidence_quotes=())
    result = generation._generation_debug(
        decision, None, enrichment, None, evidence, None, documents,
        {'decomposition_usage': {}, 'citation_structure_passed': True, 'provenance_passed': True},
    )['pilot_request_validation']
    assert result['access_scope_passed'] is (expired_site == 'HN')
    assert result['leakage_passed'] is allowed
