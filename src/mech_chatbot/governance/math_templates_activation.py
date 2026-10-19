"""Signed coexistence of the reviewed comparison and one-scope total."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re

from mech_chatbot.domain.graph_query import graph_fingerprint
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.governance.math_v2_activation import MathV2Capability, validate_math_v2_bundle
from mech_chatbot.governance.scope_total_activation import ScopeTotalCapability, validate_scope_total_bundle
from mech_chatbot.governance.review_governance import review_governance_status


CONTRACT = 'math-reviewed-templates-controlled-demo-v1'
LEDGER_SCHEMA = 'math-reviewed-templates-controlled-demo-ledger-v1'
AUTHORIZATION_SCHEMA = 'math-reviewed-templates-controlled-demo-authorization-v1'


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc)


def combined_snapshot_fingerprint(comparison_snapshot, total_snapshot):
    if any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
           for value in (comparison_snapshot, total_snapshot)):
        raise ValueError('Exact child snapshot fingerprints required')
    return graph_fingerprint({'comparison': comparison_snapshot, 'total': total_snapshot})


@dataclass(frozen=True, slots=True)
class MathTemplatesCapability:
    comparison: MathV2Capability
    total: ScopeTotalCapability
    source_commit: str
    bundle_sha256: str
    snapshot_fingerprint: str
    expires_at: str
    contract: str = CONTRACT
    context: str = 'controlled_demo'
    review_mode: str = 'multi_reviewer'


def _child(reference, name, parent, environ, *, root, current_commit):
    child = load_json_reference(reference, root=root)
    schema, validate = (('rag-activation-bundle-v2', validate_math_v2_bundle) if name == 'comparison'
                        else ('rag-activation-bundle-v5', validate_scope_total_bundle))
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


def validate_math_templates_bundle(bundle, *, root, environ, current_commit, bundle_sha256):
    """Validate both unchanged child contracts and an independent combination decision."""
    from mech_chatbot.governance.feature_activation import feature_flags, feature_versions, release_signature_valid
    try:
        flags = feature_flags(environ)
        if not (isinstance(bundle, dict) and bundle.get('schema') == 'rag-activation-bundle-v6'
                and bundle.get('scope') == environ.get('RAG_ACTIVATION_SCOPE') == 'controlled_demo'
                and bundle.get('activation_profile') == environ.get('RAG_ACTIVATION_PROFILE') == 'selective'
                and bundle.get('source_commit') == current_commit == environ.get('RAG_DEPLOYMENT_GIT_SHA')
                and isinstance(current_commit, str) and bool(current_commit.strip())
                and bundle.get('feature_flags') == flags
                and {key for key, enabled in flags.items() if enabled}
                    == {'RAG_GROUNDED_MATH_ENABLED'}
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
        if not isinstance(children, dict) or set(children) != {'comparison', 'total'}:
            return None
        comparison = _child(children['comparison'], 'comparison', bundle, environ, root=root, current_commit=current_commit)
        total = _child(children['total'], 'total', bundle, environ, root=root, current_commit=current_commit)
        if comparison is None or total is None:
            return None
        snapshot = combined_snapshot_fingerprint(comparison.snapshot_fingerprint, total.snapshot_fingerprint)
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
                and decision.get('milestone') == 'math_templates' and decision.get('scope') == 'controlled_demo'
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
        expiry = min(_timestamp(value) for value in (ledger['expires_at'], comparison.expires_at, total.expires_at)).isoformat()
        return MathTemplatesCapability(replace(comparison, bundle_sha256=bundle_sha256, expires_at=expiry),
            replace(total, bundle_sha256=bundle_sha256, expires_at=expiry), current_commit,
            bundle_sha256, snapshot, expiry, review_mode=review.mode)
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None


def select_math_capability(grant, template):
    """Select one fixed authority while preserving each child contract and pins."""
    if type(grant) is MathTemplatesCapability:
        if grant.contract != CONTRACT or grant.context != 'controlled_demo':
            return None
        grant = (grant.comparison if template == 'compare_two_scopes' else
                 grant.total if template == 'sum_reviewed_scope' else None)
    expected = {'compare_two_scopes': MathV2Capability, 'sum_reviewed_scope': ScopeTotalCapability}.get(template)
    return grant if expected is not None and type(grant) is expected and grant.template == template else None
