"""P2.3 — Material service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repository import (
    add_material_synonym,
    delete_material,
    delete_material_synonym,
    list_materials,
    upsert_material,
)

__all__ = [
    "add_material_synonym",
    "delete_material",
    "delete_material_synonym",
    "list_materials",
    "upsert_material",
]
