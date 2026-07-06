"""P2.3 — Glossary service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repository import (
    delete_glossary_term,
    list_domain_glossary,
    set_glossary_active,
    upsert_glossary_term,
)

__all__ = [
    "delete_glossary_term",
    "list_domain_glossary",
    "set_glossary_active",
    "upsert_glossary_term",
]
