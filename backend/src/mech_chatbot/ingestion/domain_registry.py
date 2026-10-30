"""Compatibility facade for department domain and security resolution."""

from mech_chatbot.db.registry_ports import (
    resolve_domain_by_department,
    resolve_security_by_department,
)
from mech_chatbot.domain.registry_policy import (
    DEFAULT_DOMAIN,
    DOMAINS,
    DomainConfig,
    FALLBACK_DOMAIN as _FALLBACK_DOMAIN,
    FALLBACK_SECURITY as _FALLBACK_SECURITY,
    get_default_security,
    get_domain_config,
    normalize_domain_value as _normalize_domain_value,
)

__all__ = [
    "DEFAULT_DOMAIN",
    "DOMAINS",
    "DomainConfig",
    "get_default_security",
    "get_domain_config",
    "resolve_domain_by_department",
    "resolve_security_by_department",
]
