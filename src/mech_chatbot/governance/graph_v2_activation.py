"""Signed authority for exact ACL-visible Graph v2 query results."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from mech_chatbot.domain.graph_query import GraphQuery, graph_fingerprint, graph_query_context
from mech_chatbot.governance.artifact_references import load_json_reference, read_json_object, resolve_path
from mech_chatbot.governance.review_governance import review_governance_status


CONTRACT = 'graph-v2-reviewed-query-controlled-demo-v1'
LEDGER_SCHEMA = 'graph-v2-controlled-demo-ledger-v1'
AUTHORIZATION_SCHEMA = 'graph-v2-controlled-demo-authorization-v1'


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc)


def _context(query):
    if not isinstance(query, GraphQuery):
        raise ValueError('Validated Graph query required')
    return json.dumps(graph_query_context(query), sort_keys=True, separators=(',', ':'))


def _pins(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError('Bounded reviewed Graph pins required')
    pins = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'graph_context', 'graph_hash'}
                or not isinstance(row['graph_context'], dict)
                or not isinstance(row['graph_hash'], str)
                or not re.fullmatch('[a-f0-9]{64}', row['graph_hash'])):
            raise ValueError('Exact Graph context and result hash required')
        query = GraphQuery(**row['graph_context'])
        if row['graph_context'] != graph_query_context(query):
            raise ValueError('Canonical Graph context required')
        pins.append((_context(query), row['graph_hash']))
    if len(set(pins)) != len(pins):
        raise ValueError('Unique Graph pins required')
    return tuple(sorted(pins))


def graph_pins_fingerprint(pins):
    """Bind the sorted approved query/result variants, not a privileged tree."""
    return graph_fingerprint([{'graph_context': json.loads(context), 'graph_hash': digest}
                              for context, digest in _pins(pins)])


@dataclass(frozen=True, slots=True)
class GraphV2Capability:
    contract: str
    graph_pins: tuple[tuple[str, str], ...]
    source_commit: str
    bundle_sha256: str
    snapshot_fingerprint: str
    expires_at: str
    context: str = 'controlled_demo'
    review_mode: str = 'multi_reviewer'

    def permits_query(self, query):
        try:
            return (datetime.now(timezone.utc) < _timestamp(self.expires_at)
                    and any(context == _context(query) for context, _ in self.graph_pins))
        except (TypeError, ValueError, AttributeError):
            return False

    def permits_result(self, query, result):
        """Call after current reviewed relations have been loaded with actor ACL."""
        return (self.permits_query(query) and isinstance(result, dict)
                and result.get('contract_version') == 'graph-v2'
                and result.get('status') in {'complete', 'partial', 'unknown'}
                and (_context(query), graph_fingerprint(result)) in self.graph_pins)


def validate_graph_v2_bundle(bundle, *, root, environ, current_commit, bundle_sha256):
    """Accept only the separately signed Graph-only contract; never promote v1/v2."""
    from mech_chatbot.governance.feature_activation import feature_flags, feature_versions, release_signature_valid
    try:
        flags = feature_flags(environ)
        if not (isinstance(bundle, dict) and bundle.get('schema') == 'rag-activation-bundle-v3'
                and bundle.get('scope') == environ.get('RAG_ACTIVATION_SCOPE') == 'controlled_demo'
                and bundle.get('activation_profile') == environ.get('RAG_ACTIVATION_PROFILE') == 'selective'
                and bundle.get('source_commit') == current_commit == environ.get('RAG_DEPLOYMENT_GIT_SHA')
                and isinstance(current_commit, str) and bool(current_commit.strip())
                and bundle.get('feature_flags') == flags
                and {key for key, enabled in flags.items() if enabled} == {'RAG_GRAPH_RETRIEVAL_ENABLED'}
                and bundle.get('versions') == feature_versions(environ)
                and bundle.get('graph_fingerprint') is None
                and release_signature_valid(bundle, root=root)):
            return None
        ledger = load_json_reference(bundle.get('decision_ledger'), root=root)
        if not (ledger and ledger.get('schema') == LEDGER_SCHEMA and ledger.get('status') == 'complete'
                and ledger.get('scope') == 'controlled_demo' and ledger.get('source_commit') == current_commit
                and ledger.get('contract') == CONTRACT
                and isinstance(ledger.get('snapshot_fingerprint'), str)
                and re.fullmatch('[a-f0-9]{64}', ledger['snapshot_fingerprint'])
                and ledger['snapshot_fingerprint'] == environ.get('RAG_SNAPSHOT_FINGERPRINT')
                and ledger['snapshot_fingerprint'] == graph_pins_fingerprint(ledger.get('graph_pins'))
                and datetime.now(timezone.utc) < _timestamp(ledger['expires_at'])):
            return None
        pins = _pins(ledger.get('graph_pins'))
        governance_ref = bundle.get('review_governance')
        governance = load_json_reference(governance_ref, root=root) if governance_ref else None
        if governance_ref and governance is None:
            return None
        review = review_governance_status(governance, source_commit=current_commit, scope='controlled_demo')
        if not review.valid:
            return None
        decision = load_json_reference(ledger.get('decision'), root=root)
        if not (decision and decision.get('schema') == 'milestone-decision-v2'
                and decision.get('milestone') == 'graph_retrieval' and decision.get('scope') == 'controlled_demo'
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
                        and _pins(artifact.get('graph_pins')) == pins
                        and artifact.get('snapshot_fingerprint') == ledger['snapshot_fingerprint']
                        and artifact.get('review_mode') == review.mode and artifact.get('passed') is True
                        and artifact.get('production_eligible') is True and artifact.get('offline_only') is False
                        and artifact.get('evidence_kind') in {'staging', 'primary'}):
                    return None
                authorized = True
        if not authorized:
            return None
        return GraphV2Capability(CONTRACT, pins, current_commit, bundle_sha256,
                                 ledger['snapshot_fingerprint'], ledger['expires_at'], review_mode=review.mode)
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None


def load_graph_v2_capability(environ, *, root='.', current_commit=None):
    """Recheck the configured immutable bundle before granting Graph access."""
    try:
        path = str(environ.get('RAG_ACTIVATION_BUNDLE_PATH') or '').strip()
        digest = str(environ.get('RAG_ACTIVATION_BUNDLE_SHA256') or '')
        if not path or not re.fullmatch('[a-f0-9]{64}', digest):
            return None
        bundle, raw = read_json_object(resolve_path(path, Path(root)))
        if raw is None or hashlib.sha256(raw).hexdigest() != digest:
            return None
        if bundle.get('schema') == 'rag-activation-bundle-v7':
            from mech_chatbot.governance.query_v2_activation import validate_query_v2_bundle
            query = validate_query_v2_bundle(bundle, root=root, environ=environ,
                                             current_commit=current_commit, bundle_sha256=digest)
            return query.graph if query else None
        if bundle.get('schema') == 'rag-activation-bundle-v4':
            from mech_chatbot.governance.math_graph_v2_activation import validate_math_graph_v2_bundle
            pair = validate_math_graph_v2_bundle(bundle, root=root, environ=environ,
                                                 current_commit=current_commit, bundle_sha256=digest)
            return pair.graph if pair else None
        return validate_graph_v2_bundle(bundle, root=root, environ=environ,
                                       current_commit=current_commit, bundle_sha256=digest)
    except (TypeError, ValueError, OSError):
        return None


def graph_v2_bundle_selected(environ, *, root='.'):
    """A broken selected grant must not enable legacy unpinned enrichment."""
    path = str(environ.get('RAG_ACTIVATION_BUNDLE_PATH') or '').strip()
    if not path:
        return False
    try:
        bundle, _ = read_json_object(resolve_path(path, root))
    except (TypeError, ValueError, OSError):
        return True
    return bundle is None or bundle.get('schema') not in {'rag-activation-bundle-v1', 'rag-activation-bundle-v2'}


def graph_v2_authorizer_for_settings(settings):
    """Capture settings and recheck local commit and signed authority per request."""
    from mech_chatbot.governance.feature_activation import current_git_commit, FEATURE_FLAGS, VERSION_FIELDS
    keys = (*FEATURE_FLAGS, *VERSION_FIELDS, 'RAG_ACTIVATION_SCOPE', 'RAG_ACTIVATION_PROFILE',
            'RAG_ACTIVATION_BUNDLE_PATH', 'RAG_ACTIVATION_BUNDLE_SHA256',
            'RAG_DEPLOYMENT_GIT_SHA', 'RAG_SNAPSHOT_FINGERPRINT')
    env = {key: getattr(settings, key, None) for key in keys}
    root = Path(__file__).resolve().parents[3]
    return lambda: load_graph_v2_capability(env, root=root, current_commit=current_git_commit(root))
