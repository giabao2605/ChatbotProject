"""Deterministic two-reviewer plus tie-break adjudication protocol."""

from __future__ import annotations

import re

from mech_chatbot.evaluation.outcomes import VALID_OUTCOMES
from mech_chatbot.evaluation.schema import validate_frozen_actor
from mech_chatbot.evaluation.grounded_math_v2 import _json_matches
from mech_chatbot.governance.artifact_references import load_json_reference

_RUN_BINDINGS = ('source_commit','snapshot_fingerprint','config_hash','gate_hash','eval_hash')
_CASE_BINDINGS = (*_RUN_BINDINGS,'run_id','case_id','department','dataset_sha256')


def _validate_review(review: dict) -> None:
    if not str(review.get("reviewer_id") or "").strip():
        raise ValueError("reviewer_id is required")
    if review.get("role") not in {"reviewer", "adjudicator"}:
        raise ValueError("role must be reviewer or adjudicator")
    if review.get("outcome_label") not in VALID_OUTCOMES:
        raise ValueError("invalid outcome_label")
    if not isinstance(review.get("answer_correct"), bool):
        raise ValueError("answer_correct must be boolean")
    if not isinstance(review.get("citation_correct"), bool):
        raise ValueError("citation_correct must be boolean")
    if not str(review.get("reason_code") or "").strip():
        raise ValueError("reason_code is required")


def _label(review: dict) -> tuple:
    return (
        review["outcome_label"],
        review["answer_correct"],
        review["citation_correct"],
    )


def resolve_case_reviews(case_id: str, reviews: list[dict]) -> dict:
    """Resolve two independent labels, requiring a third reviewer on disagreement."""
    if not str(case_id or "").strip():
        raise ValueError("case_id is required")
    for review in reviews:
        _validate_review(review)
    reviewer_ids = [str(review["reviewer_id"]) for review in reviews]
    if len(set(reviewer_ids)) != len(reviewer_ids):
        raise ValueError("reviewers must be unique")

    independent = [review for review in reviews if review["role"] == "reviewer"]
    adjudicators = [review for review in reviews if review["role"] == "adjudicator"]
    if len(independent) != 2:
        raise ValueError("exactly two independent reviewers are required")
    disagreement = _label(independent[0]) != _label(independent[1])
    if disagreement:
        if len(adjudicators) != 1:
            raise ValueError("a third adjudicator is required when labels disagree")
        resolved = adjudicators[0]
        resolved_by = str(resolved["reviewer_id"])
    else:
        if adjudicators:
            raise ValueError("adjudicator is only allowed when labels disagree")
        resolved = independent[0]
        resolved_by = "consensus"

    return {
        "schema": "evaluation-adjudication-v1",
        "case_id": str(case_id),
        "disagreement": disagreement,
        "resolved_by": resolved_by,
        "reviewer_ids": reviewer_ids,
        "outcome_label": resolved["outcome_label"],
        "answer_correct": resolved["answer_correct"],
        "citation_correct": resolved["citation_correct"],
        "reason_codes": [str(review["reason_code"]) for review in reviews],
    }


def _bound_observation(case_reference, observation_reference, root):
    case = load_json_reference(case_reference, root=root)
    observed = load_json_reference(observation_reference, root=root)
    if (not isinstance(case, dict) or case.get('schema') != 'fixed-template-case-v2'
            or not isinstance(observed, dict) or observed.get('schema') != 'fixed-template-case-observation-v2'
            or observed.get('case_sha256') != case_reference['sha256']
            or observed.get('accepted') is not False or observed.get('offline_only') is not True
            or observed.get('binding_verification') != 'pending'
            or not isinstance(observed.get('actual_outcome'), str) or observed['actual_outcome'] not in VALID_OUTCOMES
            or any(not isinstance(case.get(f), str) or not re.fullmatch(
                '[0-9a-f]{40}' if f == 'source_commit' else '[0-9a-f]{64}', case[f])
                   for f in (*_RUN_BINDINGS,'dataset_sha256'))
            or any(not isinstance(case.get(f), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',case[f])
                   for f in ('run_id','case_id','department'))
            or not isinstance(case.get('actor'), dict)
            or not _json_matches({f:case[f] for f in (*_CASE_BINDINGS,'actor')},
                                 {f:observed.get(f) for f in (*_CASE_BINDINGS,'actor')})):
        raise ValueError('An exact pending case observation is required')
    validate_frozen_actor(case['actor'])
    return observed


def _bound_review_identity(review, department, root):
    fields = {'reviewer_id','role','outcome_label','answer_correct','citation_correct',
              'reason_code','leaked','wrong_version','identity_reference'}
    if (not isinstance(review, dict) or set(review) != fields
            or any(not isinstance(review[f], str) or not review[f].strip() for f in ('reviewer_id','reason_code'))
            or review['role'] not in ('reviewer','adjudicator')
            or not isinstance(review['outcome_label'], str) or review['outcome_label'] not in VALID_OUTCOMES
            or any(type(review[f]) is not bool for f in ('answer_correct','citation_correct','leaked','wrong_version'))):
        raise ValueError('Typed independent review labels are required')
    identity = load_json_reference(review['identity_reference'], root=root)
    if (not isinstance(identity, dict) or identity.get('schema') != 'business-identity-v1'
            or identity.get('active') is not True or identity.get('synthetic') is not False
            or identity.get('identity') != review['reviewer_id'] or identity.get('department') != department):
        raise ValueError('A pinned active business reviewer identity is required')


def resolve_observation_reviews(case_reference, observation_reference, reviews_reference, *, root):
    """Join supplied independent labels; never authenticate or approve a run."""
    observed = _bound_observation(case_reference, observation_reference, root)
    artifact = load_json_reference(reviews_reference, root=root)
    fields = {'schema', *_CASE_BINDINGS, 'case_sha256','observation_sha256','reviews'}
    if (not isinstance(artifact, dict) or set(artifact) != fields
            or artifact['schema'] != 'fixed-template-case-reviews-v2'
            or not _json_matches({f:observed[f] for f in _CASE_BINDINGS},
                                 {f:artifact[f] for f in _CASE_BINDINGS})
            or artifact['case_sha256'] != case_reference['sha256']
            or artifact['observation_sha256'] != observation_reference['sha256']
            or not isinstance(artifact['reviews'], list) or not 2 <= len(artifact['reviews']) <= 3):
        raise ValueError('Reviews must bind the exact case and observation')
    reviews = artifact['reviews']
    for review in reviews:
        _bound_review_identity(review, observed['department'], root)
    resolved = resolve_case_reviews(observed['case_id'], reviews)
    risks = {(r['leaked'],r['wrong_version']) for r in reviews}
    return {'schema':'evaluation-adjudication-v2', **{f:observed[f] for f in _CASE_BINDINGS},
            'case_sha256':case_reference['sha256'],'observation_sha256':observation_reference['sha256'],
            'reviews_sha256':reviews_reference['sha256'], 'resolution':resolved,
            'leaked':any(r['leaked'] for r in reviews),'wrong_version':any(r['wrong_version'] for r in reviews),
            'risk_disagreement':len(risks) > 1, 'observed_outcome':observed.get('actual_outcome'),
            'binding_verification':'pending','accepted':False,'offline_only':True}
