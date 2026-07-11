"""Department knowledge-governance and domain-profile persistence.

The tables are configured by migration V0022.  This module deliberately stores
only references to internal users, never a free-form approver name, so the
publication gate can reliably compare the authenticated actor with the
departmental approver.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from sqlalchemy import text

from ..engine import _ensure_engine, engine
from . import audit as _r_audit
from . import qdrant as _r_qdrant
from . import semantic_cache as _r_semantic_cache


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


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _json_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(_clean(item) for item in value if _clean(item)))


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_int_or_none(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} phai la UserID hop le") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} phai la UserID hop le")
    return parsed


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return _clean(raw).lower() in {"1", "true", "yes", "on"}


def _validate_governance_principal(conn, department_code: str, user_id: int | None, field_name: str):
    """Require active departmental users for owner/approver assignments."""
    if user_id is None:
        return
    row = conn.execute(
        text(
            """
            SELECT u.IsActive,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM dbo.UserDepartments ud
                       WHERE ud.UserID = u.UserID AND ud.Department = :department
                   ) THEN 1 ELSE 0 END AS IsDepartmentMember,
                   CASE WHEN EXISTS (
                       SELECT 1
                       FROM dbo.UserRoles ur
                       JOIN dbo.Roles r ON r.RoleID = ur.RoleID
                       WHERE ur.UserID = u.UserID
                         AND r.RoleName IN ('knowledge_approver', 'reviewer', 'admin')
                   ) THEN 1 ELSE 0 END AS HasApproverRole
            FROM dbo.Users u
            WHERE u.UserID = :user_id
            """
        ),
        {"department": department_code, "user_id": user_id},
    ).mappings().first()
    if not row or not bool(row["IsActive"]):
        raise ValueError(f"{field_name} phai la user dang hoat dong")
    if not bool(row["IsDepartmentMember"]):
        raise ValueError(f"{field_name} phai thuoc phong ban {department_code}")
    if field_name == "knowledge_approver_user_id" and not bool(row["HasApproverRole"]):
        raise ValueError("Knowledge Approver phai co role knowledge_approver hoac reviewer")


def _governance_from_row(row: Any) -> dict[str, Any]:
    owner = row["KnowledgeOwnerUserID"]
    approver = row["KnowledgeApproverUserID"]
    return {
        "department_code": _clean(row["DeptCode"]),
        "knowledge_owner_user_id": int(owner) if owner is not None else None,
        "knowledge_approver_user_id": int(approver) if approver is not None else None,
        # Aliases make the taxonomy contract explicit at the API boundary.
        "taxonomy_owner_user_id": int(owner) if owner is not None else None,
        "taxonomy_approver_user_id": int(approver) if approver is not None else None,
        "taxonomy_version": _clean(row["TaxonomyVersion"]) or "v1",
        "external_processing_policy": _clean(row["ExternalProcessingPolicy"]) or "all_external",
        "is_active": bool(row["IsActive"]),
        "updated_at": _iso(row["UpdatedAt"]),
        "updated_by": _clean(row["UpdatedBy"]),
    }


def _domain_profile_from_row(row: Any) -> dict[str, Any]:
    return {
        "department_code": _clean(row["DeptCode"]),
        "document_types": _json_list(row["DocumentTypesJson"]),
        "required_metadata": _json_list(row["RequiredMetadataJson"]),
        "router_patterns": _json_list(row["RouterPatternsJson"]),
        "parent_context_enabled": bool(row["ParentContextEnabled"]),
        "is_active": bool(row["IsActive"]),
        "updated_at": _iso(row["UpdatedAt"]),
        "updated_by": _clean(row["UpdatedBy"]),
    }


_GOVERNANCE_SELECT = """
    SELECT DeptCode, KnowledgeOwnerUserID, KnowledgeApproverUserID,
           TaxonomyVersion, ExternalProcessingPolicy, IsActive, UpdatedAt, UpdatedBy
    FROM dbo.DepartmentKnowledgeGovernance
"""


_DOMAIN_PROFILE_SELECT = """
    SELECT DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
           ParentContextEnabled, IsActive, UpdatedAt, UpdatedBy
    FROM dbo.DepartmentDomainProfile
