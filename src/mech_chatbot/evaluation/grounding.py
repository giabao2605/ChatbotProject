"""Deterministic claim and citation evaluators for labeled RAG runs."""

from __future__ import annotations

import re
import unicodedata

from mech_chatbot.rag.number_normalization import normalize_numbers_in_text


_CITATION_PATTERN = re.compile(r"\[(?:Nguồn|Source)\s*:[^\]]+\]", re.IGNORECASE)
_SOURCE_ID_PATTERN = re.compile(
    r"\bSourceID\s*[:#]?\s*([A-Za-z0-9_-]+)", re.IGNORECASE
)


def _normalize_text(value) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalize_numbers_in_text(normalized).split())


def _source_ids(value) -> set[str]:
    if isinstance(value, str):
        values = [value]
    else:
        values = value or []
    return {str(item).strip().upper() for item in values if str(item).strip()}


def extract_claims(answer: str) -> list[dict]:
    """Split answer text into auditable claims and attach local SourceIDs."""
    claims: list[dict] = []
    for raw_line in str(answer or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        citations = _CITATION_PATTERN.findall(line)
        source_ids = {
            match.group(1).upper()
            for citation in citations
            for match in _SOURCE_ID_PATTERN.finditer(citation)
        }
        claim_text = _CITATION_PATTERN.sub("", line).strip(" -*\t")
        if not claim_text:
            if claims and source_ids:
                claims[-1]["source_ids"] = sorted(
                    set(claims[-1]["source_ids"]) | source_ids
                )
            continue
        parts = [
            part.strip(" -*\t")
            for part in re.split(r"(?<=[.!?])\s+", claim_text)
            if part.strip(" -*\t")
        ]
        for index, part in enumerate(parts):
            claims.append(
                {
                    "text": part,
                    "source_ids": sorted(source_ids) if index == len(parts) - 1 else [],
                }
            )
    return claims


def _expected_terms(expected: dict) -> list[str]:
    terms = expected.get("required_terms")
    if terms is None and expected.get("text"):
        terms = [expected["text"]]
    return [_normalize_text(term) for term in (terms or []) if _normalize_text(term)]


def _term_present(term: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text))


def evaluate_claims(
    actual_claims: list[dict],
    expected_claims: list[dict],
    *,
    accessible_source_ids: set[str] | list[str] | tuple[str, ...],
) -> dict:
    """Measure label precision, expected-claim recall and source faithfulness."""
    if not expected_claims:
        return {
            "schema": "claim-evaluation-v1",
            "applicable": False,
            "claim_precision": None,
            "expected_claim_recall": None,
            "faithfulness": None,
            "matched_claim_count": 0,
            "matched_expected_claim_count": 0,
            "faithful_claim_count": 0,
            "violations": [],
        }

    accessible = _source_ids(accessible_source_ids)
    matched_expected: set[int] = set()
    matched_actual = 0
    faithful_actual = 0
    violations: list[dict] = []

    for claim_index, claim in enumerate(actual_claims, 1):
        normalized_claim = _normalize_text(claim.get("text"))
        expected_index = next(
            (
                index
                for index, expected in enumerate(expected_claims)
                if index not in matched_expected
                and _expected_terms(expected)
                and all(
                    _term_present(term, normalized_claim)
                    for term in _expected_terms(expected)
                )
            ),
            None,
        )
        if expected_index is None:
            violations.append({"claim_index": claim_index, "reason": "unexpected_claim"})
            continue

        matched_actual += 1
        matched_expected.add(expected_index)
        expected = expected_claims[expected_index]
        actual_sources = _source_ids(claim.get("source_ids"))
        allowed_sources = _source_ids(expected.get("allowed_source_ids"))
        if not actual_sources:
            violations.append({"claim_index": claim_index, "reason": "missing_source"})
        elif not actual_sources <= accessible:
            violations.append({"claim_index": claim_index, "reason": "inaccessible_source"})
        elif allowed_sources and not (actual_sources & allowed_sources):
            violations.append({"claim_index": claim_index, "reason": "unsupported_source"})
        else:
            faithful_actual += 1

    denominator = len(actual_claims)
    return {
        "schema": "claim-evaluation-v1",
        "applicable": True,
        "claim_count": denominator,
        "expected_claim_count": len(expected_claims),
        "matched_claim_count": matched_actual,
        "matched_expected_claim_count": len(matched_expected),
        "faithful_claim_count": faithful_actual,
        "claim_precision": matched_actual / denominator if denominator else 0.0,
        "expected_claim_recall": len(matched_expected) / len(expected_claims),
        "faithfulness": faithful_actual / denominator if denominator else 0.0,
        "violations": violations,
    }


def _actual_identity(value: dict) -> dict:
    return {
        "document": str(value.get("document") or value.get("file_goc") or "").strip(),
        "doc_id": value.get("doc_id"),
        "page": value.get("page", value.get("trang", value.get("trang_so"))),
        "version": value.get("version", value.get("version_no")),
        "source_id": str(value.get("source_id") or "").strip().upper(),
    }


def _rendered_segments(rendered_text: str) -> list[str]:
    raw = str(rendered_text or "")
    bracketed = _CITATION_PATTERN.findall(raw)
    remainder = _CITATION_PATTERN.sub("\n", raw)
    return bracketed + [
        part.strip()
        for part in re.split(r"[;\r\n]+", remainder)
        if part.strip()
    ]


