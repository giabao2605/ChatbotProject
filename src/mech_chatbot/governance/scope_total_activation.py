"""Signed authority for a total over one reviewed scope."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.governance.review_governance import review_governance_status


CONTRACT = 'grounded-math-scope-total-controlled-demo-v1'
LEDGER_SCHEMA = 'math-scope-total-controlled-demo-ledger-v1'
AUTHORIZATION_SCHEMA = 'grounded-math-scope-total-controlled-demo-authorization-v1'


@dataclass(frozen=True, slots=True)
class ScopeTotalCapability:
    contract: str
    template: str
    scope_pins: tuple[tuple[str, str, str], ...]
    source_commit: str
    bundle_sha256: str
    snapshot_fingerprint: str
    expires_at: str
    context: str = 'controlled_demo'
    review_mode: str = 'multi_reviewer'

    def permits_scopes(self, scopes):
        """Call after current approved scopes have been loaded with the actor's ACL."""
        return (datetime.now(timezone.utc) < _timestamp(self.expires_at)
                and len(scopes) == 1 and all(scope is not None for scope in scopes)
                and all(scope.authorized is True and scope.completeness_reviewed is True
                    and (scope.scope_id, scope.snapshot_id, reviewed_scope_fingerprint(scope)) in self.scope_pins
                    for scope in scopes))


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc)


def _pins(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError('One or more bounded scope pins required')
    pins = []
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'scope_id', 'snapshot_id', 'scope_hash'}
                or any(not isinstance(row[k], str) or not row[k].strip() or len(row[k]) > 255
                       for k in ('scope_id', 'snapshot_id'))
                or not isinstance(row['scope_hash'], str) or not re.fullmatch('[a-f0-9]{64}', row['scope_hash'])):
            raise ValueError('Exact reviewed scope pins required')
        pins.append((row['scope_id'], row['snapshot_id'], row['scope_hash']))
    if len({pin[0] for pin in pins}) != len(pins):
        raise ValueError('Unique scope identities required')
    return tuple(sorted(pins))


def validate_scope_total_bundle(bundle, *, root, environ, current_commit, bundle_sha256):
    """Keep one-scope authority separate from comparison and offline evidence."""
    from mech_chatbot.governance.feature_activation import feature_flags, feature_versions, release_signature_valid
    try:
        flags = feature_flags(environ)
        if not (isinstance(bundle, dict) and bundle.get('schema') == 'rag-activation-bundle-v5'
                and bundle.get('scope') == environ.get('RAG_ACTIVATION_SCOPE') == 'controlled_demo'
                and bundle.get('activation_profile') == environ.get('RAG_ACTIVATION_PROFILE') == 'selective'
                and bundle.get('source_commit') == current_commit == environ.get('RAG_DEPLOYMENT_GIT_SHA')
                and isinstance(current_commit, str) and bool(current_commit.strip())
                and bundle.get('feature_flags') == flags
                and {key for key, enabled in flags.items() if enabled} == {'RAG_GROUNDED_MATH_ENABLED'}
                and bundle.get('versions') == feature_versions(environ)
                and bundle.get('graph_fingerprint') is None
                and release_signature_valid(bundle, root=root)):
            return None
        ledger = load_json_reference(bundle.get('decision_ledger'), root=root)
        if not (ledger and ledger.get('schema') == LEDGER_SCHEMA and ledger.get('status') == 'complete'
                and ledger.get('scope') == 'controlled_demo' and ledger.get('source_commit') == current_commit
                and ledger.get('contract') == CONTRACT and ledger.get('template') == 'sum_reviewed_scope'
                and isinstance(ledger.get('snapshot_fingerprint'), str)
                and re.fullmatch('[a-f0-9]{64}', ledger['snapshot_fingerprint'])
                and ledger['snapshot_fingerprint'] == environ.get('RAG_SNAPSHOT_FINGERPRINT')
                and datetime.now(timezone.utc) < _timestamp(ledger['expires_at'])):
            return None
        pins = _pins(ledger.get('scope_pins'))
        governance_ref = bundle.get('review_governance')
        governance = load_json_reference(governance_ref, root=root) if governance_ref else None
        if governance_ref and governance is None:
            return None
        review = review_governance_status(governance, source_commit=current_commit, scope='controlled_demo')
        if not review.valid:
            return None
        decision = load_json_reference(ledger.get('decision'), root=root)
        if not (decision and decision.get('schema') == 'milestone-decision-v2'
                and decision.get('milestone') == 'grounded_math' and decision.get('scope') == 'controlled_demo'
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
                        and artifact.get('template') == 'sum_reviewed_scope'
                        and _pins(artifact.get('scope_pins')) == pins
                        and artifact.get('snapshot_fingerprint') == ledger['snapshot_fingerprint']
                        and artifact.get('review_mode') == review.mode and artifact.get('passed') is True
                        and artifact.get('production_eligible') is True and artifact.get('offline_only') is False
                        and artifact.get('evidence_kind') in {'staging', 'primary'}):
                    return None
                authorized = True
        if not authorized:
            return None
        return ScopeTotalCapability(CONTRACT, 'sum_reviewed_scope', pins, current_commit,
                                bundle_sha256, ledger['snapshot_fingerprint'], ledger['expires_at'],
                                review_mode=review.mode)
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None
