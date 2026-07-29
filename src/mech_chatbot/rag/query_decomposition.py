"""Bounded query decomposition that preserves one immutable access context."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any

from mech_chatbot.rag.answer_checks import extract_source_ids
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.rag.execution import RequestBudgetExceeded


_COMPLEX_CUES = (" và ", " đồng thời ", " so sánh ", " đối chiếu ", " versus ", " vs ")
_CODE_RE = re.compile(r"\b[A-Z]{1,10}[-_][A-Z0-9][A-Z0-9._-]*\b", re.IGNORECASE)
_MISSING_NOTICE = (
    "Phần chưa có đủ bằng chứng chưa thể trả lời từ tài liệu nội bộ hiện có."
)
_DENIED_NOTICE = (
    "Phần bị chặn chưa thể trả lời do không có nguồn được phép truy cập."
)


@dataclass(frozen=True)
class BranchPlan:
    original_query: str
    is_complex: bool
    subqueries: tuple[str, ...] = ()
    planner_version: str = "planner-v1"
    intents: tuple[str, ...] = ()
    intent_coverage: tuple[bool, ...] = ()
    used_fallback: bool = False
    intent_overflow: bool = False


DecompositionPlan = BranchPlan


@dataclass(frozen=True)
class BranchRetrievalResult:
    documents: list[Any]
    base_k: int
    retrieval_mode: str
    started_at: float
    active_filter: Any
    correction_cost: float = 0.0
    correction_attempted: bool = False
    correction_input_tokens: int = 0
    correction_output_tokens: int = 0
    access_denied: bool = False
    deadline_exceeded: bool = False


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


def audit_decomposition_stream(stream, branches):
    """Forward a final stream and record citations rendered for each branch."""
    rendered_parts = []
    outcomes = [str((branch or {}).get("outcome") or "") for branch in branches or ()]
    if "full_answer" in outcomes and any(outcome != "full_answer" for outcome in outcomes):
        marker = "Trả lời được một phần: "
        rendered_parts.append(marker)
        yield marker
    for chunk in stream:
        rendered_parts.append(str(chunk))
        yield chunk
    notices = []
    if any(outcome in {"insufficient_evidence", "partial_answer"} for outcome in outcomes):
        notices.append(_MISSING_NOTICE)
    if "access_denied" in outcomes:
        notices.append(_DENIED_NOTICE)
    rendered_text = "".join(rendered_parts)
    for notice in notices:
        if notice in rendered_text:
            continue
        chunk = f"\n{notice}"
        rendered_parts.append(chunk)
        rendered_text += chunk
        yield chunk
    rendered_source_ids = extract_source_ids("".join(rendered_parts))
    for branch in branches or ():
        branch_source_ids = {
            str(citation.get("source_id") or "").strip().upper()
            for citation in branch.get("citations") or []
            if citation.get("source_id")
        }
        branch["rendered_source_ids"] = sorted(branch_source_ids & rendered_source_ids)

def is_complex_query(question: str) -> bool:
    normalized = " " + str(question or "").strip().lower() + " "
    cue_count = sum(cue in normalized for cue in _COMPLEX_CUES)
    return cue_count > 0 or normalized.count("?") > 1


_INTENT_SEPARATOR = re.compile(
    r"\s*(?:[;\n]+|,\s*(?:đồng\s+thời|dong\s+thoi)|,\s+|"
    r"\b(?:đồng\s+thời|dong\s+thoi|và|va|also)\b)\s*",
    re.IGNORECASE,
)
_INTENT_STOP_WORDS = {
    "cho", "biet", "neu", "dong", "thoi", "va", "cua", "la", "bao",
    "nhieu", "the", "please", "also", "and",
}


def _fold(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).replace("đ", "d")


def split_query_intents(question: str) -> tuple[tuple[str, ...], bool]:
    original = str(question or "").strip()
    parts = [
        part.strip(" ,;?\t\r\n")
        for part in _INTENT_SEPARATOR.split(original)
        if part.strip(" ,;?\t\r\n")
    ]
    if len(parts) <= 3:
        return tuple(parts or ([original] if original else [])), False
    return tuple(parts[:2] + [" và ".join(parts[2:])]), True


def _intent_tokens(value: str) -> set[str]:
    codes = {code.casefold() for code in _CODE_RE.findall(str(value or ""))}
    tokens = set(re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", _fold(value)))
    return tokens - codes - _INTENT_STOP_WORDS


def _query_covers_intent(query: str, intent: str) -> bool:
    expected_codes = {code.casefold() for code in _CODE_RE.findall(intent)}
    actual_codes = {code.casefold() for code in _CODE_RE.findall(query)}
    if not expected_codes.issubset(actual_codes):
        return False
    expected_tokens = _intent_tokens(intent)
    if not expected_tokens:
        return bool(expected_codes) or bool(str(query or "").strip())
    return bool(expected_tokens & _intent_tokens(query))


def _coverage(intents: tuple[str, ...], queries: tuple[str, ...]) -> tuple[bool, ...]:
    return tuple(
        any(_query_covers_intent(query, intent) for query in queries)
        for intent in intents
    )


def codes_in_query(question: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code.lower() for code in _CODE_RE.findall(str(question or ""))))


def compile_query_plan(
    question: str,
    access_context,
    *,
    planner=None,
    planner_version="planner-v1",
) -> BranchPlan:
    del access_context
    original = str(question or "").strip()
    if not is_complex_query(original):
        return BranchPlan(original, False, (), planner_version)
    intents, overflow = split_query_intents(original)
    payload = {}
    if planner is not None:
        try:
            payload = planner(original) or {}
        except (ExternalAICallCancelled, RequestBudgetExceeded):
            raise
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
    proposed_queries = tuple(accepted)
    proposed_coverage = _coverage(intents, proposed_queries)
    planner_complete = bool(
        proposed_queries
        and len(proposed_queries) >= len(intents)
        and all(proposed_coverage)
    )
    final_queries = proposed_queries if planner_complete else intents[:3]
    final_coverage = _coverage(intents, final_queries)
    return BranchPlan(
        original_query=original,
        is_complex=True,
        subqueries=final_queries,
        planner_version=planner_version,
        intents=intents,
        intent_coverage=final_coverage,
        used_fallback=not planner_complete,
        intent_overflow=overflow,
    )


def build_plan(question: str, planner=None, *, planner_version="planner-v1") -> BranchPlan:
    """Backward-compatible adapter for callers that do not pass access context."""
    return compile_query_plan(
        question,
        {},
        planner=planner,
        planner_version=planner_version,
    )


def execute_plan(
    plan,
    retrieve,
    access_context,
    *,
    correction_budget=None,
    max_workers=3,
    deadline_monotonic=None,
    on_timeout=None,
):
    queries = plan.subqueries if plan.is_complex else (plan.original_query,)
    queries = tuple(queries[:3])
    budget = correction_budget or CorrectionBudget(1)

    def run(query):
        return retrieve(query, access_context, budget, deadline_monotonic)

    executor = ThreadPoolExecutor(max_workers=min(max(1, max_workers), len(queries)))
    futures = [
        executor.submit(copy_context().run, run, query)
        for query in queries
    ]
    try:
        timeout = None
        if deadline_monotonic is not None:
            timeout = max(0.0, deadline_monotonic - time.monotonic())
        done, pending = wait(futures, timeout=timeout)
        for future in pending:
            future.cancel()
        if pending and on_timeout is None:
            raise TimeoutError("decomposition retrieval deadline exceeded")
        return [
            future.result() if future in done else on_timeout(query)
            for query, future in zip(queries, futures)
        ]
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def build_decomposition_instruction(branches) -> str:
    """Build a safe multi-branch instruction without naming blocked sources."""
    if not branches:
        return ""
    instruction = (
        "\n\nHướng dẫn tổng hợp nhiều ý: với mỗi ý, chỉ trả lời đúng thông tin "
        "được hỏi bằng một kết luận ngắn có nguồn; không thêm thuộc tính khác "
        "từ cùng tài liệu và không suy diễn điều tài liệu không nói."
    )
    outcomes = [str((branch or {}).get("outcome") or "") for branch in (branches or ())]
    missing = sum(
        outcome in {"insufficient_evidence", "partial_answer"}
        for outcome in outcomes
    )
    denied = sum(outcome == "access_denied" for outcome in outcomes)
    if not missing and not denied:
        return instruction
    notices = []
    if missing:
        notices.append(f"{missing} nhánh chưa có đủ bằng chứng")
    if denied:
        notices.append(f"{denied} nhánh không thể truy cập")
    return (
        instruction + "\n\nLưu ý bắt buộc: " + "; ".join(notices) + ". "
        "Chỉ trả lời các nhánh có nguồn; không tự viết câu từ chối hoặc lặp lại "
        "mã, tên hay nội dung của phần bị chặn. Hệ thống sẽ tự thêm thông báo."
    )


def reconcile_grounded_calculation_branch(branches, citations):
    """Promote one resolved BOM branch after governed calculation succeeds."""
    candidates = [
        index
        for index, branch in enumerate(branches or ())
        if (branch or {}).get("bom_lookup")
        and (branch or {}).get("outcome") != "full_answer"
    ]
    if len(candidates) != 1 or not citations:
        return tuple(branches or ())
    selected = candidates[0]
    return tuple(
        {
            **branch,
            "outcome": "full_answer",
            "grounded_negative": False,
            "citations": list(citations),
        }
        if index == selected
        else branch
        for index, branch in enumerate(branches or ())
    )


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


def sufficient_branch_documents(results, branches):
    """Return only evidence from branches allowed to reach final generation."""
    return merge_branch_documents(
        (
            result.documents[:1]
            if str(result.retrieval_mode).startswith("general")
            else result.documents
        )
        for result, branch in zip(results or (), branches or ())
        if (branch or {}).get("outcome") == "full_answer"
    )
