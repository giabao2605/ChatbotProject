"""P2.3 — Audit service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repositories.audit import (
    write_audit_log,
)

__all__ = [
    "write_audit_log",
]
