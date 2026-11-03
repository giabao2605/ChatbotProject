"""Shared search-mode validation and broad-question routing."""

import re
from typing import Literal

from mech_chatbot.rag.text_utils import remove_accents

SearchMode = Literal["quick", "comprehensive"]
_BROAD_MARKERS = re.compile(
    r"\b(?:tat\s+ca|toan\s+bo|day\s+du|toan\s+dien|full\s+scan|"
    r"comprehensive|everything|all|every)\b"
)


def has_broad_marker(question: str) -> bool:
    return bool(_BROAD_MARKERS.search(remove_accents(question.casefold())))


def resolve_search_mode(
    requested: str, question: str, *, current_part_ids=(),
    resolved_query_scope: str | None = None,
) -> SearchMode:
    if not isinstance(requested, str) or requested not in {"quick", "comprehensive"}:
        raise ValueError("search_mode must be 'quick' or 'comprehensive'")
    if requested == "comprehensive":
        return "comprehensive"
    # Inherited codes and code-shaped words do not prove a single-object intent.
    if has_broad_marker(question) and resolved_query_scope != "single_candidate":
        return "comprehensive"
    return "quick"


def validate_continuation(search_mode: str, cursor: str | None) -> None:
    if cursor is not None and (
        search_mode != "comprehensive" or not isinstance(cursor, str)
        or not cursor.strip() or len(cursor) > 4096
    ):
        raise ValueError("continuation_cursor requires comprehensive search and a bounded nonempty token")
