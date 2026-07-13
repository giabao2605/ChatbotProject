"""Bounded query decomposition that preserves one immutable access context."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass


_COMPLEX_CUES = (" và ", " đồng thời ", " so sánh ", " đối chiếu ", " versus ", " vs ")
_CODE_RE = re.compile(r"\b[A-Z]{1,10}[-_][A-Z0-9][A-Z0-9._-]*\b", re.IGNORECASE)


@dataclass(frozen=True)
class DecompositionPlan:
    original_query: str
    is_complex: bool
    subqueries: tuple[str, ...] = ()
    planner_version: str = "planner-v1"


class CorrectionBudget:
    def __init__(self, limit=1):
        self._remaining = max(0, int(limit))
        self._lock = threading.Lock()

    def claim(self) -> bool:
        with self._lock:
            if self._remaining <= 0:
                return False
            self._remaining -= 1
            return True

def is_complex_query(question: str) -> bool:
    normalized = " " + str(question or "").strip().lower() + " "
    cue_count = sum(cue in normalized for cue in _COMPLEX_CUES)
    return cue_count > 0 or normalized.count("?") > 1


def codes_in_query(question: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code.lower() for code in _CODE_RE.findall(str(question or ""))))


def build_plan(question: str, planner=None, *, planner_version="planner-v1") -> DecompositionPlan:
    original = str(question or "").strip()
    if not is_complex_query(original):
        return DecompositionPlan(original, False, (), planner_version)
    if planner is None:
        return DecompositionPlan(original, True, (original,), planner_version)
    try:
        payload = planner(original) or {}
    except Exception:
        payload = {}
    proposed = payload.get("subqueries", ()) if isinstance(payload, dict) else ()
    allowed_codes = {code.upper() for code in _CODE_RE.findall(original)}
    accepted = []
    seen = set()
    for item in proposed:
        query = str(item or "").strip()
        if not query:
            continue
        query_codes = {code.upper() for code in _CODE_RE.findall(query)}
        if not query_codes.issubset(allowed_codes):
            continue
        normalized = query.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        accepted.append(query)
        if len(accepted) == 3:
            break
    return DecompositionPlan(original, True, tuple(accepted or [original]), planner_version)


def execute_plan(plan, retrieve, access_context, *, correction_budget=None, max_workers=3):
    queries = plan.subqueries if plan.is_complex else (plan.original_query,)
    queries = tuple(queries[:3])
    budget = correction_budget or CorrectionBudget(1)

    def run(query):
        return retrieve(query, access_context, budget)

    with ThreadPoolExecutor(max_workers=min(max(1, max_workers), len(queries))) as executor:
        return list(executor.map(run, queries))


def merge_branch_documents(branches):
    """Fuse branch results without introducing a document outside a branch."""
    merged = []
    seen = set()
    for branch in branches or ():
        for document in branch or ():
            metadata = getattr(document, "metadata", {}) or {}
            key = (
                metadata.get("doc_id"),
                metadata.get("trang_so"),
                metadata.get("chunk_index"),
                str(getattr(document, "page_content", ""))[:200],
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(document)
    return merged
