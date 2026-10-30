"""Temporary fixture signatures exercise authority, never business acceptance."""
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mech_chatbot.domain.graph_query import GraphQuery, BomRollupQuery, graph_query_context, graph_fingerprint
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
from mech_chatbot.governance import feature_activation as activation
from mech_chatbot.governance import math_v2_activation as comparison
from mech_chatbot.governance import scope_total_activation as total
from mech_chatbot.governance import math_templates_activation as math
from mech_chatbot.governance import graph_v2_activation as graph
from mech_chatbot.governance import query_v2_activation as query
from scripts.ops.build_activation_bundle import build_activation_bundle
from .test_math_v2_activation import reference

FLAGS = {'RAG_GROUNDED_MATH_ENABLED', 'RAG_GRAPH_RETRIEVAL_ENABLED', 'RAG_QUERY_DECOMPOSITION_ENABLED'}


@pytest.fixture
def grant(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setattr(activation, '_release_authority_public_key', lambda *_: public)
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    graph_query = GraphQuery('document:1', 'assembly', 'factory')
    graph_value = {'contract_version': 'graph-v2', 'status': 'partial'}
    graph_pins = [{'graph_context': graph_query_context(graph_query), 'graph_hash': graph_fingerprint(graph_value)}]
    snapshots = {}
    bundles = {}

    def issue(name, module, binding, flags, mode, milestone, *, changes=None, gate_changes=None, **kwargs):
        snapshot = snapshots[name]
        gate = {'schema': module.AUTHORIZATION_SCHEMA, 'source_commit': 'candidate', 'scope': 'controlled_demo',
                'contract': module.CONTRACT, **binding, 'snapshot_fingerprint': snapshot,
                'passed': True, 'production_eligible': True, 'offline_only': False,
                'evidence_kind': 'staging', 'review_mode': 'multi_reviewer', **(gate_changes or {})}
        decision = {'schema': 'milestone-decision-v2', 'milestone': milestone, 'scope': 'controlled_demo',
                    'decision': 'accepted', 'source_commit': 'candidate', 'reason': 'Synthetic fixture only',
                    'evidence': [reference(tmp_path, f'{name}-gate.json', gate)],
                    'reviewer_signoff': {'reviewer': 'fixture', 'signed_at': datetime.now(timezone.utc).isoformat()}}
        ledger = {'schema': module.LEDGER_SCHEMA, 'status': 'complete', 'scope': 'controlled_demo',
                  'source_commit': 'candidate', 'contract': module.CONTRACT, **binding,
                  'snapshot_fingerprint': snapshot, 'expires_at': expires,
                  'decision': reference(tmp_path, f'{name}-decision.json', decision), **(changes or {})}
        reference(tmp_path, f'{name}-ledger.json', ledger)
        (tmp_path / f'{name}-signature.txt').write_text(base64.b64encode(key.sign((tmp_path / f'{name}-ledger.json').read_bytes())).decode())
        bundle, digest = build_activation_bundle(scope='controlled_demo', profile='selective', source_commit='candidate',
            decision_ledger=f'{name}-ledger.json', output=f'{name}-bundle.json', root=tmp_path,
            release_signature=f'{name}-signature.txt', enabled_features=flags, **{mode: True}, **kwargs)
        return bundle, digest

    for name, module, count, mode, template in [('comparison', comparison, 2, 'math_v2', 'compare_two_scopes'),
                                               ('total', total, 1, 'scope_total', 'sum_reviewed_scope')]:
        snapshots[name] = ('a' if count == 2 else 'b') * 64
        pins = [{'scope_id': scope, 'snapshot_id': 'snapshot', 'scope_hash': 'c' * 64} for scope in ('A', 'B')[:count]]
        bundles[name], _ = issue(name, module, {'scope_pins': pins, 'template': template},
                                 {'RAG_GROUNDED_MATH_ENABLED'}, mode, 'grounded_math')
    snapshots['math'] = math.combined_snapshot_fingerprint(snapshots['comparison'], snapshots['total'])
    math_children = {name: reference(tmp_path, f'{name}-bundle.json', bundles[name]) for name in ('comparison', 'total')}
    bundles['math'], _ = issue('math', math, {'children': math_children}, {'RAG_GROUNDED_MATH_ENABLED'}, 'math_templates', 'math_templates')
    snapshots['graph'] = graph.graph_pins_fingerprint(graph_pins)
    bundles['graph'], _ = issue('graph', graph, {'graph_pins': graph_pins}, {'RAG_GRAPH_RETRIEVAL_ENABLED'}, 'graph_v2', 'graph_retrieval')
    snapshots['query'] = query.combined_snapshot_fingerprint(snapshots['math'], snapshots['graph'])

    def parent(*, flags=FLAGS, changes=None, gate_changes=None):
        children = {name: reference(tmp_path, f'{name}-bundle.json', bundles[name]) for name in ('math', 'graph')}
        bundle, digest = issue('query', query, {'children': children}, flags, 'query_v2', 'query_decomposition',
                               changes=changes, gate_changes=gate_changes)
        env = {**{flag: str(value).lower() for flag, value in bundle['feature_flags'].items()}, **bundle['versions'],
               'RAG_ACTIVATION_SCOPE': 'controlled_demo', 'RAG_ACTIVATION_PROFILE': 'selective',
               'RAG_DEPLOYMENT_GIT_SHA': 'candidate', 'RAG_SNAPSHOT_FINGERPRINT': snapshots['query'],
               'RAG_ACTIVATION_BUNDLE_PATH': 'query-bundle.json', 'RAG_ACTIVATION_BUNDLE_SHA256': digest}
        return bundle, digest, env
    return tmp_path, parent, bundles, expires, graph_query, graph_value


def test_signed_parent_selects_all_existing_child_contracts_and_parent_identity(grant):
    root, issue, _, expiry, graph_query, graph_value = grant
    bundle, digest, env = issue()
    cap = comparison.load_math_v2_capability(env, root=root, current_commit='candidate')
    assert type(cap) is query.QueryV2Capability and bundle['schema'] == 'rag-activation-bundle-v7'
    assert cap.expires_at == expiry and cap.bundle_sha256 == digest
    assert activation.activation_status(env, root=root, current_commit='candidate').live_authorized
    assert query.select_query_capability(cap) is cap
    assert math.select_math_capability(cap, 'sum_reviewed_scope').bundle_sha256 == digest
    assert math.select_math_capability(cap, 'compare_two_scopes').bundle_sha256 == digest
    selected_graph = graph.load_graph_v2_capability(env, root=root, current_commit='candidate')
    assert selected_graph == cap.graph and selected_graph.bundle_sha256 == digest
    assert selected_graph.permits_result(graph_query, graph_value)
    assert not selected_graph.permits_result(graph_query, {**graph_value, 'status': 'complete'})
    plans = [QueryPlan((QueryBranch('a', 'sum', scope_total='A'), QueryBranch('b', 'compare', scope_comparison=('A', 'B')))),
             QueryPlan((QueryBranch('a', 'graph', graph_query=graph_query),)),
             QueryPlan((QueryBranch('a', 'bom', bom_rollup=BomRollupQuery(graph_query, '10', 'rounding')),)),
             QueryPlan((QueryBranch('a', 'text', text_query=True),))]
    assert all(cap.permits_plan(plan) for plan in plans)
    assert not cap.permits_plan(QueryPlan((QueryBranch('a', 'sum', scope_total='unknown'),)))
    assert not cap.permits_plan(QueryPlan((QueryBranch('a', 'compare', scope_comparison=('A', 'A')),)))
    assert not cap.permits_plan(QueryPlan((QueryBranch('a', 'graph', graph_query=replace(graph_query, site='other')),)))
    assert not cap.permits_plan({'branches': []})
    assert not replace(cap, expires_at='2020-01-01T00:00:00Z').permits_plan(plans[0])
    assert query.select_query_capability(replace(cap, contract='other')) is None
    assert query.select_query_capability(cap.math) is None


@pytest.mark.parametrize('kind', ['math', 'graph'])
@pytest.mark.parametrize('mutation', ['schema', 'commit', 'versions', 'signature', 'child_hash'])
def test_parent_never_promotes_invalid_or_mutated_children(grant, kind, mutation):
    root, issue, bundles, *_ = grant
    changes = {'schema': {'schema': 'rag-activation-bundle-v7'}, 'commit': {'source_commit': 'other'},
               'versions': {'versions': {}}, 'signature': {'release_signature': {}},
               'child_hash': {'decision_ledger': {}}}[mutation]
    bundles[kind] = {**bundles[kind], **changes}
    with pytest.raises(ValueError):
        issue()


@pytest.mark.parametrize('changes', [{'expires_at': '2020-01-01T00:00:00Z'}, {'children': {}},
                                   {'contract': 'other'}, {'snapshot_fingerprint': 'd' * 64}])
def test_parent_contract_is_independently_signed_and_validated(grant, changes):
    _, issue, *_ = grant
    with pytest.raises(ValueError):
        issue(changes=changes)


def test_parent_refuses_offline_evidence_extra_flags_and_changed_children_after_issue(grant):
    root, issue, *_ = grant
    with pytest.raises(ValueError):
        issue(gate_changes={'offline_only': True})
    with pytest.raises(ValueError):
        issue(flags=FLAGS | {'RAG_CRAG_ENABLED'})
    _, _, env = issue()
    (root / 'graph-ledger.json').write_text('{}')
    assert comparison.load_math_v2_capability(env, root=root, current_commit='candidate') is None
    assert graph.load_graph_v2_capability(env, root=root, current_commit='candidate') is None
    assert not activation.activation_status(env, root=root, current_commit='candidate').live_authorized

@pytest.mark.parametrize('mutation', ['hash', 'signature', 'commit', 'flags', 'snapshot', 'ledger'])
def test_configured_query_grant_rechecked_by_all_loaders(grant, mutation):
    root, issue, *_ = grant
    bundle, _, env = issue()
    if mutation == 'signature':
        env['RAG_ACTIVATION_BUNDLE_SHA256'] = reference(root, 'query-bundle.json', {**bundle, 'release_signature': {}})['sha256']
    elif mutation == 'ledger':
        (root / 'query-ledger.json').write_text('{}')
    else:
        field, value = {'hash': ('RAG_ACTIVATION_BUNDLE_SHA256', '0' * 64),
                        'commit': ('RAG_DEPLOYMENT_GIT_SHA', 'other'),
                        'flags': ('RAG_GRAPH_RETRIEVAL_ENABLED', 'false'),
                        'snapshot': ('RAG_SNAPSHOT_FINGERPRINT', 'e' * 64)}[mutation]
        env[field] = value
    assert comparison.load_math_v2_capability(env, root=root, current_commit='candidate') is None
    assert graph.load_graph_v2_capability(env, root=root, current_commit='candidate') is None
    assert not activation.activation_status(env, root=root, current_commit='candidate').live_authorized


def test_parent_shorter_expiry_reaches_all_child_templates(grant):
    root, issue, *_ = grant
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    _, _, env = issue(changes={'expires_at': expiry})
    cap = comparison.load_math_v2_capability(env, root=root, current_commit='candidate')
    assert cap.expires_at == cap.math.expires_at == cap.graph.expires_at == expiry
    assert cap.math.total.expires_at == cap.math.comparison.expires_at == expiry


def test_query_builder_does_not_allow_multiple_modes(tmp_path):
    with pytest.raises(ValueError, match='exclusive'):
        build_activation_bundle(scope='controlled_demo', profile='selective', source_commit='candidate',
            decision_ledger='missing.json', output='unused.json', root=tmp_path,
            query_v2=True, math_templates=True)
