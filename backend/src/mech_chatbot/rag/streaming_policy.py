"""Small, dependency-free policy helpers for safe answer streaming."""

from __future__ import annotations

import os
from collections.abc import Mapping


_TRUTHY = {"1", "true", "yes", "on"}


def strict_realtime_streaming_enabled(
    strict_answer_mode: bool,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the explicitly opted-in realtime strict path may run.

    The safe default is buffered verification.  A character holdback is not a
    proof that the emitted prefix is factual, so realtime remains off unless an
    operator deliberately enables a future sentence-verified implementation.
    """
    values = os.environ if env is None else env
    raw = str(values.get("STRICT_REALTIME_STREAMING", "false") or "").strip().lower()
    return bool(strict_answer_mode) and raw in _TRUTHY
