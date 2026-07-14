"""Versioned contracts for manifests and evaluation artifacts."""

from __future__ import annotations

CURRENT_MANIFEST_SCHEMA = "rag-eval-manifest-v2"
LEGACY_MANIFEST_SCHEMA = "rag-eval-manifest-v1-legacy"
SUPPORTED_MANIFEST_SCHEMAS = {CURRENT_MANIFEST_SCHEMA, LEGACY_MANIFEST_SCHEMA}
EVALUATION_REPORT_SCHEMA = "rag-labeled-eval-v4"
EVALUATOR_VERSION = "evaluation-foundation-v1"
EVALUATOR_MODELS = {
    "retrieval": "binary-relevance-v2",
    "claims": "deterministic-labeled-claims-v1",
    "citations": "structured-source-identity-v1",
    "risk_coverage": "explicit-operating-points-v1",
}


def version_manifest_case(case: dict) -> str:
    schema = case.get("manifest_schema") or LEGACY_MANIFEST_SCHEMA
    if schema not in SUPPORTED_MANIFEST_SCHEMAS:
        raise ValueError(f"unsupported manifest_schema: {schema}")
    case["manifest_schema"] = schema
    return schema


def validate_manifest_ground_truth(case: dict, *, expected_outcome: str) -> None:
    """Validate v2 human-authored claim/citation labels; legacy stays readable."""
    if case.get("manifest_schema") != CURRENT_MANIFEST_SCHEMA:
        return
    for field in ("expected_claims", "expected_citations"):
        if not isinstance(case.get(field), list):
            raise ValueError(f"{field} must be a list for {CURRENT_MANIFEST_SCHEMA}")
    if expected_outcome in {"full_answer", "partial_answer"}:
        if not case["expected_claims"]:
            raise ValueError("expected_claims must be non-empty for answer outcomes")
        if not case["expected_citations"]:
            raise ValueError("expected_citations must be non-empty for answer outcomes")
    for claim in case["expected_claims"]:
        if not isinstance(claim, dict) or not str(claim.get("id") or "").strip():
            raise ValueError("each expected_claim must have an id")
        if not isinstance(claim.get("required_terms"), list) or not claim["required_terms"]:
            raise ValueError("each expected_claim must have required_terms")
        if not isinstance(claim.get("allowed_source_ids"), list) or not claim["allowed_source_ids"]:
            raise ValueError("each expected_claim must have allowed_source_ids")
    for citation in case["expected_citations"]:
        if not isinstance(citation, dict):
            raise ValueError("each expected_citation must be an object")
        required = ("document", "doc_id", "page", "version", "source_id")
        if any(citation.get(field) in (None, "") for field in required):
            raise ValueError(
                "each expected_citation requires document/doc_id/page/version/source_id"
            )
