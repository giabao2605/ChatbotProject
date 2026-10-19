"""Synthetic signed Math template fixtures never stand in for release evidence."""
import base64
from datetime import datetime, timedelta, timezone
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mech_chatbot.governance import feature_activation as activation
from mech_chatbot.governance import math_v2_activation as math_activation
from mech_chatbot.governance import scope_total_activation as total_activation
from mech_chatbot.governance.math_templates_activation import (
    AUTHORIZATION_SCHEMA, CONTRACT, LEDGER_SCHEMA, combined_snapshot_fingerprint,
    MathTemplatesCapability, select_math_capability,
)
from scripts.ops.build_activation_bundle import build_activation_bundle
from .test_math_v2_activation import reference


@pytest.fixture
def grant(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(activation, '_release_authority_public_key', lambda *_: public)
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=1)).isoformat()
    child_bundles = {}
    for name, module in (('comparison', math_activation), ('total', total_activation)):
        if name == 'comparison':
            pins = [{'scope_id': 'A', 'snapshot_id': 's1', 'scope_hash': 'a' * 64},
                    {'scope_id': 'B', 'snapshot_id': 's2', 'scope_hash': 'b' * 64}]
            binding = {'scope_pins': pins, 'template': 'compare_two_scopes'}
            snapshot = 'c' * 64
        else:
            pins = [{'scope_id': 'T', 'snapshot_id': 's3', 'scope_hash': 'd' * 64}]
            binding = {'scope_pins': pins, 'template': 'sum_reviewed_scope'}
            snapshot = 'e' * 64
        gate = {'schema': module.AUTHORIZATION_SCHEMA, 'source_commit': 'candidate',
                'scope': 'controlled_demo', 'contract': module.CONTRACT, **binding,
                'snapshot_fingerprint': snapshot, 'passed': True, 'production_eligible': True,
                'offline_only': False, 'evidence_kind': 'staging', 'review_mode': 'multi_reviewer'}
        decision = {'schema': 'milestone-decision-v2', 'milestone': 'grounded_math',
                    'scope': 'controlled_demo', 'decision': 'accepted', 'source_commit': 'candidate',
                    'reason': 'Synthetic child only', 'evidence': [reference(tmp_path, f'{name}-gate.json', gate)],
                    'reviewer_signoff': {'reviewer': 'fixture', 'signed_at': now.isoformat()}}
        ledger = {'schema': module.LEDGER_SCHEMA, 'status': 'complete', 'scope': 'controlled_demo',
                  'source_commit': 'candidate', 'contract': module.CONTRACT, **binding,
                  'snapshot_fingerprint': snapshot, 'expires_at': expires,
                  'decision': reference(tmp_path, f'{name}-decision.json', decision)}
        reference(tmp_path, f'{name}-ledger.json', ledger)
        (tmp_path / f'{name}-signature.txt').write_text(base64.b64encode(
            key.sign((tmp_path / f'{name}-ledger.json').read_bytes())).decode())
        bundle, _ = build_activation_bundle(scope='controlled_demo', profile='selective',
            source_commit='candidate', decision_ledger=f'{name}-ledger.json', output=f'{name}-bundle.json',
            root=tmp_path, release_signature=f'{name}-signature.txt', math_v2=name == 'comparison', scope_total=name == 'total',
            enabled_features={'RAG_GROUNDED_MATH_ENABLED'})
        child_bundles[name] = bundle

    def issue(*, parent_flags=None, gate_changes=None, decision_changes=None,
              **changes):
        children = {name: reference(tmp_path, f'{name}-bundle.json', child_bundles[name]) for name in ('comparison', 'total')}
        snapshots = {name: json.loads((tmp_path / f'{name}-ledger.json').read_text())['snapshot_fingerprint']
                     for name in children}
        snapshot = combined_snapshot_fingerprint(snapshots['comparison'], snapshots['total'])
        gate = {'schema': AUTHORIZATION_SCHEMA, 'source_commit': 'candidate', 'scope': 'controlled_demo',
                'contract': CONTRACT, 'children': children, 'snapshot_fingerprint': snapshot,
                'passed': True, 'production_eligible': True, 'offline_only': False,
                'evidence_kind': 'primary', 'review_mode': 'multi_reviewer', **(gate_changes or {})}
        decision = {'schema': 'milestone-decision-v2', 'milestone': 'math_templates', 'scope': 'controlled_demo',
                    'decision': 'accepted', 'source_commit': 'candidate', 'reason': 'Synthetic pair only',
                    'evidence': [reference(tmp_path, 'pair-gate.json', gate)],
                    'reviewer_signoff': {'reviewer': 'fixture', 'signed_at': now.isoformat()}, **(decision_changes or {})}
        ledger = {'schema': LEDGER_SCHEMA, 'status': 'complete', 'scope': 'controlled_demo', 'contract': CONTRACT,
                  'source_commit': 'candidate', 'children': children, 'snapshot_fingerprint': snapshot,
                  'expires_at': (now + timedelta(hours=2)).isoformat(),
                  'decision': reference(tmp_path, 'pair-decision.json', decision), **changes}
        reference(tmp_path, 'pair-ledger.json', ledger)
        (tmp_path / 'pair-signature.txt').write_text(base64.b64encode(
            key.sign((tmp_path / 'pair-ledger.json').read_bytes())).decode())
        bundle, digest = build_activation_bundle(scope='controlled_demo', profile='selective', source_commit='candidate',
            decision_ledger='pair-ledger.json', output='pair-bundle.json', root=tmp_path,
            release_signature='pair-signature.txt', math_templates=True,
            enabled_features={'RAG_GROUNDED_MATH_ENABLED'} if parent_flags is None else parent_flags)
        env = {**{flag: str(value).lower() for flag, value in bundle['feature_flags'].items()}, **bundle['versions'],
               'RAG_ACTIVATION_SCOPE': 'controlled_demo', 'RAG_ACTIVATION_PROFILE': 'selective',
               'RAG_DEPLOYMENT_GIT_SHA': 'candidate', 'RAG_SNAPSHOT_FINGERPRINT': snapshot,
               'RAG_ACTIVATION_BUNDLE_PATH': 'pair-bundle.json', 'RAG_ACTIVATION_BUNDLE_SHA256': digest}
        return bundle, digest, env

    def change_child(name, *, ledger_changes=None, **bundle_changes):
        bundle = child_bundles[name]
        if ledger_changes:
            ledger = {**json.loads((tmp_path / f'{name}-ledger.json').read_text()), **ledger_changes}
            bundle = {**bundle, 'decision_ledger': reference(tmp_path, f'{name}-ledger.json', ledger),
                      'release_signature': {'algorithm': 'ed25519', 'value': base64.b64encode(
                          key.sign((tmp_path / f'{name}-ledger.json').read_bytes())).decode()}}
        child_bundles[name] = {**bundle, **bundle_changes}
    return tmp_path, issue, change_child, expires


