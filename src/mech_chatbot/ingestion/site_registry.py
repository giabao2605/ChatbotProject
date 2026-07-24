"""Compatibility facade for department site resolution."""

from mech_chatbot.db.registry_ports import resolve_site_by_department
from mech_chatbot.domain.registry_policy import (
    DEPARTMENT_SITE,
    SITE_DEFAULT,
    SITES,
    site_label,
)

__all__ = [
    "DEPARTMENT_SITE",
    "SITE_DEFAULT",
    "SITES",
    "resolve_site_by_department",
    "site_label",
]
