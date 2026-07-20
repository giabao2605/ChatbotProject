"""Explicit governance modes for human review evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


REVIEW_ROLES = ("rag", "security_qa", "operations")


@dataclass(frozen=True, slots=True)
class ReviewGovernance:
    valid: bool
    mode: str
    review_source: str
    owner: str | None = None
    reason: str = "valid"
    risk_accepted: bool = False

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "mode": self.mode,
            "review_source": self.review_source,
            "owner": self.owner,
            "risk_accepted": self.risk_accepted,
            "reason": self.reason,
        }


def _timestamp_valid(value: object) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None


def review_governance_status(
    payload: Mapping | None,
    *,
    source_commit: str | None = None,
    scope: str | None = None,
) -> ReviewGovernance:
    """Validate review mode while preserving the existing independent default."""
    if payload is None:
        return ReviewGovernance(
            valid=True,
            mode="multi_reviewer",
            review_source="independent",
        )
    if not isinstance(payload, Mapping):
        return ReviewGovernance(False, "invalid", "invalid", reason="payload_invalid")
    mode = str(payload.get("mode") or "").strip()
    if mode != "single_owner":
        return ReviewGovernance(
            False, mode or "invalid", "invalid", reason="unsupported_review_mode"
        )
    owner = str(payload.get("owner") or "").strip()
    role_signoffs = payload.get("role_signoffs")
    checks = {
        "schema": payload.get("schema") == "rag-review-governance-v1",
        "owner": bool(owner),
        "scope": payload.get("scope") in {"controlled_demo", "default_rollout"},
        "source_commit": bool(str(payload.get("source_commit") or "").strip()),
        "risk": payload.get("risk_accepted") is True,
        "accepted_at": _timestamp_valid(payload.get("accepted_at")),
        "roles": (
            isinstance(role_signoffs, Mapping)
            and set(role_signoffs) == set(REVIEW_ROLES)
            and all(
                isinstance(role_signoffs.get(role), Mapping)
                and str(role_signoffs[role].get("owner") or "").strip() == owner
                and role_signoffs[role].get("signed") is True
                and bool(str(role_signoffs[role].get("note") or "").strip())
                for role in REVIEW_ROLES
            )
        ),
    }
    if source_commit is not None:
        checks["expected_source_commit"] = payload.get("source_commit") == source_commit
    if scope is not None:
        checks["expected_scope"] = payload.get("scope") == scope
    valid = all(checks.values())
    reason = "valid" if valid else next(
        name for name, passed in checks.items() if not passed
    )
    return ReviewGovernance(
        valid=valid,
        mode=mode,
        review_source="owner_review",
        owner=owner or None,
        reason=reason,
        risk_accepted=payload.get("risk_accepted") is True,
    )


__all__ = ["REVIEW_ROLES", "ReviewGovernance", "review_governance_status"]
