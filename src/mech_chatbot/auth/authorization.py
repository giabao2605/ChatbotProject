"""Role capability mapping for the multi-department knowledge platform.

The legacy ``admin`` role remains a compatibility role for document-workflow
capabilities during migration. Platform control-plane access is explicit-only.
New control-plane roles never grant document retrieval or file-download access;
the legacy ``admin`` role remains the one approved global-read exception.
"""
from __future__ import annotations

from typing import Iterable


# A requested role is a capability label used by API/UI route guards. The
# values are the database roles that can satisfy that capability. Legacy admin
# continues to satisfy document workflow capabilities, but it must never imply
# platform_admin or expose organization/rollout control-plane APIs.
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "platform_admin": frozenset({"platform_admin"}),
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
