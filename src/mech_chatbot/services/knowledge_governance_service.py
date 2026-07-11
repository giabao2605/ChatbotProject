"""Knowledge governance service boundary."""

from mech_chatbot.db.repository import (
    get_department_domain_profile,
    get_department_knowledge_governance,
    list_department_domain_profiles,
    list_department_knowledge_governance,
    list_missing_site_documents,
    update_document_governance_metadata,
    upsert_department_domain_profile,
    upsert_department_knowledge_governance,
    validate_document_metadata_actor,
)


__all__ = [
    "get_department_domain_profile",
    "get_department_knowledge_governance",
    "list_department_domain_profiles",
    "list_department_knowledge_governance",
    "list_missing_site_documents",
    "validate_document_metadata_actor",
    "update_document_governance_metadata",
    "upsert_department_domain_profile",
    "upsert_department_knowledge_governance",
]
