"""Process-level contracts used by the public RAG execution facade."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
import math
import os
from typing import Any, Literal, Mapping


ExecutionMode = Literal["production", "evaluation", "pilot_replay", "test"]
CONTROLLED_DEMO_REQUEST_DEADLINE_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class RagRuntimeContract:
    """Canonical process-level settings reported by health and pilot evidence."""

    execution_context: ExecutionMode
    evaluation_force_ambiguous: bool
    request_deadline_seconds: float

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | RagRuntimeContract,
    ) -> RagRuntimeContract:
        if isinstance(value, cls):
            return value
        if not isinstance(value, MappingABC):
            raise ValueError("runtime contract must be a mapping")
        context = str(value.get("execution_context") or "").strip().lower()
        if context not in {"production", "evaluation", "pilot_replay", "test"}:
            raise ValueError("runtime contract has an unsupported execution context")
        force_ambiguous = value.get("evaluation_force_ambiguous")
        if not isinstance(force_ambiguous, bool):
            raise ValueError("runtime contract requires a boolean evaluation override")
        raw_deadline = value.get("request_deadline_seconds")
        if isinstance(raw_deadline, bool):
            raise ValueError("runtime contract requires a numeric deadline")
        try:
            deadline = float(raw_deadline)
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime contract requires a numeric deadline") from exc
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("runtime contract deadline must be finite and positive")
        return cls(
            execution_context=context,
            evaluation_force_ambiguous=force_ambiguous,
            request_deadline_seconds=deadline,
        )

    @classmethod
    def from_environment(cls) -> RagRuntimeContract:
        raw_override = os.getenv("RAG_EVAL_FORCE_AMBIGUOUS", "false").strip().lower()
        if raw_override not in {
            "1", "true", "yes", "y", "on",
            "0", "false", "no", "n", "off",
        }:
            raise ValueError("RAG_EVAL_FORCE_AMBIGUOUS must be a boolean")
        return cls.from_mapping(
            {
                "execution_context": os.getenv("RAG_EXECUTION_CONTEXT", "production"),
                "evaluation_force_ambiguous": raw_override
                in {"1", "true", "yes", "y", "on"},
                "request_deadline_seconds": os.getenv(
                    "RAG_REQUEST_DEADLINE_SECONDS", "120"
                ),
            }
        )

    @property
    def is_controlled_demo(self) -> bool:
        return (
            self.execution_context == "production"
            and self.evaluation_force_ambiguous is False
            and self.request_deadline_seconds
            == CONTROLLED_DEMO_REQUEST_DEADLINE_SECONDS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_context": self.execution_context,
            "evaluation_force_ambiguous": self.evaluation_force_ambiguous,
            "request_deadline_seconds": self.request_deadline_seconds,
        }


__all__ = [
    "CONTROLLED_DEMO_REQUEST_DEADLINE_SECONDS",
    "ExecutionMode",
    "RagRuntimeContract",
]
