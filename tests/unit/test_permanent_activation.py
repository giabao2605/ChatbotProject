"""Synthetic signatures check permanent owner authority, never business acceptance."""
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mech_chatbot.evaluation.department_readiness import CANONICAL_DEPARTMENTS
from mech_chatbot.governance import feature_activation as activation
from mech_chatbot.governance.permanent_activation import validate_permanent_bundle, capability_active, LEDGER_SCHEMA
from .test_math_v2_activation import reference
from .test_math_v2_live_comparison import live_store
from .test_structured_evidence_repository import store


@pytest.fixture
def permanent(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(activation, '_release_authority_public_key', lambda *_: public)
    commit = 'a' * 40
    flags = {flag: flag in {'RAG_GROUNDED_MATH_ENABLED', 'RAG_GRAPH_RETRIEVAL_ENABLED',
                           'RAG_QUERY_DECOMPOSITION_ENABLED'} for flag in activation.FEATURE_FLAGS}
    governance = {'schema': 'rag-review-governance-v1', 'mode': 'single_owner', 'owner': 'fixture-owner',
                  'scope': 'default_rollout', 'source_commit': commit, 'risk_accepted': True,
                  'accepted_at': '2026-10-20T00:00:00Z', 'role_signoffs': {
                      role: {'owner': 'fixture-owner', 'signed': True, 'note': 'Synthetic authority fixture'}
                      for role in ('rag', 'security_qa', 'operations')}}
    governance_ref = reference(tmp_path, 'review.json', governance)
    versions = activation.feature_versions({})
    ledger = {'schema': LEDGER_SCHEMA, 'status': 'authorized', 'scope': 'default_rollout',
              'activation_profile': 'selective', 'source_commit': commit, 'feature_flags': flags,
              'versions': versions, 'lifetime': 'permanent', 'expires_at': None,
              'source_policy': 'reviewed_current_acl', 'departments': list(CANONICAL_DEPARTMENTS),
              'snapshot_fingerprint': 'b' * 64, 'business_accepted': False,
              'owner_authorization': {'owner': 'fixture-owner', 'authorized_at': '2026-10-20T00:00:00Z',
                                      'instruction': 'Enable Query Graph Math permanently for all 14 departments'},
              'review_governance': governance_ref}

    def issue(*, changes=None, bundle_changes=None):
        ledger_ref = reference(tmp_path, 'ledger.json', {**ledger, **(changes or {})})
        signature = base64.b64encode(key.sign((tmp_path / 'ledger.json').read_bytes())).decode()
        bundle = {'schema': 'rag-activation-bundle-v8', 'scope': 'default_rollout',
                  'activation_profile': 'selective', 'source_commit': commit, 'feature_flags': flags,
                  'versions': versions, 'graph_fingerprint': None, 'decision_ledger': ledger_ref,
                  'release_signature': {'algorithm': 'ed25519', 'value': signature},
                  'review_governance': governance_ref, **(bundle_changes or {})}
        digest = reference(tmp_path, 'bundle.json', bundle)['sha256']
        env = {**{flag: str(value).lower() for flag, value in flags.items()}, **versions,
               'RAG_ACTIVATION_SCOPE': 'default_rollout', 'RAG_ACTIVATION_PROFILE': 'selective',
               'RAG_DEPLOYMENT_GIT_SHA': commit, 'RAG_SNAPSHOT_FINGERPRINT': 'b' * 64}
        return bundle, env, digest
    return tmp_path, commit, issue


def validate(permanent, **kwargs):
    root, commit, issue = permanent
    bundle, env, digest = issue(**kwargs)
    return validate_permanent_bundle(bundle, root=root, environ=env, current_commit=commit, bundle_sha256=digest)


def test_signed_permanent_owner_authority_builds_existing_caps_without_expiry(permanent):
    cap = validate(permanent)
    assert cap is not None and cap.context == 'default_rollout' and cap.expires_at is None
    assert cap.reviewed_sources is True and cap.review_mode == 'single_owner'
    for child in (cap.math, cap.graph, cap.math.total, cap.math.comparison):
        assert child.context == 'default_rollout' and child.expires_at is None
        assert child.reviewed_sources is True and child.bundle_sha256 == cap.bundle_sha256
    assert cap.math.total.scope_pins == cap.math.comparison.scope_pins == cap.graph.graph_pins == ()


@pytest.mark.parametrize('changes', [
    {'lifetime': 'temporary'}, {'expires_at': '2099-01-01T00:00:00Z'}, {'expires_at': ''},
    {'source_policy': 'all_sources'}, {'business_accepted': True}, {'business_accepted': None},
    {'departments': list(CANONICAL_DEPARTMENTS[:-1])}, {'departments': [*CANONICAL_DEPARTMENTS, 'IT']},
    {'source_commit': 'c' * 40}, {'feature_flags': {}}, {'versions': {}},
    {'scope': 'controlled_demo'}, {'activation_profile': 'all'}, {'snapshot_fingerprint': 'c' * 64},
    {'review_governance': None}, {'owner_authorization': {}}, {'status': 'complete'},
])
def test_permanent_authority_refuses_broadened_or_incomplete_contract(permanent, changes):
    assert validate(permanent, changes=changes) is None


@pytest.mark.parametrize('mutation', ['signature', 'ledger', 'review', 'flags', 'versions', 'commit', 'scope'])
def test_permanent_grant_rechecks_signature_references_and_process_binding(permanent, mutation):
    root, commit, issue = permanent
    bundle, env, digest = issue()
    if mutation == 'signature':
        bundle = {**bundle, 'release_signature': {'algorithm': 'ed25519', 'value': 'invalid'}}
    elif mutation in {'ledger', 'review'}:
        (root / ('ledger.json' if mutation == 'ledger' else 'review.json')).write_text('{}')
    else:
        env = {**env, **{'flags': {'RAG_CRAG_ENABLED': 'true'}, 'versions': {'RAG_PLANNER_VERSION': 'other'},
                        'commit': {'RAG_DEPLOYMENT_GIT_SHA': 'c' * 40},
                        'scope': {'RAG_ACTIVATION_SCOPE': 'controlled_demo'}}[mutation]}
    assert validate_permanent_bundle(bundle, root=root, environ=env, current_commit=commit, bundle_sha256=digest) is None


def test_owner_authorization_must_match_review_identity_and_timezone(permanent):
    for owner in ({'owner': 'other', 'authorized_at': '2026-10-20T00:00:00Z', 'instruction': 'Enable'},
                  {'owner': 'fixture-owner', 'authorized_at': '2026-10-20', 'instruction': 'Enable'},
                  {'owner': 'fixture-owner', 'authorized_at': '2026-10-20T00:00:00Z', 'instruction': ''}):
        assert validate(permanent, changes={'owner_authorization': owner}) is None


@pytest.mark.parametrize('context,reviewed,expiry,active', [
    ('default_rollout', True, None, True), ('controlled_demo', True, None, False),
    ('default_rollout', False, None, False), ('default_rollout', True, '', False),
    ('default_rollout', True, '2099-01-01', False),
    ('controlled_demo', False, '2000-01-01T00:00:00Z', False),
    ('controlled_demo', False, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), True),
])
def test_expiry_omission_requires_exact_permanent_reviewed_source_mode(context, reviewed, expiry, active):
    @dataclass(frozen=True)
    class Cap:
        context: str
        reviewed_sources: bool
        expires_at: str | None
    assert capability_active(Cap(context, reviewed, expiry)) is active


