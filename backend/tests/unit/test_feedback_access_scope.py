import pytest

from mech_chatbot.application import feedback_access

pytestmark = pytest.mark.unit


def test_complete_answer_requires_every_source_and_a_valid_manifest():
    actor = {'user_id': 9, 'username': 'reviewer', 'roles': ['reviewer'],
             'allowed_departments': ['ENG'], 'allowed_sites': ['HN'], 'max_security_level': 'internal'}
    scope = feedback_access.validated_scope(actor, 'ENG', 'HN')
    pins = [{'doc_id': 1, 'version_no': 2, 'source_hash': 'a' * 64},
            {'doc_id': 2, 'version_no': 1, 'source_hash': 'b' * 64}]
    basis = feedback_access.access_basis(scope, pins)
    checked = []
    def source(pin):
        checked.append(pin['doc_id'])
        return pin['doc_id'] == 1
    assert not feedback_access.basis_allowed(basis, actor, source)
    assert checked == [1, 2]
    assert not feedback_access.basis_allowed(None, actor, lambda _: True)
    assert not feedback_access.basis_allowed({**basis, 'complete': False}, actor, lambda _: True)
    assert not feedback_access.basis_allowed({**basis, 'evidence_count': 1}, actor, lambda _: True)
    assert feedback_access.basis_allowed(basis, actor, lambda _: True)
    assert not feedback_access.basis_allowed(basis, {**actor, 'allowed_sites': ['HCM']}, lambda _: True)


def test_manual_scope_defaults_to_actor_clearance_and_cannot_broaden_it():
    actor = {'roles': ['reviewer'], 'allowed_departments': ['ENG'],
             'allowed_sites': ['HN'], 'max_security_level': 'internal'}
    assert feedback_access.validated_scope(actor, 'ENG', 'HN')['max_security_level'] == 'internal'
    for values in [('ENG', 'HN', 'confidential'), ('HR', 'HN', None), ('ENG', None, None)]:
        with pytest.raises(ValueError):
            feedback_access.validated_scope(actor, *values)


@pytest.mark.parametrize('invalid', [
    {'doc_id': True}, {'doc_id': 0}, {'version_no': None},
    {'source_hash': None}, {'source_hash': 'not-a-source-digest'},
])
def test_malformed_source_pin_is_denied_before_source_lookup(invalid):
    actor = {'roles': ['reviewer'], 'allowed_departments': ['ENG'],
             'allowed_sites': ['HN'], 'max_security_level': 'internal'}
    source = {'doc_id': 1, 'version_no': 2, 'source_hash': 'a' * 64, **invalid}
    basis = feedback_access.access_basis(feedback_access.validated_scope(actor, 'ENG', 'HN'), [source])
    def must_not_query(_):
        pytest.fail('Malformed identity or digest must not reach source lookup')
    assert not feedback_access.basis_allowed(basis, actor, must_not_query)
