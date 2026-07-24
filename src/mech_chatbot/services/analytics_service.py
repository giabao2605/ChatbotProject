"""P2.3 — Analytics/observability service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repositories.analytics import (
    dashboard_by_department,
    get_observability,
    get_usage_analytics,
)
from mech_chatbot.db.repositories.semantic_cache import (
    sc_clear_all,
    sc_stats,
)

__all__ = [
    "dashboard_by_department",
    "get_observability",
    "get_usage_analytics",
    "sc_clear_all",
    "sc_stats",
]
