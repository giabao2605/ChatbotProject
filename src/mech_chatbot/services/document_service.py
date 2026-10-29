"""P2.3 — Document service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repositories.doc_metadata import (
    update_document_common_metadata,
    update_pending_variant_code,
)
from mech_chatbot.db.repositories.document import (
    delete_document_completely,
)
from mech_chatbot.db.repositories.publication import (
    publish_document,
    reconcile_serving_state,
    validate_publish_actor,
    validate_publish_contract,
)
from mech_chatbot.db.repositories.version import (
    archive_document,
    publish_as_new_variant,
    publish_as_new_version,
    publish_as_standalone,
    reject_document,
)

__all__ = [
    "archive_document",
    "delete_document_completely",
    "publish_as_new_variant",
    "publish_as_new_version",
    "publish_as_standalone",
    "publish_document",
    "reconcile_serving_state",
    "reject_document",
    "update_document_common_metadata",
    "validate_publish_contract",
]
