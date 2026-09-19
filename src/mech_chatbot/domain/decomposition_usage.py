"""Shared metadata-only usage contract for decomposition."""

from __future__ import annotations

import math
from typing import Any, Mapping

def _usage_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def _usage_float(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return parsed


def normalize_decomposition_usage(value: Any) -> dict[str, Any] | None:
    """Keep only the declared metadata-only decomposition usage contract."""

    if value in (None, {}):
        return None
    if not isinstance(value, Mapping):
        raise ValueError("decomposition_usage must be an object")
    if value.get("schema") != "rag-decomposition-usage-v1":
        raise ValueError("decomposition_usage schema is invalid")
    planner = value.get("planner") or {}
    retrieval_batch = value.get("retrieval_batch") or {}
    final_context = value.get("final_context") or {}
    final_generation = value.get("final_generation") or {}
    if not all(
        isinstance(item, Mapping)
        for item in (planner, retrieval_batch, final_context, final_generation)
    ):
        raise ValueError("decomposition_usage stage buckets must be objects")
    branches = []
    for index, raw in enumerate(value.get("branches") or (), 1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"decomposition_usage branch {index} must be an object")
        retrieval = raw.get("retrieval") or {}
        correction = raw.get("correction") or {}
        if not isinstance(retrieval, Mapping) or not isinstance(correction, Mapping):
            raise ValueError(f"decomposition_usage branch {index} buckets are invalid")
        latency_scope = str(retrieval.get("latency_scope") or "branch")
        if latency_scope not in {"branch", "shared_batch"}:
            raise ValueError(
                f"decomposition_usage branch {index} latency_scope is invalid"
            )
        cost_status = str(retrieval.get("cost_status") or "unpriced")
        if cost_status not in {"unpriced", "priced"}:
            raise ValueError(f"decomposition_usage branch {index} cost_status is invalid")
        retrieval_cost = retrieval.get("estimated_cost")
        if retrieval_cost is not None:
            retrieval_cost = _usage_float(
                retrieval_cost,
                f"branches[{index}].retrieval.estimated_cost",
            )
        branches.append(
            {
                "branch_id": str(raw.get("branch_id") or f"branch-{index}"),
                "retrieval": {
                    "latency_ms": _usage_int(
                        retrieval.get("latency_ms"),
                        f"branches[{index}].retrieval.latency_ms",
                    ),
                    "latency_scope": latency_scope,
                    "document_count": _usage_int(
                        retrieval.get("document_count"),
                        f"branches[{index}].retrieval.document_count",
                    ),
                    "estimated_input_tokens": _usage_int(
                        retrieval.get("estimated_input_tokens"),
                        f"branches[{index}].retrieval.estimated_input_tokens",
                    ),
                    "estimated_cost": retrieval_cost,
                    "cost_status": cost_status,
                },
                "correction": {
                    "attempted": bool(correction.get("attempted")),
                    "input_tokens": _usage_int(
                        correction.get("input_tokens"),
                        f"branches[{index}].correction.input_tokens",
                    ),
                    "output_tokens": _usage_int(
                        correction.get("output_tokens"),
                        f"branches[{index}].correction.output_tokens",
                    ),
                    "estimated_cost": _usage_float(
                        correction.get("estimated_cost"),
                        f"branches[{index}].correction.estimated_cost",
                    ),
                },
            }
        )
    return {
        "schema": "rag-decomposition-usage-v1",
        "planner": {
            "calls": _usage_int(planner.get("calls"), "planner.calls"),
            "input_tokens": _usage_int(
                planner.get("input_tokens"), "planner.input_tokens"
            ),
            "output_tokens": _usage_int(
                planner.get("output_tokens"), "planner.output_tokens"
            ),
            "estimated_cost": _usage_float(
                planner.get("estimated_cost"), "planner.estimated_cost"
            ),
        },
        "branches": branches,
        "retrieval_batch": {
            "latency_ms": _usage_int(
                retrieval_batch.get("latency_ms"),
                "retrieval_batch.latency_ms",
            ),
            "branch_count": _usage_int(
                retrieval_batch.get("branch_count"),
                "retrieval_batch.branch_count",
            ),
            "shared": bool(retrieval_batch.get("shared")),
        },
        "final_context": {
            "estimated_input_tokens": _usage_int(
                final_context.get("estimated_input_tokens"),
                "final_context.estimated_input_tokens",
            ),
            "estimated_input_cost": _usage_float(
                final_context.get("estimated_input_cost"),
                "final_context.estimated_input_cost",
            ),
            "included_in_final_generation": bool(
                final_context.get("included_in_final_generation")
            ),
        },
        "final_generation": {
            "calls": _usage_int(
                final_generation.get("calls"), "final_generation.calls"
            ),
            "input_tokens": _usage_int(
                final_generation.get("input_tokens"),
                "final_generation.input_tokens",
            ),
            "output_tokens": _usage_int(
                final_generation.get("output_tokens"),
                "final_generation.output_tokens",
            ),
            "estimated_cost": _usage_float(
                final_generation.get("estimated_cost"),
                "final_generation.estimated_cost",
            ),
        },
    }