"""


def get_department_knowledge_governance(department_code: str) -> dict[str, Any] | None:
    code = _clean(department_code)
    if not code:
        return None
    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(_GOVERNANCE_SELECT + " WHERE DeptCode = :code"),
            {"code": code},
        ).mappings().first()
    return _governance_from_row(row) if row else None


def list_department_knowledge_governance() -> list[dict[str, Any]]:
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(_GOVERNANCE_SELECT + " ORDER BY DeptCode")).mappings().all()
    return [_governance_from_row(row) for row in rows]


def get_department_domain_profile(department_code: str) -> dict[str, Any] | None:
    code = _clean(department_code)
    if not code:
        return None
    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(_DOMAIN_PROFILE_SELECT + " WHERE DeptCode = :code"),
            {"code": code},
        ).mappings().first()
    return _domain_profile_from_row(row) if row else None


def list_department_domain_profiles() -> list[dict[str, Any]]:
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(_DOMAIN_PROFILE_SELECT + " ORDER BY DeptCode")).mappings().all()
    return [_domain_profile_from_row(row) for row in rows]


def upsert_department_knowledge_governance(
    department_code: str,
    *,
    knowledge_owner_user_id: int | None,
    knowledge_approver_user_id: int | None,
    taxonomy_version: str,
    external_processing_policy: str = "all_external",
    is_active: bool = True,
    updated_by: str = "System",
) -> dict[str, Any]:
    code = _clean(department_code)
    if not code:
        raise ValueError("Department code khong hop le")
    owner = _to_int_or_none(knowledge_owner_user_id, "knowledge_owner_user_id")
    approver = _to_int_or_none(knowledge_approver_user_id, "knowledge_approver_user_id")
    taxonomy = _clean(taxonomy_version)
    policy = _clean(external_processing_policy).lower()
    if not taxonomy:
        raise ValueError("taxonomy_version la bat buoc")
    if policy not in {"all_external", "internal_only"}:
        raise ValueError("external_processing_policy khong hop le")

    _ensure_engine()
    params = {
        "code": code,
        "owner": owner,
        "approver": approver,
        "taxonomy": taxonomy[:100],
        "policy": policy,
        "active": 1 if is_active else 0,
        "updated_by": _clean(updated_by)[:100] or "System",
    }
    with engine.begin() as conn:
        _validate_governance_principal(conn, code, owner, "knowledge_owner_user_id")
        _validate_governance_principal(conn, code, approver, "knowledge_approver_user_id")
        conn.execute(
            text(
                """
                MERGE dbo.DepartmentKnowledgeGovernance AS target
                USING (SELECT :code AS DeptCode) AS source
                ON target.DeptCode = source.DeptCode
                WHEN MATCHED THEN UPDATE SET
                    KnowledgeOwnerUserID = :owner,
                    KnowledgeApproverUserID = :approver,
                    TaxonomyVersion = :taxonomy,
                    ExternalProcessingPolicy = :policy,
                    IsActive = :active,
                    UpdatedAt = GETDATE(),
                    UpdatedBy = :updated_by
                WHEN NOT MATCHED THEN INSERT (
                    DeptCode, KnowledgeOwnerUserID, KnowledgeApproverUserID,
                    TaxonomyVersion, ExternalProcessingPolicy, IsActive, UpdatedBy
                ) VALUES (
                    :code, :owner, :approver, :taxonomy, :policy, :active, :updated_by
                );
                """
            ),
            params,
        )
    _r_audit.write_audit_log(
        params["updated_by"],
        "department_knowledge_governance_upsert",
        "DepartmentKnowledgeGovernance",
        None,
        {
            "department_code": code,
            "knowledge_owner_user_id": owner,
            "knowledge_approver_user_id": approver,
            "taxonomy_version": params["taxonomy"],
            "external_processing_policy": policy,
            "is_active": bool(is_active),
        },
    )
    return get_department_knowledge_governance(code) or {}


def upsert_department_domain_profile(
    department_code: str,
    *,
    document_types: list[str] | tuple[str, ...],
    required_metadata: list[str] | tuple[str, ...],
    router_patterns: list[str] | tuple[str, ...],
    parent_context_enabled: bool = True,
    is_active: bool = True,
    updated_by: str = "System",
) -> dict[str, Any]:
    code = _clean(department_code)
    types = _json_list(document_types)
    required = _json_list(required_metadata)
    patterns = _json_list(router_patterns)
    if not code:
        raise ValueError("Department code khong hop le")
    if not types:
        raise ValueError("document_types phai co it nhat mot gia tri")
    if not required:
        raise ValueError("required_metadata phai co it nhat mot gia tri")

    _ensure_engine()
    params = {
        "code": code,
        "types": json.dumps(types, ensure_ascii=False),
        "required": json.dumps(required, ensure_ascii=False),
        "patterns": json.dumps(patterns, ensure_ascii=False),
        "parent_context": 1 if parent_context_enabled else 0,
        "active": 1 if is_active else 0,
        "updated_by": _clean(updated_by)[:100] or "System",
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                MERGE dbo.DepartmentDomainProfile AS target
                USING (SELECT :code AS DeptCode) AS source
                ON target.DeptCode = source.DeptCode
                WHEN MATCHED THEN UPDATE SET
                    DocumentTypesJson = :types,
                    RequiredMetadataJson = :required,
                    RouterPatternsJson = :patterns,
                    ParentContextEnabled = :parent_context,
                    IsActive = :active,
                    UpdatedAt = GETDATE(),
                    UpdatedBy = :updated_by
                WHEN NOT MATCHED THEN INSERT (
                    DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
                    ParentContextEnabled, IsActive, UpdatedBy
                ) VALUES (
                    :code, :types, :required, :patterns, :parent_context, :active, :updated_by
                );
                """
            ),
            params,
        )
    _r_audit.write_audit_log(
        params["updated_by"],
        "department_domain_profile_upsert",
        "DepartmentDomainProfile",
        None,
        {
            "department_code": code,
            "document_types": types,
            "required_metadata": required,
            "router_patterns": patterns,
            "parent_context_enabled": bool(parent_context_enabled),
            "is_active": bool(is_active),
        },
    )
    return get_department_domain_profile(code) or {}