def test_pair_returns_existing_child_types_bound_to_parent_transport_and_shortest_expiry(grant):
    root, issue, _, child_expiry = grant
    bundle, digest, env = issue()
    original = {name: (root / f'{name}-bundle.json').read_bytes() for name in ('comparison', 'total')}
    cap = math_activation.load_math_v2_capability(env, root=root, current_commit='candidate')
    assert bundle['schema'] == 'rag-activation-bundle-v6'
    assert cap and cap.contract == CONTRACT and cap.expires_at == child_expiry
    assert isinstance(cap.comparison, math_activation.MathV2Capability)
    assert isinstance(cap.total, total_activation.ScopeTotalCapability)
    assert cap.comparison.bundle_sha256 == cap.total.bundle_sha256 == digest
    assert cap.comparison.expires_at == cap.total.expires_at == cap.expires_at
    assert isinstance(cap, MathTemplatesCapability)
    assert activation.activation_status(env, root=root, current_commit='candidate').live_authorized
    assert all((root / f'{name}-bundle.json').read_bytes() == raw for name, raw in original.items())
    with pytest.raises(AttributeError):
        cap.contract = 'other'


@pytest.mark.parametrize('kind', ['comparison', 'total'])
@pytest.mark.parametrize('mutation', ['stale', 'commit', 'versions', 'recursive', 'extra_flags', 'signature'])
def test_parent_rejects_invalid_children(grant, kind, mutation):
    _, issue, change_child, _ = grant
    if mutation == 'stale':
        change_child(kind, ledger_changes={'expires_at': '2020-01-01T00:00:00Z'})
    else:
        changes = {'commit': {'source_commit': 'other'}, 'versions': {'versions': {}},
                   'recursive': {'schema': 'rag-activation-bundle-v6'},
                   'extra_flags': {'feature_flags': {}}, 'signature': {'release_signature': {}}}[mutation]
        change_child(kind, **changes)
    with pytest.raises(ValueError):
        issue()