def _identity_present_in_segment(segment: str, expected: dict) -> bool:
    rendered = _normalize_text(segment)
    document = _normalize_text(expected.get("document"))
    page = str(expected.get("page") or "").strip()
    version = str(expected.get("version") or "").strip()
    source_id = _normalize_text(expected.get("source_id"))
    document_present = not document or bool(
        re.search(rf"(?<!\w){re.escape(document)}(?!\w)", rendered)
    )
    page_present = not page or bool(
        re.search(rf"\b(?:trang|page)\s*[:#]?\s*{re.escape(page)}(?!\d)", rendered)
    )
    version_present = not version or bool(
        re.search(
            rf"(?:\b(?:version|phiên bản)\s*[:#]?\s*{re.escape(version)}(?!\d)"
            rf"|(?<![a-z0-9])v\s*{re.escape(version)}(?!\d))",
            rendered,
        )
    )
    source_id_present = not source_id or bool(
        re.search(rf"(?<![\w-]){re.escape(source_id)}(?![\w-])", rendered)
    )
    return (
        document_present
        and page_present
        and version_present
        and source_id_present
    )


def _rendered_identity_present(rendered_text: str, expected: dict) -> bool:
    return any(
        _identity_present_in_segment(segment, expected)
        for segment in _rendered_segments(rendered_text)
    )


def select_rendered_citations(candidates: list[dict], rendered_text: str) -> list[dict]:
    """Select cited sources from the full retrieval registry."""
    rendered = _normalize_text(rendered_text)
    selected = []
    for candidate in candidates:
        identity = _actual_identity(candidate)
        source_id = _normalize_text(identity["source_id"])
        document = _normalize_text(identity["document"])
        page = str(identity["page"] or "").strip()
        version = str(identity["version"] or "").strip()
        source_id_present = bool(
            source_id
            and re.search(rf"(?<![\w-]){re.escape(source_id)}(?![\w-])", rendered)
        )
        rendered_reference_present = _rendered_identity_present(
            rendered_text,
            {
                "document": document,
                "page": page,
                "version": version,
                "source_id": "",
            },
        )
        if source_id_present or rendered_reference_present:
            selected.append(candidate)
    return selected


def evaluate_citations(
    actual_citations: list[dict],
    expected_citations: list[dict],
    *,
    accessible_source_ids: set[str] | list[str] | tuple[str, ...],
    rendered_text: str,
) -> dict:
    """Validate structured and rendered citation identity fail-closed."""
    if not expected_citations:
        return {
            "schema": "citation-evaluation-v1",
            "applicable": False,
            "citation_accuracy": None,
            "citation_precision": None,
            "valid_expected_citation_count": 0,
            "valid_actual_citation_count": 0,
            "violations": [],
        }

    actual = [_actual_identity(item) for item in actual_citations]
    accessible = _source_ids(accessible_source_ids)
    valid_expected = 0
    valid_actual_indexes: set[int] = set()
    used_actual_indexes: set[int] = set()
    violations: list[dict] = []

    for citation_index, expected_raw in enumerate(expected_citations, 1):
        expected = _actual_identity(expected_raw)
        candidates = [
            index
            for index, item in enumerate(actual)
            if index not in used_actual_indexes
            and _normalize_text(item["document"]) == _normalize_text(expected["document"])
        ]
        match_index = min(
            candidates,
            key=lambda index: sum(
                actual[index][field] != expected[field]
                for field in ("doc_id", "page", "version", "source_id")
            ),
            default=None,
        )
        if match_index is None:
            violations.append({"citation_index": citation_index, "reason": "missing_citation"})
            continue
        item = actual[match_index]
        used_actual_indexes.add(match_index)
        before = len(violations)
        if item["page"] != expected["page"]:
            violations.append({"citation_index": citation_index, "reason": "wrong_page"})
        if item["doc_id"] != expected["doc_id"]:
            violations.append({"citation_index": citation_index, "reason": "wrong_doc_id"})
        if item["version"] != expected["version"]:
            violations.append({"citation_index": citation_index, "reason": "wrong_version"})
        if expected["source_id"] and item["source_id"] != expected["source_id"]:
            violations.append({"citation_index": citation_index, "reason": "wrong_source_id"})
        if not item["source_id"] or item["source_id"] not in accessible:
            violations.append({"citation_index": citation_index, "reason": "inaccessible_source"})
        if not _rendered_identity_present(rendered_text, expected):
            violations.append(
                {"citation_index": citation_index, "reason": "rendered_citation_mismatch"}
            )
        if len(violations) == before:
            valid_expected += 1
            valid_actual_indexes.add(match_index)

    return {
        "schema": "citation-evaluation-v1",
        "applicable": True,
        "expected_citation_count": len(expected_citations),
        "actual_citation_count": len(actual),
        "valid_expected_citation_count": valid_expected,
        "valid_actual_citation_count": len(valid_actual_indexes),
        "citation_accuracy": valid_expected / len(expected_citations),
        "citation_precision": (
            len(valid_actual_indexes) / len(actual) if actual else 0.0
        ),
        "violations": violations,
    }
