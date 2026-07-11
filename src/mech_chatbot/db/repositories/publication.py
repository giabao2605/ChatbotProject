"""Publication contract, outbox, and SQL/Qdrant serving-state coordination.

The SQL database is the workflow source of truth. Qdrant points may be created
while a document is pending review, but only ``metadata.servable=true`` points
are eligible for retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os

from sqlalchemy import text

from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from . import audit as _r_audit
from . import qdrant as _r_qdrant
from . import semantic_cache as _r_semantic_cache


MAX_PUBLICATION_ATTEMPTS = int(os.getenv("PUBLICATION_MAX_ATTEMPTS", "5"))


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    code: str
    message: str

    def to_dict(self):
        return {"field": self.field, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    doc_id: int | None
    valid: bool
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    def __bool__(self):
        return self.valid

    def to_dict(self):
        return {
            "doc_id": self.doc_id,
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class PublicationResult:
    ok: bool
    doc_id: int | None
    outbox_id: int | None = None
    state: str | None = None
    error: str | None = None
    validation: ValidationResult | None = None

    def __bool__(self):
        return self.ok

    def to_dict(self):
        return {
            "ok": self.ok,
            "doc_id": self.doc_id,
            "outbox_id": self.outbox_id,
            "state": self.state,
            "error": self.error,
            "validation": self.validation.to_dict() if self.validation else None,
        }


def _clean(value):
    return str(value or "").strip()


def _classification_doc_type(classification_json):
    try:
        payload = json.loads(classification_json or "{}")
    except Exception:
        return ""
    return _clean(payload.get("document_type")) if isinstance(payload, dict) else ""


def _json_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            value = []
    if not isinstance(value, (list, tuple, set)):
        return []
    return [_clean(item) for item in value if _clean(item)]


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return bool(default)
    return _clean(value).lower() in {"1", "true", "yes", "on"}


def _required_metadata_value(field_name, row, doc_type):
    values = {
        "owner_department": row["OwnerDepartment"],
        "shared_departments": row["DeptCount"],
        "domain": row["Domain"],
        "document_type": doc_type,
        "source_system": row["SourceSystem"],
        "site": row["Site"],
        "security_level": row["SecurityLevel"],
        "classification_rationale": row["ClassificationRationale"],
        "classification_model": row["ClassificationModel"],
        "external_processing_policy": row["ExternalProcessingPolicy"],
        "taxonomy_version": row["TaxonomyVersion"],
        "knowledge_owner_user_id": row["KnowledgeOwnerUserID"],
        "knowledge_approver_user_id": row["KnowledgeApproverUserID"],
        "version": row["VersionNo"],
        "parent_section": row["ParentSection"],
        "parent_page": row["ParentPage"],
    }
    return values.get(_clean(field_name).lower())


def validate_publish_contract(doc_id) -> ValidationResult:
    """Validate the canonical publication contract from SQL source-of-truth."""
    try:
        doc_id = int(doc_id)
    except (TypeError, ValueError):
        return ValidationResult(
            doc_id=None,
            valid=False,
            issues=(ValidationIssue("doc_id", "invalid", "DocID khong hop le"),),
        )

    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT
                    t.DocID, t.TenFile, t.OwnerDepartment, t.Domain,
                    t.SecurityLevel, t.Site, t.SourceSystem,
                    t.ExternalProcessingPolicy, t.ClassificationRationale,
                    t.ClassificationModel, t.ClassificationJson,
                    t.LifecycleStatus, t.ReviewStatus, t.VersionNo,
                    t.KnowledgeOwnerUserID, t.KnowledgeApproverUserID,
                    t.TaxonomyVersion, t.ParentApplicable, t.ParentSection,
                    t.ParentPage,
                    g.TaxonomyVersion AS DepartmentTaxonomyVersion,
                    g.KnowledgeOwnerUserID AS DepartmentKnowledgeOwnerUserID,
                    g.KnowledgeApproverUserID AS DepartmentKnowledgeApproverUserID,
                    g.IsActive AS DepartmentGovernanceActive,
                    p.DocumentTypesJson, p.RequiredMetadataJson,
                    p.IsActive AS DomainProfileActive,
                    (SELECT COUNT(*) FROM dbo.PhongBanChiaSe p WHERE p.DocID = t.DocID) AS DeptCount,
                    (SELECT TOP 1 k.LoaiTaiLieu
                     FROM dbo.TaiLieuKyThuat k
                     WHERE k.DocID = t.DocID AND k.LoaiTaiLieu IS NOT NULL
                     ORDER BY k.TrangSo) AS TechnicalDocType
                FROM dbo.TaiLieu t
                LEFT JOIN dbo.DepartmentKnowledgeGovernance g
                    ON g.DeptCode = t.OwnerDepartment
                LEFT JOIN dbo.DepartmentDomainProfile p
                    ON p.DeptCode = t.OwnerDepartment
                WHERE t.DocID = :doc_id
                """
            ),
            {"doc_id": doc_id},
        ).mappings().first()

    if not row:
        return ValidationResult(
            doc_id=doc_id,
            valid=False,
            issues=(ValidationIssue("doc_id", "not_found", "Khong tim thay tai lieu"),),
        )

    issues = []

    def required(field_name, value, message):
        if not _clean(value):
            issues.append(ValidationIssue(field_name, "required", message))

    required("file_name", row["TenFile"], "Tai lieu phai co ten file")
    required("owner_department", row["OwnerDepartment"], "Thieu phong ban so huu")
    required("domain", row["Domain"], "Thieu domain")
    required("security_level", row["SecurityLevel"], "Thieu muc bao mat")
    required("site", row["Site"], "Thieu site")
    required("source_system", row["SourceSystem"], "Thieu he thong nguon")
    required(
        "external_processing_policy",
        row["ExternalProcessingPolicy"],
        "Thieu chinh sach external processing",
    )
    required(
        "classification_rationale",
        row["ClassificationRationale"],
        "Thieu ly do phan loai",
    )
    required(
        "classification_model",
        row["ClassificationModel"],
        "Thieu phien ban/model phan loai",
    )
    required(
        "taxonomy_version",
        row["TaxonomyVersion"],
        "Thieu phien ban taxonomy cua tai lieu",
    )
    if row["KnowledgeOwnerUserID"] is None:
        issues.append(
            ValidationIssue(
                "knowledge_owner_user_id",
                "required",
                "Thieu Knowledge Owner cua tai lieu",
            )
        )
    if row["KnowledgeApproverUserID"] is None:
        issues.append(
            ValidationIssue(
                "knowledge_approver_user_id",
                "required",
                "Thieu Knowledge Approver cua tai lieu",
            )
        )
    if not bool(row["DepartmentGovernanceActive"]):
        issues.append(
            ValidationIssue(
                "department_governance",
                "required",
                "Phong ban chua co governance active",
            )
        )
    elif row["DepartmentKnowledgeOwnerUserID"] is None or row["DepartmentKnowledgeApproverUserID"] is None:
        issues.append(
            ValidationIssue(
                "taxonomy_owner_approver",
                "required",
                "Phong ban chua gan taxonomy owner/approver",
            )
        )
    elif _clean(row["DepartmentTaxonomyVersion"]) != _clean(row["TaxonomyVersion"]):
        issues.append(
            ValidationIssue(
                "taxonomy_version",
                "stale",
                "Taxonomy cua tai lieu khong khop profile phong ban hien hanh",
            )
        )
    if not bool(row["DomainProfileActive"]):
        issues.append(
            ValidationIssue(
                "domain_profile",
                "required",
                "Phong ban chua co domain profile active",
            )
        )

    if int(row["DeptCount"] or 0) <= 0:
        issues.append(
            ValidationIssue(
                "shared_departments",
                "required",
                "Tai lieu phai co it nhat mot phong ban duoc truy cap",
            )
        )

    security = _clean(row["SecurityLevel"]).lower()
    if security and security not in {"public", "internal", "confidential"}:
        issues.append(
            ValidationIssue(
                "security_level",
                "invalid",
                "SecurityLevel phai la public/internal/confidential",
            )
        )

    lifecycle = _clean(row["LifecycleStatus"]).lower()
    if lifecycle in {"deleting", "rejected", "retired"}:
        issues.append(
            ValidationIssue(
                "lifecycle_status",
                "not_publishable",
                f"LifecycleStatus '{lifecycle}' khong the publish",
            )
        )

    doc_type = _classification_doc_type(row["ClassificationJson"]) or _clean(
        row["TechnicalDocType"]
    )
    if not doc_type:
        issues.append(
            ValidationIssue(
                "document_type",
                "required",
                "Thieu loai tai lieu trong metadata/classification",
            )
        )
    elif bool(row["DomainProfileActive"]):
        allowed_types = {value.lower() for value in _json_list(row["DocumentTypesJson"])}
        if allowed_types and doc_type.lower() not in allowed_types:
            issues.append(
                ValidationIssue(
                    "document_type",
                    "not_allowed",
                    "Loai tai lieu khong nam trong domain profile cua phong ban",
                )
            )

    try:
        version_no = int(row["VersionNo"])
    except (TypeError, ValueError):
        version_no = 0
    if version_no <= 0:
        issues.append(
            ValidationIssue("version", "required", "Thieu version hop le cua tai lieu")
        )

    policy = _clean(row["ExternalProcessingPolicy"]).lower()
    if policy and policy not in {"all_external", "internal_only"}:
        issues.append(
            ValidationIssue(
                "external_processing_policy",
                "invalid",
                "ExternalProcessingPolicy phai la all_external/internal_only",
            )
        )

    if bool(row["ParentApplicable"]):
        required("parent_section", row["ParentSection"], "Tai lieu nay can ParentSection")
        if row["ParentPage"] is None:
            issues.append(
                ValidationIssue("parent_page", "required", "Tai lieu nay can ParentPage")
            )

    if bool(row["DomainProfileActive"]):
        for metadata_field in _json_list(row["RequiredMetadataJson"]):
            value = _required_metadata_value(metadata_field, row, doc_type)
            if metadata_field.lower() == "shared_departments":
                missing = int(value or 0) <= 0
            else:
                missing = not _clean(value)
            if missing:
                issues.append(
                    ValidationIssue(
                        metadata_field,
                        "required_by_profile",
                        f"Thieu metadata bat buoc theo profile: {metadata_field}",
                    )
                )

    return ValidationResult(doc_id=doc_id, valid=not issues, issues=tuple(issues))


