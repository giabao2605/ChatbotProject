"""Synthetic signed Graph grants are authorization tests, never live evidence."""
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mech_chatbot.domain.graph_query import GraphQuery, graph_fingerprint, graph_query_context
from mech_chatbot.governance import feature_activation as activation
from mech_chatbot.governance.graph_v2_activation import (
    AUTHORIZATION_SCHEMA, CONTRACT, LEDGER_SCHEMA, graph_pins_fingerprint,
    graph_v2_bundle_selected, load_graph_v2_capability,
)
from scripts.ops.build_activation_bundle import build_activation_bundle
from .test_math_v2_activation import reference, grant as math_grant


@pytest.fixture
def grant(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(activation, '_release_authority_public_key', lambda *_: public)
    query = GraphQuery('document:1', 'assembly', 'factory')
    value = {'contract_version': 'graph-v2', 'status': 'partial', 'edges': (),
             'manifest': None, 'reasons': ('coverage_unverified',), 'examined_relations': 0}
    pins = [{'graph_context': graph_query_context(query), 'graph_hash': graph_fingerprint(value)}]
    snapshot = graph_pins_fingerprint(pins)
    gate = {'schema': AUTHORIZATION_SCHEMA, 'scope': 'controlled_demo', 'contract': CONTRACT,
            'source_commit': 'candidate', 'graph_pins': pins, 'snapshot_fingerprint': snapshot,
            'passed': True, 'production_eligible': True, 'offline_only': False,
            'evidence_kind': 'primary', 'review_mode': 'multi_reviewer'}
    decision = {'schema': 'milestone-decision-v2', 'milestone': 'graph_retrieval',
                'scope': 'controlled_demo', 'decision': 'accepted', 'source_commit': 'candidate',
                'reason': 'Synthetic fixture only', 'evidence': [reference(tmp_path, 'graph-gate.json', gate)],
                'reviewer_signoff': {'reviewer': 'fixture-reviewer', 'signed_at': '2026-10-17T00:00:00Z'}}
    ledger = {'schema': LEDGER_SCHEMA, 'status': 'complete', 'scope': 'controlled_demo',
              'source_commit': 'candidate', 'contract': CONTRACT, 'graph_pins': pins,
              'snapshot_fingerprint': snapshot,
              'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
              'decision': reference(tmp_path, 'graph-decision.json', decision)}

    def issue(*, flags=None, governance=None, **changes):
        current = {**ledger, **changes}
        reference(tmp_path, 'graph-ledger.json', current)
        (tmp_path / 'graph-signature.txt').write_text(base64.b64encode(
            key.sign((tmp_path / 'graph-ledger.json').read_bytes())).decode())
        return build_activation_bundle(scope='controlled_demo', profile='selective',
            source_commit=current['source_commit'], decision_ledger='graph-ledger.json',
            output='graph-bundle.json', root=tmp_path, release_signature='graph-signature.txt',
            enabled_features={'RAG_GRAPH_RETRIEVAL_ENABLED'} if flags is None else flags,
            review_governance=governance, graph_v2=True)

    def environment(bundle, digest):
        return {**{name: str(value).lower() for name, value in bundle['feature_flags'].items()},
                **bundle['versions'], 'RAG_ACTIVATION_PROFILE': 'selective',
                'RAG_ACTIVATION_SCOPE': 'controlled_demo', 'RAG_DEPLOYMENT_GIT_SHA': 'candidate',
                'RAG_SNAPSHOT_FINGERPRINT': snapshot, 'RAG_ACTIVATION_BUNDLE_PATH': 'graph-bundle.json',
                'RAG_ACTIVATION_BUNDLE_SHA256': digest}
    return tmp_path, query, value, ledger, gate, decision, issue, environment


def test_signed_partial_result_is_pinned_without_upgrading_completeness(grant):
    root, query, value, _, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    cap = load_graph_v2_capability(env, root=root, current_commit='candidate')
    assert bundle['schema'] == 'rag-activation-bundle-v3'
    assert cap and cap.contract == CONTRACT and cap.context == 'controlled_demo'
    assert cap.bundle_sha256 == digest and cap.permits_result(query, value)
    assert cap.permits_query(query) and not cap.permits_query(None)
    assert value['status'] == 'partial'
    assert not cap.permits_result(query, {**value, 'status': 'complete'})
    assert not cap.permits_result(replace(query, product_scope='other'), value)
    assert not replace(cap, expires_at='2020-01-01T00:00:00Z').permits_result(query, value)
    with pytest.raises(AttributeError):
        cap.contract = 'other'
    status = activation.activation_status(env, root=root, current_commit='candidate')
    assert status.valid and status.live_authorized


@pytest.mark.parametrize('mutation', ['revision', 'source_hash', 'review_status', 'acl_subset'])
def test_changed_source_review_or_acl_result_is_not_inherited(grant, mutation):
    root, query, value, _, _, _, issue, environment = grant
    bundle, digest = issue()
    cap = load_graph_v2_capability(environment(bundle, digest), root=root, current_commit='candidate')
    changed = {**value, **{'revision': {'source_revisions': [[1, 2]]},
        'source_hash': {'source_hash': 'd' * 64}, 'review_status': {'reasons': ['review_revoked']},
        'acl_subset': {'edges': [{'occurrence_id': 'visible-subset'}]}}[mutation]}
    assert not cap.permits_result(query, changed)


def test_separately_pinned_acl_variants_share_only_the_approved_query(grant):
    root, query, value, ledger, gate, decision, issue, environment = grant
    other = {**value, 'edges': [{'occurrence_id': 'another-visible-subset'}]}
    pins = [*ledger['graph_pins'], {'graph_context': graph_query_context(query), 'graph_hash': graph_fingerprint(other)}]
    snapshot = graph_pins_fingerprint(pins)
    gate = {**gate, 'graph_pins': pins, 'snapshot_fingerprint': snapshot}
    decision = {**decision, 'evidence': [reference(root, 'graph-gate.json', gate)]}
    bundle, digest = issue(graph_pins=pins, snapshot_fingerprint=snapshot,
                          decision=reference(root, 'graph-decision.json', decision))
    cap = load_graph_v2_capability({**environment(bundle, digest), 'RAG_SNAPSHOT_FINGERPRINT': snapshot},
                                    root=root, current_commit='candidate')
    assert cap.permits_result(query, value) and cap.permits_result(query, other)
    assert not cap.permits_result(query, {**other, 'examined_relations': 1})


@pytest.mark.parametrize('field,value', [('contract', 'graph-v1'), ('scope', 'default_rollout'),
    ('snapshot_fingerprint', 'd' * 64), ('graph_pins', []), ('expires_at', '2020-01-01T00:00:00Z')])
def test_builder_rejects_invalid_graph_contract(grant, field, value):
    *_, issue, _ = grant
    with pytest.raises(ValueError):
        issue(**{field: value})


@pytest.mark.parametrize('mutation', ['signature', 'hash', 'commit', 'flags', 'version', 'rollback', 'ledger'])
def test_runtime_rejects_changed_bundle_or_deployment(grant, mutation):
    root, _, _, ledger, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    if mutation == 'signature':
        bundle = {**bundle, 'release_signature': {'algorithm': 'ed25519', 'value': base64.b64encode(b'x' * 64).decode()}}
        env['RAG_ACTIVATION_BUNDLE_SHA256'] = reference(root, 'graph-bundle.json', bundle)['sha256']
    elif mutation == 'hash':
        env['RAG_ACTIVATION_BUNDLE_SHA256'] = '0' * 64
    elif mutation == 'commit':
        env['RAG_DEPLOYMENT_GIT_SHA'] = 'replacement'
    elif mutation == 'flags':
        env['RAG_GROUNDED_MATH_ENABLED'] = 'true'
    elif mutation == 'version':
        env['RAG_GRAPH_SERVING_EPOCH'] = 'replacement'
    elif mutation == 'rollback':
        env['RAG_ACTIVATION_BUNDLE_PATH'] = ''
    else:
        reference(root, 'graph-ledger.json', {**ledger, 'graph_pins': []})
    assert load_graph_v2_capability(env, root=root, current_commit='candidate') is None


@pytest.mark.parametrize('mutation', ['missing_gate', 'offline', 'rejected', 'wrong_pins', 'wrong_commit'])
def test_invalid_gate_cannot_issue_graph_grant(grant, mutation):
    root, _, _, _, gate, decision, issue, _ = grant
    if mutation == 'missing_gate':
        decision = {**decision, 'evidence': []}
    elif mutation == 'rejected':
        decision = {**decision, 'decision': 'rejected'}
    else:
        changes = {'offline': {'offline_only': True}, 'wrong_pins': {'graph_pins': []},
                   'wrong_commit': {'source_commit': 'other'}}[mutation]
        decision = {**decision, 'evidence': [reference(root, 'graph-gate.json', {**gate, **changes})]}
    with pytest.raises(ValueError):
        issue(decision=reference(root, 'graph-decision.json', decision))


@pytest.mark.parametrize('flags', [set(), {'RAG_GROUNDED_MATH_ENABLED'},
    {'RAG_GRAPH_RETRIEVAL_ENABLED', 'RAG_GROUNDED_MATH_ENABLED'}])
def test_graph_grant_never_authorizes_other_flags(grant, flags):
    *_, issue, _ = grant
    with pytest.raises(ValueError):
        issue(flags=flags)


def test_pin_digest_is_sorted_and_binds_both_context_and_result(grant):
    _, query, value, ledger, *_ = grant
    second = {'graph_context': graph_query_context(replace(query, root_key='document:2')),
              'graph_hash': graph_fingerprint(value)}
    pins = [*ledger['graph_pins'], second]
    assert graph_pins_fingerprint(pins) == graph_pins_fingerprint(pins[::-1])
    assert graph_pins_fingerprint(pins) != ledger['snapshot_fingerprint']
    with pytest.raises(ValueError):
        graph_pins_fingerprint([pins[0], pins[0]])
    with pytest.raises(ValueError):
        graph_pins_fingerprint([{**pins[0], 'graph_context': {**pins[0]['graph_context'], 'max_relations': 501}}])


def test_graph_bundle_selection_is_fail_closed_but_does_not_select_math(grant):
    root, _, _, _, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    assert graph_v2_bundle_selected(env, root=root)
    (root / 'graph-bundle.json').write_text('invalid')
    assert graph_v2_bundle_selected(env, root=root)
    assert graph_v2_bundle_selected({**env, 'RAG_ACTIVATION_BUNDLE_PATH': '\x00'}, root=root)
    reference(root, 'graph-bundle.json', {**bundle, 'schema': 'rag-activation-bundle-v2'})
    assert not graph_v2_bundle_selected(env, root=root)
    assert not graph_v2_bundle_selected({'RAG_ACTIVATION_BUNDLE_PATH': ''}, root=root)


def test_explicit_owner_governance_is_bound_to_gate_and_commit(grant):
    root, _, _, _, gate, decision, issue, environment = grant
    governance = {'schema': 'rag-review-governance-v1', 'mode': 'single_owner', 'owner': 'fixture-owner',
                  'scope': 'controlled_demo', 'source_commit': 'candidate', 'risk_accepted': True,
                  'accepted_at': '2026-10-17T00:00:00Z', 'role_signoffs': {
                      role: {'owner': 'fixture-owner', 'signed': True, 'note': 'Synthetic fixture only'}
                      for role in ('rag', 'security_qa', 'operations')}}
    reference(root, 'graph-governance.json', governance)
    decision = {**decision, 'evidence': [reference(root, 'graph-gate.json', {**gate, 'review_mode': 'single_owner'})]}
    decision_ref = reference(root, 'graph-decision.json', decision)
    bundle, digest = issue(governance='graph-governance.json', decision=decision_ref)
    cap = load_graph_v2_capability(environment(bundle, digest), root=root, current_commit='candidate')
    assert cap and cap.review_mode == 'single_owner'
    reference(root, 'graph-governance.json', {**governance, 'source_commit': 'other'})
    assert load_graph_v2_capability(environment(bundle, digest), root=root, current_commit='candidate') is None


def test_settings_authorizer_rechecks_commit_and_bundle_on_each_call(grant, monkeypatch):
    from types import SimpleNamespace
    from mech_chatbot.governance import graph_v2_activation as graph_activation
    root, _, _, _, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    commits = iter(('candidate', 'replacement'))
    monkeypatch.setattr(activation, 'current_git_commit', lambda _: next(commits))
    original = graph_activation.load_graph_v2_capability
    monkeypatch.setattr(graph_activation, 'load_graph_v2_capability',
                        lambda environ, **kwargs: original(environ, root=root, current_commit=kwargs['current_commit']))
    authorizer = graph_activation.graph_v2_authorizer_for_settings(SimpleNamespace(**env))
    assert authorizer() is not None
    assert authorizer() is None


def test_existing_math_bundle_remains_authorized(math_grant):
    root, _, _, _, issue, environment = math_grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    assert activation.activation_status(env, root=root, current_commit='candidate').live_authorized
    assert load_graph_v2_capability(env, root=root, current_commit='candidate') is None
    assert not graph_v2_bundle_selected(env, root=root)


def test_builder_rejects_ambiguous_v2_selection(grant):
    root, *_ = grant
    with pytest.raises(ValueError, match='exclusive'):
        build_activation_bundle(scope='controlled_demo', profile='selective', source_commit='candidate',
            decision_ledger='graph-ledger.json', output='ambiguous.json', root=root,
            math_v2=True, graph_v2=True)
