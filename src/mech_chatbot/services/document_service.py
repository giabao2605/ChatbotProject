"""P2.3 — Document service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repository import (
    archive_document,
    delete_document_completely,
    publish_as_new_variant,
    publish_as_new_version,
    publish_as_standalone,
    reject_document,
    update_document_common_metadata,
)

__all__ = [
    "archive_document",
    "delete_document_completely",
    "publish_as_new_variant",
    "publish_as_new_version",
    "publish_as_standalone",
    "reject_document",
    "update_document_common_metadata",
]
