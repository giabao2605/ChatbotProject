"""Role capability mapping for the multi-department knowledge platform.

The legacy ``admin`` role remains a compatibility role during migration. New
assignments should use the least-privileged roles below. New control-plane
roles never grant document retrieval or file-download access; the legacy
``admin`` role is the one explicit business-approved global-read exception.
"""
from __future__ import annotations

from typing import Iterable


# A requested role is a capability label used by API/UI route guards. The
# values are the database roles that can satisfy that capability. Keep the old
# ``admin`` value only as a transitional super-user capability, not as a
# retrieval bypass.
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "platform_admin": frozenset({"platform_admin", "admin"}),
    "security_admin": frozenset({"security_admin", "admin"}),
    "knowledge_approver": frozenset({"knowledge_approver", "reviewer", "admin"}),
    "reviewer": frozenset({"reviewer", "knowledge_approver", "admin"}),
    "uploader": frozenset({"uploader", "admin"}),
    "viewer": frozenset({"viewer", "knowledge_consumer", "admin"}),
    "knowledge_consumer": frozenset({"knowledge_consumer", "viewer", "admin"}),
    # The legacy admin role is deliberately kept distinct from platform_admin.
    # New platform administrators receive only the explicitly requested
    # platform_admin capability, never reviewer/uploader capabilities by
    # implication.
    "admin": frozenset({"admin"}),
}


def normalized_roles(roles: Iterable[object] | None) -> set[str]:
    return {str(role).strip().lower() for role in (roles or []) if str(role).strip()}


def role_allows(current_roles: Iterable[object] | None, *required_roles: str) -> bool:
    """Return whether an assigned role satisfies one requested capability."""
    current = normalized_roles(current_roles)
    for required in required_roles:
        key = str(required or "").strip().lower()
        if not key:
            continue
        accepted = ROLE_CAPABILITIES.get(key, frozenset({key}))
        if current & accepted:
            return True
    return False


__all__ = ["ROLE_CAPABILITIES", "normalized_roles", "role_allows"]