def validate_publish_actor(doc_id, reviewer_id=None, reviewer_roles=None) -> ValidationResult:
    """Require the configured departmental approver for every publish path.

    A platform admin has global read by business decision, but does not silently
    obtain knowledge-publish authority.  A documented, opt-in override exists
    only for an emergency policy configured by the deployment owner.
    """
    try:
        normalized_doc_id = int(doc_id)
    except (TypeError, ValueError):
        return ValidationResult(
            doc_id=None,
            valid=False,
            issues=(ValidationIssue("doc_id", "invalid", "DocID khong hop le"),),
        )
    try:
        actor_id = int(reviewer_id)
    except (TypeError, ValueError):
        actor_id = None
    roles = {str(role).strip().lower() for role in (reviewer_roles or []) if str(role).strip()}
    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT t.OwnerDepartment, t.KnowledgeApproverUserID,
                       g.KnowledgeApproverUserID AS DepartmentApproverUserID,
                       CASE WHEN EXISTS (
                           SELECT 1 FROM dbo.Users u
                           WHERE u.UserID = :actor_id AND u.IsActive = 1
                       ) THEN 1 ELSE 0 END AS ActorActive,
                       CASE WHEN EXISTS (
                           SELECT 1 FROM dbo.UserDepartments ud
                           WHERE ud.UserID = :actor_id AND ud.Department = t.OwnerDepartment
                       ) THEN 1 ELSE 0 END AS ActorInOwnerDepartment
                FROM dbo.TaiLieu t
                LEFT JOIN dbo.DepartmentKnowledgeGovernance g ON g.DeptCode = t.OwnerDepartment
                WHERE t.DocID = :doc_id
                """
            ),
            {"doc_id": normalized_doc_id, "actor_id": actor_id},
        ).mappings().first()
    if not row:
        return ValidationResult(
            doc_id=normalized_doc_id,
            valid=False,
            issues=(ValidationIssue("doc_id", "not_found", "Khong tim thay tai lieu"),),
        )
    approver_id = row["KnowledgeApproverUserID"] or row["DepartmentApproverUserID"]
    issues = []
    if actor_id is None:
        issues.append(ValidationIssue("reviewer_id", "required", "Publish phai co user da xac thuc"))
    if approver_id is None:
        issues.append(ValidationIssue("knowledge_approver_user_id", "required", "Tai lieu chua co approver"))
    if actor_id is not None and not bool(row["ActorActive"]):
        issues.append(ValidationIssue("reviewer_id", "inactive", "User publish khong con hoat dong"))
    if actor_id is not None and not bool(row["ActorInOwnerDepartment"]):
        issues.append(
            ValidationIssue(
                "reviewer_id",
                "department_required",
                "Knowledge Approver phai thuoc phong ban so huu tai lieu",
            )
        )
    allowed_approver_roles = {"knowledge_approver", "reviewer", "admin"}
    if actor_id is not None and not (roles & allowed_approver_roles):
        issues.append(
            ValidationIssue(
                "reviewer_roles",
                "role_required",
                "Publish yeu cau role knowledge_approver hoac reviewer",
            )
        )
    if actor_id is not None and approver_id is not None and actor_id != int(approver_id):
        admin_override = "admin" in roles and _env_bool("KNOWLEDGE_ALLOW_ADMIN_APPROVAL_OVERRIDE", False)
        if not admin_override:
            issues.append(
                ValidationIssue(
                    "knowledge_approver_user_id",
                    "forbidden",
                    "Chi Knowledge Approver cua tai lieu duoc publish",
                )
            )
    return ValidationResult(doc_id=normalized_doc_id, valid=not issues, issues=tuple(issues))


def _create_outbox_event(doc_id, action, reviewer, reviewer_id=None, reviewer_roles=None):
    contract_validation = validate_publish_contract(doc_id)
    actor_validation = validate_publish_actor(doc_id, reviewer_id, reviewer_roles)
    validation = ValidationResult(
        doc_id=contract_validation.doc_id,
        valid=bool(contract_validation) and bool(actor_validation),
        issues=tuple(contract_validation.issues) + tuple(actor_validation.issues),
    )
    if not validation:
        _r_audit.write_audit_log(
            reviewer,
            "publish_contract_rejected",
            "TaiLieu",
            doc_id,
            validation.to_dict(),
        )
        return PublicationResult(
            ok=False,
            doc_id=doc_id,
            state="validation_failed",
            error="Publish contract khong hop le",
            validation=validation,
        )

    normalized_roles = sorted(
        {str(role).strip().lower() for role in (reviewer_roles or []) if str(role).strip()}
    )
    payload = json.dumps(
        {
            "reviewer": reviewer,
            "reviewer_id": int(reviewer_id),
            "reviewer_roles": normalized_roles,
            "action": action,
        },
        ensure_ascii=False,
    )
    with engine.begin() as conn:
        state = conn.execute(
            text(
                """
                SELECT PublicationState, Servable, PublicationVersion
                FROM dbo.TaiLieu WITH (UPDLOCK, HOLDLOCK)
                WHERE DocID = :doc_id
                """
            ),
            {"doc_id": doc_id},
        ).mappings().first()
        if not state:
            return PublicationResult(
                ok=False,
                doc_id=doc_id,
                state="validation_failed",
                error="Khong tim thay tai lieu",
                validation=validation,
            )
        if state["PublicationState"] == "published" and bool(state["Servable"]):
            return PublicationResult(
                ok=True,
                doc_id=doc_id,
                state="published",
                validation=validation,
            )

        existing = conn.execute(
            text(
                """
                SELECT TOP 1 OutboxID, Status
                FROM dbo.PublicationOutbox WITH (UPDLOCK, HOLDLOCK)
                WHERE DocID = :doc_id AND Action = :action
                  AND Status IN ('pending', 'processing', 'failed')
                  AND AttemptCount < :max_attempts
                ORDER BY OutboxID DESC
                """
            ),
            {
                "doc_id": doc_id,
                "action": action,
                "max_attempts": MAX_PUBLICATION_ATTEMPTS,
            },
        ).mappings().first()

        conn.execute(
            text(
                """
                UPDATE dbo.TaiLieu
                SET PublicationState = 'publishing', Servable = 0,
                    PublicationError = NULL,
                    PublicationUpdatedAt = GETDATE()
                WHERE DocID = :doc_id
                """
            ),
            {"doc_id": doc_id},
        )
        if existing:
            outbox_id = int(existing["OutboxID"])
            if existing["Status"] != "processing":
                conn.execute(
                    text(
                        """
                        UPDATE dbo.PublicationOutbox
                        SET Status = 'pending', PayloadJson = :payload,
                            AvailableAt = GETDATE(), LastError = NULL,
                            LockedAt = NULL, LockedBy = NULL, UpdatedAt = GETDATE()
                        WHERE OutboxID = :outbox_id
                        """
                    ),
                    {"payload": payload, "outbox_id": outbox_id},
                )
            existing_status = existing["Status"]
        else:
            target_version = int(state["PublicationVersion"] or 0) + 1
            idempotency_key = f"publication:{doc_id}:{action}:{target_version}"
            outbox_row = conn.execute(
                text(
                    """
                    INSERT INTO dbo.PublicationOutbox
                        (DocID, Action, PayloadJson, IdempotencyKey, Status)
                    OUTPUT INSERTED.OutboxID
                    VALUES (:doc_id, :action, :payload, :key, 'pending')
                    """
                ),
                {
                    "doc_id": doc_id,
                    "action": action,
                    "payload": payload,
                    "key": idempotency_key,
                },
            ).fetchone()
            outbox_id = int(outbox_row[0]) if outbox_row else None
            existing_status = "pending"
    return PublicationResult(
        ok=True,
        doc_id=doc_id,
        outbox_id=outbox_id,
        state="processing" if existing_status == "processing" else "publishing",
        validation=validation,
    )


def _claim_outbox(outbox_id=None, worker_id="publication-inline"):
    _ensure_engine()
    with engine.begin() as conn:
        # Worker chet giua chung khong duoc khoa event vinh vien.
        conn.execute(
            text(
                """
                UPDATE dbo.PublicationOutbox
                SET Status = 'failed', LockedAt = NULL, LockedBy = NULL,
                    LastError = COALESCE(LastError, 'stale_processing_lock'),
                    AvailableAt = GETDATE(), UpdatedAt = GETDATE()
                WHERE Status = 'processing'
                  AND LockedAt < DATEADD(minute, -5, GETDATE())
                """
            )
        )
        if outbox_id is None:
            row = conn.execute(
                text(
                    """
                    SELECT TOP 1 OutboxID
                    FROM dbo.PublicationOutbox WITH (READPAST, UPDLOCK, ROWLOCK)
                    WHERE Status IN ('pending', 'failed')
                      AND AvailableAt <= GETDATE()
                      AND AttemptCount < :max_attempts
                    ORDER BY CreatedAt, OutboxID
                    """
                ),
                {"max_attempts": MAX_PUBLICATION_ATTEMPTS},
            ).fetchone()
            if not row:
                return None
            outbox_id = int(row[0])

            status_condition = "Status IN ('pending', 'failed') AND AvailableAt <= GETDATE()"
        else:
            status_condition = "Status IN ('pending', 'failed')"

        claimed = conn.execute(
            text(
                f"""
                UPDATE dbo.PublicationOutbox
                SET Status = 'processing', LockedAt = GETDATE(), LockedBy = :worker,
                    AttemptCount = AttemptCount + 1, UpdatedAt = GETDATE()
                OUTPUT INSERTED.OutboxID, INSERTED.DocID, INSERTED.Action,
                       INSERTED.PayloadJson, INSERTED.AttemptCount
                WHERE OutboxID = :outbox_id
                  AND {status_condition}
                  AND AttemptCount < :max_attempts
                """
            ),
            {
                "worker": worker_id,
                "outbox_id": int(outbox_id),
                "max_attempts": MAX_PUBLICATION_ATTEMPTS,
            },
        ).mappings().first()
        if claimed:
            conn.execute(
                text(
                    """
                    UPDATE dbo.TaiLieu
                    SET PublicationState = 'publishing', Servable = 0,
                        PublicationError = NULL, PublicationUpdatedAt = GETDATE()
                    WHERE DocID = :doc_id
                    """
                ),
                {"doc_id": int(claimed["DocID"])},
            )
    return dict(claimed) if claimed else None


def _mark_outbox_failure(event, error):
    outbox_id = int(event["OutboxID"])
    doc_id = int(event["DocID"])
    attempt = int(event.get("AttemptCount") or 1)
    delay_seconds = min(300, 2 ** min(attempt, 8))
    message = str(error)[:1900]
    # Qdrant co the da nhan operation truoc khi client gap timeout. Gui mot
    # strong-ordered disable de trang thai cuoi cung van fail-closed.
    _r_qdrant.update_qdrant_metadata(
        doc_id,
        {"servable": False, "publication_state": "failed"},
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dbo.PublicationOutbox
                SET Status = 'failed', LastError = :error,
                    AvailableAt = DATEADD(second, :delay, GETDATE()),
                    LockedAt = NULL, LockedBy = NULL, UpdatedAt = GETDATE()
                WHERE OutboxID = :outbox_id
                """
            ),
            {"error": message, "delay": delay_seconds, "outbox_id": outbox_id},
        )
        conn.execute(
            text(
                """
                UPDATE dbo.TaiLieu
                SET PublicationState = 'failed', Servable = 0,
                    PublicationError = :error,
                    PublicationRetryCount = PublicationRetryCount + 1,
                    PublicationUpdatedAt = GETDATE()
                WHERE DocID = :doc_id
                """
            ),
            {"error": message, "doc_id": doc_id},
        )
    logger.error(
        "Publication outbox failed outbox_id=%s doc_id=%s: %s",
        outbox_id,
        doc_id,
        message,
    )


