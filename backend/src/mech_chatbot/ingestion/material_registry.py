"""Compatibility facade for the DB-backed material dictionary."""

from mech_chatbot.db.registry_ports import (
    get_known_materials,
    get_material_patterns,
    normalize_material,
    refresh_cache,
)

__all__ = [
    "get_known_materials",
    "get_material_patterns",
    "normalize_material",
    "refresh_cache",
]
