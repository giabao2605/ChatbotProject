"""Shared deterministic metric helpers for evaluation reports."""

import math


def nearest_rank(values, ratio):
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio + 0.999999) - 1))
    return ordered[index]


def ranked_retrieval_metrics(retrieved, relevant, cutoffs=(5, 10)):
    """Return binary-relevance recall and nDCG at the requested cutoffs."""
    ranked = [str(item).strip().lower() for item in (retrieved or [])]
    expected = {str(item).strip().lower() for item in (relevant or []) if str(item).strip()}
    result = {}
    for cutoff in cutoffs:
        k = max(1, int(cutoff))
        top = ranked[:k]
        hits = [1 if item in expected else 0 for item in top]
        recall = len({item for item in top if item in expected}) / len(expected) if expected else 0.0
        dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
        ideal_hits = min(len(expected), k)
        idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
        result[f"recall_at_{k}"] = recall
        result[f"ndcg_at_{k}"] = dcg / idcg if idcg else 0.0
    return result