def _publish_event(event):
    doc_id = int(event["DocID"])
    action = _clean(event["Action"])
    try:
        payload = json.loads(event.get("PayloadJson") or "{}")
    except Exception:
        payload = {}
    reviewer = _clean(payload.get("reviewer")) or "System"
    reviewer_id = payload.get("reviewer_id")
    reviewer_roles = payload.get("reviewer_roles") or []
    contract_validation = validate_publish_contract(doc_id)
    actor_validation = validate_publish_actor(doc_id, reviewer_id, reviewer_roles)
    if not contract_validation or not actor_validation:
        problems = [issue.message for issue in contract_validation.issues + actor_validation.issues]
        raise RuntimeError("Publish contract/actor khong con hop le: " + "; ".join(problems))
    serving_epoch = int(event["OutboxID"])

    with engine.connect() as conn:
        doc = conn.execute(
            text(
                """
                SELECT DocID, BaseCode, VariantCode, PublicationVersion, ServingEpoch
                FROM dbo.TaiLieu WHERE DocID = :doc_id
                """
            ),
            {"doc_id": doc_id},
        ).mappings().first()
        if not doc:
            raise RuntimeError(f"Khong tim thay DocID {doc_id}")
        old_rows = []
        if action == "new_version" and _clean(doc["BaseCode"]):
            old_rows = conn.execute(
                text(
                    """
                    SELECT DocID, ServingEpoch
                    FROM dbo.TaiLieu
                    WHERE BaseCode = :base_code
                      AND ISNULL(VariantCode, 'default') = :variant_code
                      AND IsCurrent = 1 AND DocID <> :doc_id
                    """
                ),
                {
                    "base_code": doc["BaseCode"],
                    "variant_code": _clean(doc["VariantCode"]) or "default",
                    "doc_id": doc_id,
                },
            ).fetchall()
    old_ids = [int(row[0]) for row in old_rows]
    old_epochs = {int(row[0]): int(row[1] or 0) for row in old_rows}
    next_version = int(doc["PublicationVersion"] or 0) + 1

    # Verify that staging points exist and are mutable before SQL becomes published.
    if not _r_qdrant.update_qdrant_metadata(
        doc_id,
        {
            "servable": False,
            "publication_state": "qdrant_synced",
            "publication_version": next_version,
            "serving_epoch": serving_epoch,
        },
        require_points=True,
    ):
        raise RuntimeError("Khong dong bo duoc staging points sang Qdrant")

    # Persist the state-machine transition before changing serving visibility.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE dbo.TaiLieu
                SET Servable = 0, PublicationState = 'qdrant_synced',
                    PublicationError = NULL, PublicationUpdatedAt = GETDATE()
                WHERE DocID = :doc_id
                """
            ),
            {"doc_id": doc_id},
        )

    # Old and new visibility changes share one strongly ordered batch request.
    # This removes the previous per-document disable/enable network sequence.
    publish_updates = {
        old_id: {
            "servable": False,
            "is_current": False,
            "is_archived": True,
            "lifecycle_status": "superseded",
            "publication_state": "published",
        }
        for old_id in old_ids
    }
    publish_updates[doc_id] = {
        "doc_status": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "is_current": True,
        "is_archived": False,
        "servable": True,
        "publication_state": "published",
        "publication_version": next_version,
        "serving_epoch": serving_epoch,
        "published_at": datetime.now().isoformat(),
        "supersedes_doc_id": old_ids[0] if old_ids else None,
    }
    if not _r_qdrant.batch_update_qdrant_metadata(publish_updates, require_points=True):
        raise RuntimeError("Khong batch activate/disable duoc Qdrant serving points")

    try:
        with engine.begin() as conn:
            for old_id in old_ids:
                conn.execute(
                    text(
                        """
                        UPDATE dbo.TaiLieu
                        SET IsCurrent = 0, IsArchived = 1, Servable = 0,
                            LifecycleStatus = 'superseded', ArchivedAt = GETDATE(),
                            PublicationState = 'published', PublicationUpdatedAt = GETDATE()
                        WHERE DocID = :old_id
                        """
                    ),
                    {"old_id": old_id},
                )
            conn.execute(
                text(
                    """
                    UPDATE dbo.TaiLieu
                    SET IsCurrent = 1, IsArchived = 0, Servable = 1,
                        LifecycleStatus = 'published', ReviewStatus = 'approved',
                        PublishedAt = GETDATE(), NgayDuyet = GETDATE(),
                        NguoiDuyet = :reviewer, ReviewedBy = :reviewer,
                        SupersedesDocID = :old_id, TrangThai = 'published',
                        PublicationState = 'published',
                        PublicationVersion = :publication_version,
                        ServingEpoch = :serving_epoch,
                        PublicationError = NULL, PublicationUpdatedAt = GETDATE()
                    WHERE DocID = :doc_id
                    """
                ),
                {
                    "doc_id": doc_id,
                    "reviewer": reviewer,
                    "old_id": old_ids[0] if old_ids else None,
                    "publication_version": next_version,
                    "serving_epoch": serving_epoch,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE dbo.PublicationOutbox
                    SET Status = 'done', LastError = NULL, CompletedAt = GETDATE(),
                        LockedAt = NULL, LockedBy = NULL, UpdatedAt = GETDATE()
                    WHERE OutboxID = :outbox_id
                    """
                ),
                {"outbox_id": int(event["OutboxID"])},
            )
    except Exception:
        # SQL is the workflow source of truth. If its finalization fails after
        # the Qdrant batch, restore the prior serving set before retrying.
        rollback_updates = {
            old_id: {
                "servable": True,
                "is_current": True,
                "is_archived": False,
                "lifecycle_status": "published",
                "publication_state": "published",
                "serving_epoch": old_epochs.get(old_id, 0),
            }
            for old_id in old_ids
        }
        rollback_updates[doc_id] = {
            "servable": False,
            "is_current": False,
            "is_archived": False,
            "publication_state": "qdrant_synced",
            "serving_epoch": int(doc["ServingEpoch"] or 0),
        }
        _r_qdrant.batch_update_qdrant_metadata(rollback_updates, require_points=False)
        raise

    _r_audit.write_audit_log(
        reviewer,
        f"publish_{action}",
        "TaiLieu",
        doc_id,
        {
            "old_doc_ids": old_ids,
            "publication_version": next_version,
            "serving_epoch": serving_epoch,
            "reviewer_id": reviewer_id,
        },
    )
    _r_semantic_cache._invalidate_semantic_cache("doc.publish")
    return PublicationResult(
        ok=True,
        doc_id=doc_id,
        outbox_id=int(event["OutboxID"]),
        state="published",
    )