@pytest.mark.parametrize('template', ['sum_reviewed_scope', 'compare_two_scopes'])
def test_permanent_calculation_completion_uses_current_reviewed_sources(permanent, live_store, monkeypatch, template):
    from mech_chatbot.config import logging
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    from .test_rag_scope_comparison import request
    from .test_rag_scope_total import selected
    cap = validate(permanent)
    events = []
    monkeypatch.setattr(logging, 'log_trace', lambda event, trace, **fields: events.append((event, fields)))
    query = selected() if template == 'sum_reviewed_scope' else request()
    with bind_repository_runtime(db_engine=live_store):
        result = collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: cap).run(query, RagInvocation('permanent')))
    assert result.outcome == 'answered'
    completion = next(fields for event, fields in events if event == (
        'math_scope_total_completed' if template == 'sum_reviewed_scope' else 'math_v2_comparison_completed'))
    assert completion['calculation_result_status'] == 'valid'
    assert completion['provenance_passed'] is completion['citation_structure_passed'] is completion['security_passed'] is True


@pytest.mark.parametrize('fault', ['scope_id', 'snapshot_id', 'scope_hash', 'row_set_sha256', 'row_count', 'consistency', 'duplicates'])
def test_permanent_completion_refuses_missing_or_malformed_current_scope_metadata(permanent, fault):
    from mech_chatbot.rag.math_v2_pilot import completion_provenance
    cap = validate(permanent).math.total
    scope = {'scope_id': 'current-reviewed-scope', 'snapshot_id': 'current-snapshot', 'scope_hash': 'c' * 64,
             'row_set_sha256': 'd' * 64, 'row_count': 2}
    math = {'scopes': [scope], 'read_consistency': 'serializable', 'contract': cap.contract,
            'activation_scope': 'default_rollout', 'offline_only': False}
    assert completion_provenance(math, cap, 1)
    if fault == 'consistency':
        math = {**math, 'read_consistency': None}
    elif fault == 'duplicates':
        math = {**math, 'scopes': [scope, scope]}
    else:
        math = {**math, 'scopes': [{**scope, fault: -1 if fault == 'row_count' else ''}]}
    assert not completion_provenance(math, cap, 1)
