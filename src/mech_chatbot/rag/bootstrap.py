"""Legacy RAG bootstrap names without import-time infrastructure.

Production Qdrant, embedding, text-generation, and vision dependencies are
constructed by :mod:`mech_chatbot.composition.rag_runtime`.  The names below
remain only while Phase 5 migrates internal compatibility imports; importing
this module is deliberately side-effect free.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def env_bool(
    name: str,
    default: bool = False,
    *,
    values: Mapping[str, Any] | None = None,
) -> bool:
    """Parse an explicitly supplied compatibility flag.

    Ambient environment access is intentionally unsupported here. New callers
    receive typed policy values through the request's composed dependencies.
    """

    if values is None or name not in values:
        return bool(default)
    return str(values[name]).strip().lower() in {"1", "true", "yes", "y", "on"}


# Temporary compatibility exports for the Phase 5 internal-import migration.
# They are inert values, not lazy locators and never construct infrastructure.
_VISION_MODEL = None
STRICT_ANSWER_MODE = True
RERANK_PER_PART = 8
RERANK_TOP_N_CAP = 20
client = None
vectorstore = None
llm = None


__all__ = [
    "_VISION_MODEL",
    "RERANK_PER_PART",
    "RERANK_TOP_N_CAP",
    "STRICT_ANSWER_MODE",
    "client",
    "env_bool",
    "llm",
    "vectorstore",
]