def process_publication_outbox_once(outbox_id=None, worker_id="publication-worker"):
    """Claim and process one publication event. Safe to call repeatedly."""
    event = _claim_outbox(outbox_id=outbox_id, worker_id=worker_id)
    if not event:
        return None
    try:
        return _publish_event(event)
    except Exception as exc:
        _mark_outbox_failure(event, exc)
        return PublicationResult(
            ok=False,
            doc_id=int(event["DocID"]),
            outbox_id=int(event["OutboxID"]),
            state="failed",
            error=str(exc),
        )


def publish_document(
    doc_id,
    action="standalone",
    reviewer="System",
    reviewer_id=None,
    reviewer_roles=None,
):
    """Validate, enqueue, and attempt publication synchronously once."""
    action = _clean(action).lower()
    if action not in {"standalone", "new_variant", "new_version"}:
        return PublicationResult(
            ok=False,
            doc_id=doc_id,
            state="validation_failed",
            error=f"Publish action '{action}' khong hop le",
        )
    try:
        normalized_doc_id = int(doc_id)
    except (TypeError, ValueError):
        return PublicationResult(
            ok=False,
            doc_id=None,
            state="validation_failed",
            error="DocID khong hop le",
        )
    queued = _create_outbox_event(
        normalized_doc_id,
        action,
        reviewer,
        reviewer_id=reviewer_id,
        reviewer_roles=reviewer_roles,
    )
    if not queued:
        return queued
    if queued.state in {"published", "processing"}:
        return queued
    return process_publication_outbox_once(
        outbox_id=queued.outbox_id,
        worker_id=f"inline:{reviewer}",
    )


