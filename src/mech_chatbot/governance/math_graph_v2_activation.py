"""Signed authority for the fixed Math v2 comparison and Graph v2 pair."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

from mech_chatbot.domain.graph_query import graph_fingerprint
from mech_chatbot.governance.artifact_references import load_json_reference, read_json_object, resolve_path
from mech_chatbot.governance.math_v2_activation import MathV2Capability, validate_math_v2_bundle
from mech_chatbot.governance.graph_v2_activation import GraphV2Capability, validate_graph_v2_bundle
from mech_chatbot.governance.review_governance import review_governance_status


CONTRACT = 'math-graph-v2-controlled-demo-v1'
LEDGER_SCHEMA = 'math-graph-v2-controlled-demo-ledger-v1'
AUTHORIZATION_SCHEMA = 'math-graph-v2-controlled-demo-authorization-v1'


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc)


def combined_snapshot_fingerprint(math_snapshot, graph_snapshot):
    if any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
           for value in (math_snapshot, graph_snapshot)):
        raise ValueError('Exact child snapshot fingerprints required')
    return graph_fingerprint({'math': math_snapshot, 'graph': graph_snapshot})


@dataclass(frozen=True, slots=True)
class MathGraphV2Capability:
    math: MathV2Capability
    graph: GraphV2Capability
    source_commit: str
    bundle_sha256: str
    snapshot_fingerprint: str
    expires_at: str
    contract: str = CONTRACT
    context: str = 'controlled_demo'
    review_mode: str = 'multi_reviewer'


def _child(reference, name, parent, environ, *, root, current_commit):
    child = load_json_reference(reference, root=root)
    schema, validate = (('rag-activation-bundle-v2', validate_math_v2_bundle) if name == 'math'
                        else ('rag-activation-bundle-v3', validate_graph_v2_bundle))
    if not (child and child.get('schema') == schema
            and child.get('source_commit') == current_commit
            and child.get('versions') == parent.get('versions')
            and child.get('scope') == parent.get('scope')
            and child.get('activation_profile') == parent.get('activation_profile')):
        return None
    ledger = load_json_reference(child.get('decision_ledger'), root=root)
    if ledger is None:
        return None
    child_env = {**environ, **{key: str(value).lower() for key, value in child['feature_flags'].items()},
                 'RAG_SNAPSHOT_FINGERPRINT': ledger.get('snapshot_fingerprint')}
    return validate(child, root=root, environ=child_env, current_commit=current_commit,
                    bundle_sha256=reference['sha256'])


def validate_math_graph_v2_bundle(bundle, *, root, environ, current_commit, bundle_sha256):
    """Validate both unchanged child contracts and an independent combination decision."""
    from mech_chatbot.governance.feature_activation import feature_flags, feature_versions, release_signature_valid
    try:
        flags = feature_flags(environ)
        if not (isinstance(bundle, dict) and bundle.get('schema') == 'rag-activation-bundle-v4'
                and bundle.get('scope') == environ.get('RAG_ACTIVATION_SCOPE') == 'controlled_demo'
                and bundle.get('activation_profile') == environ.get('RAG_ACTIVATION_PROFILE') == 'selective'
                and bundle.get('source_commit') == current_commit == environ.get('RAG_DEPLOYMENT_GIT_SHA')
                and isinstance(current_commit, str) and bool(current_commit.strip())
                and bundle.get('feature_flags') == flags
                and {key for key, enabled in flags.items() if enabled}
                    == {'RAG_GROUNDED_MATH_ENABLED', 'RAG_GRAPH_RETRIEVAL_ENABLED'}
                and bundle.get('versions') == feature_versions(environ)
                and bundle.get('graph_fingerprint') is None
                and release_signature_valid(bundle, root=root)):
            return None
        ledger = load_json_reference(bundle.get('decision_ledger'), root=root)
        if not (ledger and ledger.get('schema') == LEDGER_SCHEMA and ledger.get('status') == 'complete'
                and ledger.get('scope') == 'controlled_demo' and ledger.get('source_commit') == current_commit
                and ledger.get('contract') == CONTRACT
                and datetime.now(timezone.utc) < _timestamp(ledger['expires_at'])):
            return None
        children = ledger.get('children')
        if not isinstance(children, dict) or set(children) != {'math', 'graph'}:
            return None
        math = _child(children['math'], 'math', bundle, environ, root=root, current_commit=current_commit)
        graph = _child(children['graph'], 'graph', bundle, environ, root=root, current_commit=current_commit)
        if math is None or graph is None:
            return None
        snapshot = combined_snapshot_fingerprint(math.snapshot_fingerprint, graph.snapshot_fingerprint)
        if not (ledger.get('snapshot_fingerprint') == environ.get('RAG_SNAPSHOT_FINGERPRINT') == snapshot):
            return None
        governance_ref = bundle.get('review_governance')
        governance = load_json_reference(governance_ref, root=root) if governance_ref else None
        if governance_ref and governance is None:
            return None
        review = review_governance_status(governance, source_commit=current_commit, scope='controlled_demo')
        if not review.valid:
            return None
        decision = load_json_reference(ledger.get('decision'), root=root)
        if not (decision and decision.get('schema') == 'milestone-decision-v2'
                and decision.get('milestone') == 'math_graph' and decision.get('scope') == 'controlled_demo'
                and decision.get('decision') == 'accepted' and decision.get('source_commit') == current_commit
                and str(decision.get('reason') or '').strip()):
            return None
        signoff = decision.get('reviewer_signoff')
        if not (isinstance(signoff, dict) and str(signoff.get('reviewer') or '').strip()
                and str(signoff.get('signed_at') or '').strip()):
            return None
        evidence = decision.get('evidence')
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 20:
            return None
        authorized = False
        for reference in evidence:
            artifact = load_json_reference(reference, root=root)
            if not artifact or artifact.get('source_commit') != current_commit:
                return None
            if artifact.get('schema') == AUTHORIZATION_SCHEMA:
                if not (artifact.get('scope') == 'controlled_demo' and artifact.get('contract') == CONTRACT
                        and artifact.get('children') == children and artifact.get('snapshot_fingerprint') == snapshot
                        and artifact.get('review_mode') == review.mode and artifact.get('passed') is True
                        and artifact.get('production_eligible') is True and artifact.get('offline_only') is False
                        and artifact.get('evidence_kind') in {'staging', 'primary'}):
                    return None
                authorized = True
        if not authorized:
            return None
        expiry = min(_timestamp(value) for value in (ledger['expires_at'], math.expires_at, graph.expires_at)).isoformat()
        return MathGraphV2Capability(replace(math, bundle_sha256=bundle_sha256, expires_at=expiry),
            replace(graph, bundle_sha256=bundle_sha256, expires_at=expiry), current_commit,
            bundle_sha256, snapshot, expiry, review_mode=review.mode)
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None


def load_math_graph_v2_capability(environ, *, root='.', current_commit=None):
    """Read the immutable parent bundle without following recursive combinations."""
    try:
        path = str(environ.get('RAG_ACTIVATION_BUNDLE_PATH') or '').strip()
        digest = str(environ.get('RAG_ACTIVATION_BUNDLE_SHA256') or '')
        if not path or not re.fullmatch('[a-f0-9]{64}', digest):
            return None
        bundle, raw = read_json_object(resolve_path(path, Path(root)))
        if raw is None or hashlib.sha256(raw).hexdigest() != digest:
            return None
        return validate_math_graph_v2_bundle(bundle, root=root, environ=environ,
                                             current_commit=current_commit, bundle_sha256=digest)
    except (TypeError, ValueError, OSError):
        return None
