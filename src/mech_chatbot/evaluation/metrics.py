"""Shared deterministic metric helpers for evaluation reports."""

import math


def nearest_rank(values, ratio):
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio + 0.999999) - 1))
    return ordered[index]


def canonical_source_identity(value):
    """Normalize document provenance without collapsing page or version identity."""
    if isinstance(value, dict):
        identity = {
            "document": value.get("document") or value.get("file_goc") or "",
            "doc_id": value.get("doc_id"),
            "page": value.get("page", value.get("trang", value.get("trang_so"))),
            "version": value.get("version", value.get("version_no")),
            "source_id": value.get("source_id") or "",
        }
    else:
        identity = {
            "document": value,
            "doc_id": None,
            "page": None,
            "version": None,
            "source_id": "",
        }
    return {
        field: str(raw).strip().casefold() if raw not in (None, "") else ""
        for field, raw in identity.items()
    }


def _normalized_rank_inputs(retrieved, relevant):
    ranked = [canonical_source_identity(item) for item in (retrieved or [])]
    expected = [
        canonical_source_identity(item)
        for item in (relevant or [])
        if canonical_source_identity(item)["document"]
    ]
    return ranked, expected


def _identity_matches(actual, expected):
    return all(
        not expected[field] or actual[field] == expected[field]
        for field in ("document", "doc_id", "page", "version", "source_id")
    )


def _rank_hits(ranked, expected):
    unmatched = set(range(len(expected)))
    hits = []
    for actual in ranked:
        match = next(
            (index for index in sorted(unmatched) if _identity_matches(actual, expected[index])),
            None,
        )
        hits.append(match is not None)
        if match is not None:
            unmatched.remove(match)
    return hits


def ranked_retrieval_metrics(retrieved, relevant, cutoffs=(5, 10, 20)):
    """Return binary-relevance recall/nDCG and reciprocal rank."""
    ranked, expected = _normalized_rank_inputs(retrieved, relevant)
    result = {}
    for cutoff in cutoffs:
        k = max(1, int(cutoff))
        hits = _rank_hits(ranked[:k], expected)
        recall = sum(hits) / len(expected) if expected else 0.0
        dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
        ideal_hits = min(len(expected), k)
        idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
        result[f"recall_at_{k}"] = recall
        result[f"ndcg_at_{k}"] = dcg / idcg if idcg else 0.0
    first_relevant_rank = next(
        (index for index, hit in enumerate(_rank_hits(ranked, expected), 1) if hit), None
    )
    result["mrr"] = 1.0 / first_relevant_rank if first_relevant_rank else 0.0
    return result


def ranked_retrieval_audit(retrieved, relevant):
    """Keep the normalized rank list and relevance identity for case audits."""
    ranked, expected = _normalized_rank_inputs(retrieved, relevant)
    hits = _rank_hits(ranked, expected)
    return [
        {
            "rank": index,
            "source": identity["document"],
            "identity": identity,
            "relevant": hits[index - 1],
        }
        for index, identity in enumerate(ranked, 1)
    ]
