"""P2.3 — Access/clearance service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repository import (
    count_pending_access_requests,
    create_access_request,
    get_grant_history,
    get_user_access_requests,
    list_access_requests,
    list_users_with_access,
    resolve_access_request,
    revoke_user_clearance,
    revoke_user_department,
    set_user_clearance,
)

__all__ = [
    "count_pending_access_requests",
    "create_access_request",
    "get_grant_history",
    "get_user_access_requests",
    "list_access_requests",
    "list_users_with_access",
    "resolve_access_request",
    "revoke_user_clearance",
    "revoke_user_department",
    "set_user_clearance",
]