def update_document_governance_metadata(
    doc_id: int,
    *,
    knowledge_owner_user_id: int | None = None,
    knowledge_approver_user_id: int | None = None,
    taxonomy_version: str | None = None,
    parent_applicable: bool | None = None,
    parent_section: str | None = None,
    parent_page: int | None = None,
    updated_by: str = "System",
) -> bool:
    try:
        normalized_doc_id = int(doc_id)
    except (TypeError, ValueError):
        raise ValueError("DocID khong hop le")
    owner = _to_int_or_none(knowledge_owner_user_id, "knowledge_owner_user_id")
    approver = _to_int_or_none(knowledge_approver_user_id, "knowledge_approver_user_id")
    sets = []
    params: dict[str, Any] = {"doc_id": normalized_doc_id}
    if knowledge_owner_user_id is not None:
        sets.append("KnowledgeOwnerUserID = :owner")
        params["owner"] = owner
    if knowledge_approver_user_id is not None:
        sets.append("KnowledgeApproverUserID = :approver")
        params["approver"] = approver
    if taxonomy_version is not None:
        value = _clean(taxonomy_version)
        if not value:
            raise ValueError("taxonomy_version khong duoc de trong")
        sets.append("TaxonomyVersion = :taxonomy")
        params["taxonomy"] = value[:100]
    if parent_applicable is not None:
        sets.append("ParentApplicable = :parent_applicable")
        params["parent_applicable"] = 1 if parent_applicable else 0
    if parent_section is not None:
        sets.append("ParentSection = :parent_section")
        params["parent_section"] = _clean(parent_section) or None
    if parent_page is not None:
        try:
            page = int(parent_page)
        except (TypeError, ValueError) as exc:
            raise ValueError("parent_page phai la so nguyen") from exc
        if page <= 0:
            raise ValueError("parent_page phai lon hon 0")
        sets.append("ParentPage = :parent_page")
        params["parent_page"] = page
    if not sets:
        return True

    previous = None
    _ensure_engine()
    with engine.begin() as conn:
        previous = conn.execute(
            text(
                """
                SELECT OwnerDepartment, KnowledgeOwnerUserID, KnowledgeApproverUserID,
                       TaxonomyVersion, ParentApplicable, ParentSection, ParentPage
                FROM dbo.TaiLieu WHERE DocID = :doc_id
                """
            ),
            {"doc_id": normalized_doc_id},
        ).mappings().first()
        if not previous:
            return False
        if knowledge_owner_user_id is not None:
            _validate_governance_principal(
                conn, str(previous["OwnerDepartment"]), owner, "knowledge_owner_user_id"
            )
        if knowledge_approver_user_id is not None:
            _validate_governance_principal(
                conn, str(previous["OwnerDepartment"]), approver, "knowledge_approver_user_id"
            )
        result = conn.execute(
            text("UPDATE dbo.TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :doc_id"),
            params,
        )
    if (getattr(result, "rowcount", 0) or 0) <= 0:
        return False
    qdrant_updates = {}
    if "owner" in params:
        qdrant_updates["knowledge_owner_user_id"] = params["owner"]
    if "approver" in params:
        qdrant_updates["knowledge_approver_user_id"] = params["approver"]
    if "taxonomy" in params:
        qdrant_updates["taxonomy_version"] = params["taxonomy"]
    if "parent_applicable" in params:
        qdrant_updates["parent_applicable"] = bool(params["parent_applicable"])
    if "parent_section" in params:
        qdrant_updates["parent_section"] = params["parent_section"]
    if "parent_page" in params:
        qdrant_updates["parent_page"] = params["parent_page"]
    if qdrant_updates and not _r_qdrant.update_qdrant_metadata(
        normalized_doc_id, qdrant_updates, require_points=False
    ):
        # Metadata changes influence parent hydration and publish validation.
        # Revert SQL when Qdrant cannot acknowledge the same payload update so
        # the serving source of truth does not drift silently.
        rollback = {
            "owner": previous["KnowledgeOwnerUserID"],
            "approver": previous["KnowledgeApproverUserID"],
            "taxonomy": previous["TaxonomyVersion"],
            "parent_applicable": previous["ParentApplicable"],
            "parent_section": previous["ParentSection"],
            "parent_page": previous["ParentPage"],
            "doc_id": normalized_doc_id,
        }
        try:
            with engine.begin() as conn:
                conn.execute(text("UPDATE dbo.TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :doc_id"), rollback)
            _r_qdrant.update_qdrant_metadata(
                normalized_doc_id,
                {
                    "knowledge_owner_user_id": previous["KnowledgeOwnerUserID"],
                    "knowledge_approver_user_id": previous["KnowledgeApproverUserID"],
                    "taxonomy_version": previous["TaxonomyVersion"],
                    "parent_applicable": bool(previous["ParentApplicable"]),
                    "parent_section": previous["ParentSection"],
                    "parent_page": previous["ParentPage"],
                },
                require_points=False,
            )
        except Exception as rollback_error:
            logger.error(
                "Khong rollback duoc governance metadata DocID %s: %s",
                normalized_doc_id,
                rollback_error,
                exc_info=True,
            )
        raise RuntimeError("Qdrant chua dong bo duoc governance metadata; SQL da rollback")
    _r_audit.write_audit_log(
        _clean(updated_by) or "System",
        "document_governance_metadata_update",
        "TaiLieu",
        normalized_doc_id,
        {key: value for key, value in params.items() if key != "doc_id"},
    )
    _r_semantic_cache._invalidate_semantic_cache("document.governance")
    return True


