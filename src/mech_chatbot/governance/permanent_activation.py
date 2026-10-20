"""Signed permanent feature availability; source review and actor ACL stay mandatory."""
from datetime import datetime, timezone
import re

from mech_chatbot.evaluation.department_readiness import CANONICAL_DEPARTMENTS
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.governance.review_governance import review_governance_status


LEDGER_SCHEMA = 'permanent-feature-authorization-v1'
ENABLED_FLAGS = frozenset({'RAG_GROUNDED_MATH_ENABLED', 'RAG_GRAPH_RETRIEVAL_ENABLED',
                           'RAG_QUERY_DECOMPOSITION_ENABLED'})


def capability_active(cap):
    """Only explicit permanent reviewed-source capabilities may omit an expiry."""
    try:
        if cap.expires_at is None:
            return cap.context == 'default_rollout' and cap.reviewed_sources is True
        expiry = datetime.fromisoformat(cap.expires_at.replace('Z', '+00:00'))
        return expiry.tzinfo is not None and datetime.now(timezone.utc) < expiry
    except (AttributeError, TypeError, ValueError):
        return False


def validate_permanent_bundle(bundle, *, root, environ, current_commit, bundle_sha256):
    """Bind explicit owner authorization to exact code, settings and current reviewed sources."""
    from mech_chatbot.governance.feature_activation import feature_flags, feature_versions, release_signature_valid
    from mech_chatbot.governance.math_v2_activation import MathV2Capability, CONTRACT as COMPARISON_CONTRACT
    from mech_chatbot.governance.scope_total_activation import ScopeTotalCapability, CONTRACT as TOTAL_CONTRACT
    from mech_chatbot.governance.math_templates_activation import MathTemplatesCapability
    from mech_chatbot.governance.graph_v2_activation import GraphV2Capability, CONTRACT as GRAPH_CONTRACT
    from mech_chatbot.governance.query_v2_activation import QueryV2Capability
    try:
        flags = feature_flags(environ)
        versions = feature_versions(environ)
        if not (isinstance(bundle, dict) and bundle.get('schema') == 'rag-activation-bundle-v8'
                and bundle.get('scope') == environ.get('RAG_ACTIVATION_SCOPE') == 'default_rollout'
                and bundle.get('activation_profile') == environ.get('RAG_ACTIVATION_PROFILE') == 'selective'
                and isinstance(current_commit, str) and re.fullmatch('[a-f0-9]{40}', current_commit)
                and bundle.get('source_commit') == environ.get('RAG_DEPLOYMENT_GIT_SHA') == current_commit
                and bundle.get('feature_flags') == flags and {key for key, value in flags.items() if value} == ENABLED_FLAGS
                and bundle.get('versions') == versions and bundle.get('graph_fingerprint') is None
                and isinstance(bundle_sha256, str) and re.fullmatch('[a-f0-9]{64}', bundle_sha256)
                and release_signature_valid(bundle, root=root)):
            return None
        ledger = load_json_reference(bundle.get('decision_ledger'), root=root)
        if not (ledger and ledger.get('schema') == LEDGER_SCHEMA and ledger.get('status') == 'authorized'
                and ledger.get('lifetime') == 'permanent' and 'expires_at' in ledger and ledger['expires_at'] is None
                and ledger.get('source_policy') == 'reviewed_current_acl' and ledger.get('business_accepted') is False
                and isinstance(ledger.get('departments'), list)
                and len(ledger['departments']) == len(CANONICAL_DEPARTMENTS)
                and set(ledger['departments']) == set(CANONICAL_DEPARTMENTS)
                and all(ledger.get(field) == bundle.get(field) for field in
                        ('scope', 'activation_profile', 'source_commit', 'feature_flags', 'versions'))
                and ledger.get('review_governance') == bundle.get('review_governance')):
            return None
        snapshot = ledger.get('snapshot_fingerprint')
        if not (isinstance(snapshot, str) and re.fullmatch('[a-f0-9]{64}', snapshot)
                and snapshot == environ.get('RAG_SNAPSHOT_FINGERPRINT')):
            return None
        governance = load_json_reference(ledger.get('review_governance'), root=root)
        review = review_governance_status(governance, source_commit=current_commit, scope='default_rollout')
        owner = ledger.get('owner_authorization')
        if not (governance and review.valid and review.mode == 'single_owner' and isinstance(owner, dict)
                and owner.get('owner') == review.owner and isinstance(owner.get('instruction'), str)
                and owner['instruction'].strip() and isinstance(owner.get('authorized_at'), str)
                and datetime.fromisoformat(owner['authorized_at'].replace('Z', '+00:00')).tzinfo is not None):
            return None
        common = {'source_commit': current_commit, 'bundle_sha256': bundle_sha256,
                  'snapshot_fingerprint': snapshot, 'expires_at': None, 'context': 'default_rollout',
                  'review_mode': review.mode, 'reviewed_sources': True}
        comparison = MathV2Capability(contract=COMPARISON_CONTRACT, template='compare_two_scopes', scope_pins=(), **common)
        total = ScopeTotalCapability(contract=TOTAL_CONTRACT, template='sum_reviewed_scope', scope_pins=(), **common)
        math = MathTemplatesCapability(comparison=comparison, total=total, **common)
        graph = GraphV2Capability(contract=GRAPH_CONTRACT, graph_pins=(), **common)
        return QueryV2Capability(math=math, graph=graph, **common)
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None
