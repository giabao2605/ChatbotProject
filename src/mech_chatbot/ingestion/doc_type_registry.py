"""Compatibility facade for document-type registry policy."""

from mech_chatbot.domain.registry_policy import (
    DEFAULT_CODE,
    DOC_TYPES,
    SUPPORTED_LANGS,
    canonical_label,
    doc_type_label,
    list_doc_types,
    normalize_doc_type,
    strip_accents,
)

__all__ = [
    "DEFAULT_CODE",
    "DOC_TYPES",
    "SUPPORTED_LANGS",
    "canonical_label",
    "doc_type_label",
    "list_doc_types",
    "normalize_doc_type",
    "strip_accents",
]
