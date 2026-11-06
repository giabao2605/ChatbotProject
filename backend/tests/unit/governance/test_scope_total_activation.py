"""Synthetic signed grants exercise authority; fixtures are never live evidence."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mech_chatbot.governance import feature_activation as activation
from mech_chatbot.governance.scope_total_activation import CONTRACT, ScopeTotalCapability
from mech_chatbot.governance.math_v2_activation import load_math_v2_capability, math_v2_bundle_selected
from scripts.ops.build_activation_bundle import build_activation_bundle


def reference(root, name, value):
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    (root / name).write_bytes(raw)
    return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'schema': value['schema']}


@pytest.fixture
def grant(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(activation, '_release_authority_public_key', lambda *_: public)
    pins = [{'scope_id': 'A', 'snapshot_id': 's1', 'scope_hash': 'a' * 64}]
    gate = {'schema': 'grounded-math-scope-total-controlled-demo-authorization-v1',
            'source_commit': 'candidate', 'scope': 'controlled_demo', 'contract': CONTRACT,
            'template': 'sum_reviewed_scope', 'scope_pins': pins, 'snapshot_fingerprint': 'c' * 64, 'passed': True,
            'production_eligible': True, 'offline_only': False, 'evidence_kind': 'staging',
            'review_mode': 'multi_reviewer'}
    decision = {'schema': 'milestone-decision-v2', 'milestone': 'grounded_math',
                'scope': 'controlled_demo', 'decision': 'accepted', 'source_commit': 'candidate',
                'reason': 'Synthetic review fixture only', 'evidence': [reference(tmp_path, 'gate.json', gate)],
                'reviewer_signoff': {'reviewer': 'fixture-reviewer', 'signed_at': '2026-10-16T00:00:00Z'}}
    ledger = {'schema': 'math-scope-total-controlled-demo-ledger-v1', 'status': 'complete',
              'scope': 'controlled_demo', 'source_commit': 'candidate', 'contract': CONTRACT,
              'template': 'sum_reviewed_scope', 'scope_pins': pins,
              'snapshot_fingerprint': 'c' * 64,
              'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
              'decision': reference(tmp_path, 'decision.json', decision)}

    def issue(**changes):
        current = {**ledger, **changes}
        reference(tmp_path, 'ledger.json', current)
        signature = base64.b64encode(key.sign((tmp_path / 'ledger.json').read_bytes())).decode()
        (tmp_path / 'signature.txt').write_text(signature)
        return build_activation_bundle(scope='controlled_demo', profile='selective', source_commit=current['source_commit'],
            decision_ledger='ledger.json', output='bundle.json', root=tmp_path,
            release_signature='signature.txt', enabled_features={'RAG_GROUNDED_MATH_ENABLED'}, scope_total=True)

    def environment(bundle, digest):
        return {**{name: str(value).lower() for name, value in bundle['feature_flags'].items()},
                **bundle['versions'], 'RAG_ACTIVATION_PROFILE': 'selective',
                'RAG_SNAPSHOT_FINGERPRINT': 'c' * 64,
                'RAG_ACTIVATION_SCOPE': 'controlled_demo', 'RAG_EXECUTION_CONTEXT': 'production',
                'RAG_DEPLOYMENT_GIT_SHA': 'candidate', 'RAG_ACTIVATION_BUNDLE_PATH': 'bundle.json',
                'RAG_ACTIVATION_BUNDLE_SHA256': digest}
    return tmp_path, ledger, gate, decision, issue, environment


def test_signed_total_grant_is_frozen_and_activation_authorized(grant):
    root, _, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    capability = load_math_v2_capability(env, root=root, current_commit='candidate')
    assert isinstance(capability, ScopeTotalCapability) and capability.template == 'sum_reviewed_scope'
    assert capability.scope_pins == (('A', 's1', 'a' * 64),)
    assert capability.bundle_sha256 == digest and capability.context == 'controlled_demo'
    with pytest.raises(AttributeError):
        capability.contract = 'other'
    status = activation.activation_status(env, root=root, current_commit='candidate')
    assert status.valid and status.live_authorized
    assert math_v2_bundle_selected(env, root=root)


@pytest.mark.parametrize('field,value', [('contract', 'grounded-math-v2-comparison-pilot-v1'), ('template', 'compare_two_scopes'),
    ('scope', 'default_rollout'), ('scope_pins', [{'scope_id': 'A', 'snapshot_id': '', 'scope_hash': 'a' * 64}]),
    ('expires_at', '2020-01-01T00:00:00Z')])
def test_builder_rejects_invalid_signed_contract(grant, field, value):
    *_, issue, _ = grant
    with pytest.raises(ValueError):
        issue(**{field: value})


@pytest.mark.parametrize('mutation', ['v1', 'signature', 'snapshot', 'template', 'rollback', 'other_feature', 'commit'])
def test_runtime_does_not_trust_bundle_or_environment_alone(grant, mutation):
    root, _, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    if mutation == 'v1':
        bundle = {**bundle, 'schema': 'rag-activation-bundle-v1'}
    elif mutation == 'signature':
        bundle = {**bundle, 'release_signature': {'algorithm': 'ed25519', 'value': base64.b64encode(b'x' * 64).decode()}}
    elif mutation in {'snapshot', 'template'}:
        ledger = json.loads((root / 'ledger.json').read_text())
        if mutation == 'snapshot':
            ledger['scope_pins'][0]['snapshot_id'] = 'replacement'
        else:
            ledger['template'] = 'bom_rollup'
        bundle = {**bundle, 'decision_ledger': reference(root, 'ledger.json', ledger)}
    elif mutation == 'rollback':
        env = {**env, 'RAG_ACTIVATION_BUNDLE_PATH': ''}
    elif mutation == 'other_feature':
        env = {**env, 'RAG_GRAPH_RETRIEVAL_ENABLED': 'true'}
    elif mutation == 'commit':
        env = {**env, 'RAG_DEPLOYMENT_GIT_SHA': 'different'}
    if mutation in {'v1', 'signature', 'snapshot', 'template'}:
        ref = reference(root, 'bundle.json', bundle)
        env = {**env, 'RAG_ACTIVATION_BUNDLE_SHA256': ref['sha256']}
    assert load_math_v2_capability(env, root=root, current_commit='candidate') is None


def test_scope_allowlist_and_expiry_are_checked_after_loading(grant):
    from dataclasses import replace
    from mech_chatbot.domain.structured_evidence import ScopeManifest, reviewed_scope_fingerprint
    root, _, gate, decision, issue, environment = grant
    scopes = tuple(ScopeManifest(key, snapshot, (), completeness_reviewed=True, authorized=True)
                   for key, snapshot in [('A', 's1')])
    pins = [{'scope_id': s.scope_id, 'snapshot_id': s.snapshot_id, 'scope_hash': reviewed_scope_fingerprint(s)} for s in scopes]
    gate = {**gate, 'scope_pins': pins}
    decision = {**decision, 'evidence': [reference(root, 'gate.json', gate)]}
    bundle, digest = issue(scope_pins=pins, decision=reference(root, 'decision.json', decision))
    cap = load_math_v2_capability(environment(bundle, digest), root=root, current_commit='candidate')
    assert cap.permits_scopes(scopes)
    assert not cap.permits_scopes((replace(scopes[0], snapshot_id='new'),))
    assert not cap.permits_scopes((replace(scopes[0], authorized=False),))
    assert not cap.permits_scopes((scopes[0], scopes[0]))
    assert not replace(cap, expires_at='2020-01-01T00:00:00Z').permits_scopes(scopes)


def test_broken_configured_bundle_does_not_select_legacy_math(grant):
    root, _, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    (root / 'bundle.json').write_text('invalid')
    assert math_v2_bundle_selected(env, root=root)
    assert math_v2_bundle_selected({**env, 'RAG_ACTIVATION_BUNDLE_PATH': '\x00'}, root=root)
    reference(root, 'bundle.json', {**bundle, 'schema': 'rag-activation-bundle-v1'})
    assert not math_v2_bundle_selected(env, root=root)


@pytest.mark.parametrize('mutation', ['offline_gate', 'unapproved', 'missing_decision'])
def test_offline_gate_or_unapproved_decision_cannot_issue_grant(grant, mutation):
    root, _, gate, decision, issue, _ = grant
    if mutation == 'offline_gate':
        gate = {**gate, 'offline_only': True, 'evidence_kind': 'offline'}
        decision = {**decision, 'evidence': [reference(root, 'gate.json', gate)]}
    elif mutation == 'unapproved':
        decision = {**decision, 'decision': 'pending', 'reviewer_signoff': {}}
    decision_ref = reference(root, 'decision.json', decision)
    with pytest.raises(ValueError):
        issue(decision=None if mutation == 'missing_decision' else decision_ref)


@pytest.mark.parametrize('pins', [[], None, [{}],
    [{'scope_id': 'A', 'snapshot_id': 's1', 'scope_hash': 'bad'}],
    [{'scope_id': 'A', 'snapshot_id': 's1', 'scope_hash': 'a'*64}]*2,
    [{'scope_id': str(i), 'snapshot_id': 's1', 'scope_hash': 'a'*64} for i in range(101)]])
def test_scope_pin_limits_fail_closed(grant, pins):
    *_, issue, _ = grant
    with pytest.raises(ValueError): issue(scope_pins=pins)


@pytest.mark.parametrize('field,value', [('schema', 'grounded-math-v2-controlled-demo-authorization-v1'),
    ('contract', 'grounded-math-v2-comparison-pilot-v1'), ('template', 'compare_two_scopes'),
    ('source_commit', 'old'), ('passed', False), ('production_eligible', False),
    ('offline_only', True), ('evidence_kind', 'offline'), ('review_mode', 'single_owner'),
    ('snapshot_fingerprint', 'd'*64), ('scope_pins', [])])
def test_total_authorization_cannot_borrow_comparison_or_offline_evidence(grant, field, value):
    root, _, gate, decision, issue, _ = grant
    gate = {**gate, field: value}
    decision = {**decision, 'evidence': [reference(root, 'gate.json', gate)]}
    with pytest.raises(ValueError): issue(decision=reference(root, 'decision.json', decision))


@pytest.mark.parametrize('changes', [{'reviewer_signoff': {}}, {'evidence': []},
    {'evidence': [{'path': 'missing.json', 'sha256': 'a'*64, 'schema': 'missing'}]}])
def test_missing_review_or_evidence_cannot_issue_total_grant(grant, changes):
    root, _, _, decision, issue, _ = grant
    with pytest.raises(ValueError):
        issue(decision=reference(root, 'decision.json', {**decision, **changes}))


def test_primary_evidence_can_issue_same_exact_total_contract(grant):
    root, _, gate, decision, issue, environment = grant
    decision = {**decision, 'evidence': [reference(root, 'gate.json', {**gate, 'evidence_kind': 'primary'})]}
    bundle, digest = issue(decision=reference(root, 'decision.json', decision))
    assert isinstance(load_math_v2_capability(environment(bundle, digest), root=root,
        current_commit='candidate'), ScopeTotalCapability)


@pytest.mark.parametrize('mutation', ['versions', 'snapshot', 'governance_missing', 'governance_invalid', 'hash'])
def test_runtime_rechecks_total_grant_bindings(grant, mutation):
    root, _, _, _, issue, environment = grant
    bundle, digest = issue()
    env = environment(bundle, digest)
    if mutation == 'versions': env = {**env, 'RAG_PLANNER_VERSION': 'other'}
    elif mutation == 'snapshot': env = {**env, 'RAG_SNAPSHOT_FINGERPRINT': 'd'*64}
    elif mutation == 'hash': env = {**env, 'RAG_ACTIVATION_BUNDLE_SHA256': '0'*64}
    else:
        governance = {'path': 'missing.json', 'sha256': 'a'*64, 'schema': 'rag-review-governance-v1'}
        if mutation == 'governance_invalid':
            governance = reference(root, 'governance.json', {'schema': 'rag-review-governance-v1', 'mode': 'unknown'})
        bundle = {**bundle, 'review_governance': governance}
        env = {**env, 'RAG_ACTIVATION_BUNDLE_SHA256': reference(root, 'bundle.json', bundle)['sha256']}
    assert load_math_v2_capability(env, root=root, current_commit='candidate') is None
    assert not activation.activation_status(env, root=root, current_commit='candidate').live_authorized


def test_total_bundle_mode_is_exclusive(grant):
    root, *_ = grant
    with pytest.raises(ValueError, match='mutually exclusive'):
        build_activation_bundle(scope='controlled_demo', profile='selective', source_commit='candidate',
            decision_ledger='ledger.json', output='bundle.json', root=root, math_v2=True, scope_total=True)
