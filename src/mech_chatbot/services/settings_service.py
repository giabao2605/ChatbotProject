"""P2.3 — App settings service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repository import (
    count_docs_by_department,
    get_all_app_settings,
    set_app_setting,
)

__all__ = [
    "count_docs_by_department",
    "get_all_app_settings",
    "set_app_setting",
]