@pytest.mark.parametrize('mutation', ['missing', 'individual', 'offline', 'unapproved', 'different_children'])
def test_individual_gates_do_not_replace_combination_evidence(grant, mutation):
    _, issue, _, _ = grant
    kwargs = {'missing': {'decision_changes': {'evidence': []}},
              'individual': {'gate_changes': {'schema': math_activation.AUTHORIZATION_SCHEMA}},
              'offline': {'gate_changes': {'offline_only': True}},
              'unapproved': {'decision_changes': {'decision': 'pending'}},
              'different_children': {'gate_changes': {'children': {}}}}[mutation]
    with pytest.raises(ValueError):
        issue(**kwargs)


@pytest.mark.parametrize('mutation', ['flags', 'commit', 'snapshot', 'signature', 'hash', 'child_tamper'])
def test_runtime_rechecks_complete_parent_authority(grant, mutation):
    root, issue, _, _ = grant
    bundle, _, env = issue()
    if mutation == 'signature':
        env['RAG_ACTIVATION_BUNDLE_SHA256'] = reference(root, 'pair-bundle.json', {**bundle, 'release_signature': {}})['sha256']
    elif mutation == 'child_tamper':
        (root / 'total-bundle.json').write_text('{}')
    else:
        field, value = {'flags': ('RAG_QUERY_DECOMPOSITION_ENABLED', 'true'),
                        'commit': ('RAG_DEPLOYMENT_GIT_SHA', 'other'),
                        'snapshot': ('RAG_SNAPSHOT_FINGERPRINT', 'f' * 64),
                        'hash': ('RAG_ACTIVATION_BUNDLE_SHA256', '0' * 64)}[mutation]
        env[field] = value
    assert math_activation.load_math_v2_capability(env, root=root, current_commit='candidate') is None
    assert not activation.activation_status(env, root=root, current_commit='candidate').live_authorized


@pytest.mark.parametrize('changes', [{'expires_at': '2020-01-01T00:00:00Z'}, {'children': {}},
    {'snapshot_fingerprint': 'a' * 64}, {'contract': 'math-only'}])
def test_signed_invalid_parent_contract_is_rejected(grant, changes):
    _, issue, _, _ = grant
    with pytest.raises(ValueError):
        issue(**changes)


@pytest.mark.parametrize('other_mode', ['math_v2', 'graph_v2', 'math_graph_v2', 'scope_total'])
def test_builder_modes_are_mutually_exclusive(tmp_path, other_mode):
    with pytest.raises(ValueError, match='exclusive'):
        build_activation_bundle(scope='controlled_demo', profile='selective', source_commit='candidate',
            decision_ledger='missing.json', output='unused.json', root=tmp_path,
            math_templates=True, **{other_mode: True})


def test_template_selector_preserves_direct_and_parent_child_authority(grant):
    root, issue, _, _ = grant
    _, _, env = issue()
    cap = math_activation.load_math_v2_capability(env, root=root, current_commit='candidate')
    for child, template in ((cap.comparison, 'compare_two_scopes'), (cap.total, 'sum_reviewed_scope')):
        assert select_math_capability(cap, template) == child
        assert select_math_capability(child, template) is child
    assert select_math_capability(cap.comparison, 'sum_reviewed_scope') is None
    assert select_math_capability(cap.total, 'compare_two_scopes') is None
    assert select_math_capability(cap, 'bom_rollup') is None
    assert select_math_capability({'comparison': cap.comparison}, 'compare_two_scopes') is None
    assert select_math_capability(None, 'sum_reviewed_scope') is None


def test_selector_rejects_wrong_parent_contract_or_child_type(grant):
    from dataclasses import replace
    root, issue, _, _ = grant
    _, _, env = issue()
    cap = math_activation.load_math_v2_capability(env, root=root, current_commit='candidate')
    assert select_math_capability(replace(cap, contract='other'), 'sum_reviewed_scope') is None
    assert select_math_capability(replace(cap, total=cap.comparison), 'sum_reviewed_scope') is None
