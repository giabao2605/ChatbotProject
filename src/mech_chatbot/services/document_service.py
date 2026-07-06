"""P2.3 — Document service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repository import (
    delete_document_completely,
    publish_as_new_variant,
    publish_as_new_version,
    publish_as_standalone,
    update_document_common_metadata,
)

__all__ = [
    "delete_document_completely",
    "publish_as_new_variant",
    "publish_as_new_version",
    "publish_as_standalone",
    "update_document_common_metadata",
]