def validate_document_metadata_actor(doc_id: int, actor_id: int | None, actor_roles=None) -> tuple[bool, str]:
    """Allow only the configured owner/approver to edit reviewed metadata."""
    try:
        normalized_doc_id = int(doc_id)
        normalized_actor_id = int(actor_id)
    except (TypeError, ValueError):
        return False, "Metadata update phai co user da xac thuc"
    roles = {str(role).strip().lower() for role in (actor_roles or []) if str(role).strip()}
    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT KnowledgeOwnerUserID, KnowledgeApproverUserID
                FROM dbo.TaiLieu WHERE DocID = :doc_id
                """
            ),
            {"doc_id": normalized_doc_id},
        ).mappings().first()
    if not row:
        return False, "Khong tim thay tai lieu"
    allowed_ids = {
        int(value)
        for value in (row["KnowledgeOwnerUserID"], row["KnowledgeApproverUserID"])
        if value is not None
    }
    if normalized_actor_id in allowed_ids:
        return True, ""
    if "admin" in roles and _env_bool("KNOWLEDGE_ALLOW_ADMIN_METADATA_OVERRIDE", False):
        return True, ""
    return False, "Chi Knowledge Owner hoac Knowledge Approver cua tai lieu duoc sua metadata"


def list_missing_site_documents(limit: int = 500) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 500), 5000))
    _ensure_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT TOP (:limit) DocID, TenFile, ThuMuc, OwnerDepartment, Domain,
                       LifecycleStatus, ReviewStatus, PublicationState, NgayTaiLen
                FROM dbo.vwMissingSiteDocuments
                ORDER BY NgayTaiLen DESC, DocID DESC
                """
            ),
            {"limit": limit},
        ).mappings().all()
    return [
        {
            "doc_id": int(row["DocID"]),
            "file_name": _clean(row["TenFile"]),
            "folder": _clean(row["ThuMuc"]),
            "owner_department": _clean(row["OwnerDepartment"]),
            "domain": _clean(row["Domain"]),
            "lifecycle_status": _clean(row["LifecycleStatus"]),
            "review_status": _clean(row["ReviewStatus"]),
            "publication_state": _clean(row["PublicationState"]),
            "uploaded_at": _iso(row["NgayTaiLen"]),
        }
        for row in rows
    ]
