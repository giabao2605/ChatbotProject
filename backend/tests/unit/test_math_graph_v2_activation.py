"""Synthetic fixed Math/Graph combinations never stand in for release evidence."""
import base64
from datetime import datetime, timedelta, timezone
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mech_chatbot.governance import feature_activation as activation
from mech_chatbot.governance import math_v2_activation as math_activation
from mech_chatbot.governance import graph_v2_activation as graph_activation
from mech_chatbot.governance.math_graph_v2_activation import (
    AUTHORIZATION_SCHEMA, CONTRACT, LEDGER_SCHEMA, combined_snapshot_fingerprint,
    load_math_graph_v2_capability,
)
from mech_chatbot.domain.graph_query import GraphQuery, graph_fingerprint, graph_query_context
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
    for name, module in (('math', math_activation), ('graph', graph_activation)):
        if name == 'math':
            pins = [{'scope_id': 'A', 'snapshot_id': 's1', 'scope_hash': 'a' * 64},
                    {'scope_id': 'B', 'snapshot_id': 's2', 'scope_hash': 'b' * 64}]
            binding = {'scope_pins': pins, 'template': 'compare_two_scopes'}
            snapshot = 'c' * 64
        else:
            pins = [{'graph_context': graph_query_context(GraphQuery('document:1', 'product', 'factory')),
                     'graph_hash': graph_fingerprint({'contract_version': 'graph-v2', 'status': 'partial'})}]
            binding = {'graph_pins': pins}
            snapshot = graph_activation.graph_pins_fingerprint(pins)
        gate = {'schema': module.AUTHORIZATION_SCHEMA, 'source_commit': 'candidate',
                'scope': 'controlled_demo', 'contract': module.CONTRACT, **binding,
                'snapshot_fingerprint': snapshot, 'passed': True, 'production_eligible': True,
                'offline_only': False, 'evidence_kind': 'staging', 'review_mode': 'multi_reviewer'}
        decision = {'schema': 'milestone-decision-v2', 'milestone': 'grounded_math' if name == 'math' else 'graph_retrieval',
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
            root=tmp_path, release_signature=f'{name}-signature.txt', math_v2=name == 'math', graph_v2=name == 'graph',
            enabled_features={'RAG_GROUNDED_MATH_ENABLED' if name == 'math' else 'RAG_GRAPH_RETRIEVAL_ENABLED'})
        child_bundles[name] = bundle

    def issue(*, parent_flags=None, gate_changes=None, decision_changes=None,
              math_gate=None, graph_gate=None, **changes):
        for name, gate in (('math', math_gate), ('graph', graph_gate)):
            if gate is None:
                continue
            decision = json.loads((tmp_path / f'{name}-decision.json').read_text())
            decision = {**decision, 'evidence': [reference(tmp_path, f'{name}-gate.json', gate)]}
            binding = ({'scope_pins': gate['scope_pins'], 'template': gate['template']} if name == 'math'
                       else {'graph_pins': gate['graph_pins']})
            change_child(name, ledger_changes={**binding, 'snapshot_fingerprint': gate['snapshot_fingerprint'],
                'decision': reference(tmp_path, f'{name}-decision.json', decision)})
        children = {name: reference(tmp_path, f'{name}-bundle.json', child_bundles[name]) for name in ('math', 'graph')}
        snapshots = {name: json.loads((tmp_path / f'{name}-ledger.json').read_text())['snapshot_fingerprint']
                     for name in children}
        snapshot = combined_snapshot_fingerprint(snapshots['math'], snapshots['graph'])
        gate = {'schema': AUTHORIZATION_SCHEMA, 'source_commit': 'candidate', 'scope': 'controlled_demo',
                'contract': CONTRACT, 'children': children, 'snapshot_fingerprint': snapshot,
                'passed': True, 'production_eligible': True, 'offline_only': False,
                'evidence_kind': 'primary', 'review_mode': 'multi_reviewer', **(gate_changes or {})}
        decision = {'schema': 'milestone-decision-v2', 'milestone': 'math_graph', 'scope': 'controlled_demo',
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
            release_signature='pair-signature.txt', math_graph_v2=True,
            enabled_features={'RAG_GROUNDED_MATH_ENABLED', 'RAG_GRAPH_RETRIEVAL_ENABLED'} if parent_flags is None else parent_flags)
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


@pytest.fixture
def pair_grant(grant):
    """Reissue signed children and pair after binding these gate dicts to SQL pins."""
    root, base_issue, _, _ = grant
    math_gate = json.loads((root / 'math-gate.json').read_text())
    graph_gate = json.loads((root / 'graph-gate.json').read_text())

    def issue(**changes):
        bundle, digest, _ = base_issue(math_gate=math_gate, graph_gate=graph_gate, **changes)
        return bundle, digest

    def environment(bundle, digest):
        ledger = json.loads((root / 'pair-ledger.json').read_text())
        return {**{flag: str(value).lower() for flag, value in bundle['feature_flags'].items()}, **bundle['versions'],
                'RAG_ACTIVATION_SCOPE': 'controlled_demo', 'RAG_ACTIVATION_PROFILE': 'selective',
                'RAG_DEPLOYMENT_GIT_SHA': 'candidate', 'RAG_SNAPSHOT_FINGERPRINT': ledger['snapshot_fingerprint'],
                'RAG_ACTIVATION_BUNDLE_PATH': 'pair-bundle.json', 'RAG_ACTIVATION_BUNDLE_SHA256': digest}
    return root, math_gate, graph_gate, issue, environment


