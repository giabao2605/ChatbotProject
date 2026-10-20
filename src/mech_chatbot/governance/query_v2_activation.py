"""Signed bounded Query actions composed from existing Math and Graph authority."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re

from mech_chatbot.domain.graph_query import graph_fingerprint
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
from mech_chatbot.governance.math_templates_activation import MathTemplatesCapability, validate_math_templates_bundle
from mech_chatbot.governance.graph_v2_activation import GraphV2Capability, validate_graph_v2_bundle
from mech_chatbot.governance.review_governance import review_governance_status


CONTRACT = 'query-reviewed-actions-controlled-demo-v1'
LEDGER_SCHEMA = 'query-v2-controlled-demo-ledger-v1'
AUTHORIZATION_SCHEMA = 'query-v2-controlled-demo-authorization-v1'


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
class QueryV2Capability:
    math: MathTemplatesCapability
    graph: GraphV2Capability
    source_commit: str
    bundle_sha256: str
    snapshot_fingerprint: str
    expires_at: str
    contract: str = CONTRACT
    context: str = 'controlled_demo'
    review_mode: str = 'multi_reviewer'


    def permits_plan(self, plan):
        """Authorize bounded actions; current source ACL and hashes remain child checks."""
        try:
            if (select_query_capability(self) is None or type(plan) is not QueryPlan
                    or datetime.now(timezone.utc) >= _timestamp(self.expires_at)):
                return False
            # Revalidate frozen descriptors too, since callers may bypass constructors.
            QueryPlan(tuple(QueryBranch(**{field: getattr(branch, field)
                for field in QueryBranch.__dataclass_fields__}) for branch in plan.branches))
            total_scopes = {pin[0] for pin in self.math.total.scope_pins}
            comparison_scopes = {pin[0] for pin in self.math.comparison.scope_pins}
            for branch in plan.branches:
                if branch.scope_total is not None and branch.scope_total not in total_scopes:
                    return False
                if branch.scope_comparison is not None and (len(set(branch.scope_comparison)) != 2
                        or not set(branch.scope_comparison) <= comparison_scopes):
                    return False
                graph = branch.graph_query or (branch.bom_rollup.graph if branch.bom_rollup else None)
                if graph is not None and not self.graph.permits_query(graph):
                    return False
            return True
        except (AttributeError, TypeError, ValueError):
            return False


def _child(reference, name, parent, environ, *, root, current_commit):
    child = load_json_reference(reference, root=root)
    schema, validate = (('rag-activation-bundle-v6', validate_math_templates_bundle) if name == 'math'
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


def validate_query_v2_bundle(bundle, *, root, environ, current_commit, bundle_sha256):
    """Validate both unchanged child contracts and an independent combination decision."""
    from mech_chatbot.governance.feature_activation import feature_flags, feature_versions, release_signature_valid
    try:
        flags = feature_flags(environ)
        if not (isinstance(bundle, dict) and bundle.get('schema') == 'rag-activation-bundle-v7'
                and bundle.get('scope') == environ.get('RAG_ACTIVATION_SCOPE') == 'controlled_demo'
                and bundle.get('activation_profile') == environ.get('RAG_ACTIVATION_PROFILE') == 'selective'
                and bundle.get('source_commit') == current_commit == environ.get('RAG_DEPLOYMENT_GIT_SHA')
                and isinstance(current_commit, str) and bool(current_commit.strip())
                and bundle.get('feature_flags') == flags
                and {key for key, enabled in flags.items() if enabled}
                    == {'RAG_GROUNDED_MATH_ENABLED', 'RAG_GRAPH_RETRIEVAL_ENABLED', 'RAG_QUERY_DECOMPOSITION_ENABLED'}
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
                and decision.get('milestone') == 'query_decomposition' and decision.get('scope') == 'controlled_demo'
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
        math = replace(math, bundle_sha256=bundle_sha256, expires_at=expiry,
            comparison=replace(math.comparison, bundle_sha256=bundle_sha256, expires_at=expiry),
            total=replace(math.total, bundle_sha256=bundle_sha256, expires_at=expiry))
        return QueryV2Capability(math, replace(graph, bundle_sha256=bundle_sha256, expires_at=expiry), current_commit,
            bundle_sha256, snapshot, expiry, review_mode=review.mode)
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None


def select_query_capability(grant):
    """Recognize only the reviewed Query contract, never a duck-typed fixture."""
    return grant if (type(grant) is QueryV2Capability and grant.contract == CONTRACT
                     and grant.context == 'controlled_demo') else None
