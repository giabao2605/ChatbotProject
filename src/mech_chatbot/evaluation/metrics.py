"""Shared deterministic metric helpers for evaluation reports."""


def nearest_rank(values, ratio):
    ordered = sorted(values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, int(len(ordered) * ratio + 0.999999) - 1))
    return ordered[index]