def test_pair_fixture_reissues_child_source_bindings_before_combination(pair_grant):
    root, math_gate, graph_gate, issue, environment = pair_grant
    math_gate['scope_pins'] = [{**pin, 'snapshot_id': 'real-sql-snapshot'} for pin in math_gate['scope_pins']]
    math_gate['snapshot_fingerprint'] = 'f' * 64
    graph_gate['graph_pins'] = [{'graph_context': graph_query_context(GraphQuery('document:2', 'product', 'factory')),
                                'graph_hash': 'e' * 64}]
    graph_gate['snapshot_fingerprint'] = graph_activation.graph_pins_fingerprint(graph_gate['graph_pins'])
    bundle, digest = issue()
    cap = load_math_graph_v2_capability(environment(bundle, digest), root=root, current_commit='candidate')
    assert cap.math.scope_pins[0][1] == 'real-sql-snapshot'
    assert cap.graph.permits_query(GraphQuery('document:2', 'product', 'factory'))


def test_pair_returns_existing_child_types_bound_to_parent_transport_and_shortest_expiry(grant):
    root, issue, _, child_expiry = grant
    bundle, digest, env = issue()
    original = {name: (root / f'{name}-bundle.json').read_bytes() for name in ('math', 'graph')}
    cap = load_math_graph_v2_capability(env, root=root, current_commit='candidate')
    assert bundle['schema'] == 'rag-activation-bundle-v4'
    assert cap and cap.contract == CONTRACT and cap.expires_at == child_expiry
    assert isinstance(cap.math, math_activation.MathV2Capability)
    assert isinstance(cap.graph, graph_activation.GraphV2Capability)
    assert cap.math.bundle_sha256 == cap.graph.bundle_sha256 == digest
    assert cap.math.expires_at == cap.graph.expires_at == cap.expires_at
    assert math_activation.load_math_v2_capability(env, root=root, current_commit='candidate') == cap.math
    assert graph_activation.load_graph_v2_capability(env, root=root, current_commit='candidate') == cap.graph
    assert activation.activation_status(env, root=root, current_commit='candidate').live_authorized
    assert all((root / f'{name}-bundle.json').read_bytes() == raw for name, raw in original.items())
    with pytest.raises(AttributeError):
        cap.contract = 'other'


@pytest.mark.parametrize('kind', ['math', 'graph'])
@pytest.mark.parametrize('mutation', ['stale', 'commit', 'versions', 'recursive', 'extra_flags', 'signature'])
def test_parent_rejects_invalid_children(grant, kind, mutation):
    _, issue, change_child, _ = grant
    if mutation == 'stale':
        change_child(kind, ledger_changes={'expires_at': '2020-01-01T00:00:00Z'})
    else:
        changes = {'commit': {'source_commit': 'other'}, 'versions': {'versions': {}},
                   'recursive': {'schema': 'rag-activation-bundle-v4'},
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
        (root / 'graph-bundle.json').write_text('{}')
    else:
        field, value = {'flags': ('RAG_QUERY_DECOMPOSITION_ENABLED', 'true'),
                        'commit': ('RAG_DEPLOYMENT_GIT_SHA', 'other'),
                        'snapshot': ('RAG_SNAPSHOT_FINGERPRINT', 'f' * 64),
                        'hash': ('RAG_ACTIVATION_BUNDLE_SHA256', '0' * 64)}[mutation]
        env[field] = value
    assert load_math_graph_v2_capability(env, root=root, current_commit='candidate') is None
    assert math_activation.load_math_v2_capability(env, root=root, current_commit='candidate') is None
    assert graph_activation.load_graph_v2_capability(env, root=root, current_commit='candidate') is None


@pytest.mark.parametrize('changes', [{'expires_at': '2020-01-01T00:00:00Z'}, {'children': {}},
    {'snapshot_fingerprint': 'a' * 64}, {'contract': 'math-only'}])
def test_signed_invalid_parent_contract_is_rejected(grant, changes):
    _, issue, _, _ = grant
    with pytest.raises(ValueError):
        issue(**changes)


@pytest.mark.parametrize('other_mode', ['math_v2', 'graph_v2'])
def test_builder_modes_are_mutually_exclusive(tmp_path, other_mode):
    with pytest.raises(ValueError, match='exclusive'):
        build_activation_bundle(scope='controlled_demo', profile='selective', source_commit='candidate',
            decision_ledger='missing.json', output='unused.json', root=tmp_path,
            math_graph_v2=True, **{other_mode: True})