def reconcile_publications(limit=100, worker_id="publication-reconciler"):
    """Retry pending/failed publication events and return a compact summary."""
    limit = max(1, min(int(limit or 100), 1000))
    processed = 0
    succeeded = 0
    failed = 0
    for _ in range(limit):
        result = process_publication_outbox_once(worker_id=worker_id)
        if result is None:
            break
        processed += 1
        if result:
            succeeded += 1
        else:
            failed += 1
    return {"processed": processed, "succeeded": succeeded, "failed": failed}


def reconcile_serving_state(limit=500, worker_id="serving-reconciler"):
    """Repair SQL-to-Qdrant serving metadata drift from the SQL source of truth."""
    result = backfill_qdrant_servable(limit=limit)
    result["worker_id"] = str(worker_id)[:100]
    _r_audit.write_audit_log(
        str(worker_id)[:100],
        "reconcile_qdrant_serving_state",
        "TaiLieu",
        None,
        {"total": result.get("total"), "updated": result.get("updated"), "failed_doc_ids": result.get("failed_doc_ids", [])[:100]},
    )
    return result


def backfill_qdrant_servable(limit=None):
    """Copy authoritative SQL serving state to all Qdrant points."""
    _ensure_engine()
    limit_clause = ""
    params = {}
    if limit is not None:
        limit = max(1, int(limit))
        limit_clause = f"TOP ({limit})"
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT {limit_clause} DocID, Servable, PublicationState,
                       LifecycleStatus, ReviewStatus, IsCurrent, IsArchived,
                       PublicationVersion, ServingEpoch, TaxonomyVersion,
                       ExternalProcessingPolicy
                FROM dbo.TaiLieu
                WHERE TrangThaiVector = 1 AND LifecycleStatus <> 'deleting'
                ORDER BY DocID
                """
            ),
            params,
        ).mappings().all()

    updated = 0
    failed = []
    for row in rows:
        metadata = {
            "servable": bool(row["Servable"]),
            "publication_state": row["PublicationState"],
            "lifecycle_status": row["LifecycleStatus"],
            "review_status": row["ReviewStatus"],
            "is_current": bool(row["IsCurrent"]),
            "is_archived": bool(row["IsArchived"]),
            "publication_version": int(row["PublicationVersion"] or 1),
            "serving_epoch": int(row["ServingEpoch"] or 0),
            "taxonomy_version": row["TaxonomyVersion"] or "v1",
            "external_processing_policy": row["ExternalProcessingPolicy"] or "all_external",
        }
        if _r_qdrant.update_qdrant_metadata(
            int(row["DocID"]), metadata, require_points=True
        ):
            updated += 1
        else:
            failed.append(int(row["DocID"]))
    return {"total": len(rows), "updated": updated, "failed_doc_ids": failed}


__all__ = [
    "PublicationResult",
    "ValidationIssue",
    "ValidationResult",
    "backfill_qdrant_servable",
    "process_publication_outbox_once",
    "publish_document",
    "reconcile_publications",
    "reconcile_serving_state",
    "validate_publish_actor",
    "validate_publish_contract",
]
