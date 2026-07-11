"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import normalize_base_code
from . import audit as _r_audit
from . import document as _r_document
from . import feedback as _r_feedback
from . import qdrant as _r_qdrant
from . import publication as _r_publication
from . import semantic_cache as _r_semantic_cache

__all__ = [
    'archive_document',
    'publish_as_new_variant',
    'publish_as_new_version',
    'publish_as_standalone',
    'reject_document',
    'rollback_to_version',
    'rollback_to_version_by_family',
    'update_document_full_metadata',
]

def _sync_qdrant_metadata_best_effort(doc_id, metadata, action):
    ok = _r_qdrant.update_qdrant_metadata(doc_id, metadata)
    if not ok:
        logger.warning(f"{action}: khong dong bo duoc Qdrant metadata cho DocID {doc_id}; SQL van duoc cap nhat.")
    return ok


def update_document_full_metadata(doc_id, base_code=None, version_no=None, version_label=None,
                                  variant_code=None, variant_group=None, loai_tai_lieu=None,
                                  domain=None, security_level=None, site=None, cong_doan=None,
                                  reviewer="System"):
    """Cap nhat lai 'ma ban ve'/version/variant cho 1 tai lieu da duyet/tu choi.
    Dong bo ca SQL (TaiLieu + TaiLieuKyThuat + FamilyID) lan Qdrant payload."""
    _ensure_engine()
    norm_base = normalize_base_code(base_code) if base_code else base_code
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu
            SET BaseCode = :bc, VersionNo = :vn, VersionLabel = :vl,
                VariantCode = :vc, VariantGroup = :vg
            WHERE DocID = :id
        """), {"bc": norm_base, "vn": version_no, "vl": version_label,
               "vc": variant_code, "vg": variant_group, "id": doc_id})
        # GD4b: cho phep chinh phan loai linh hoat da phong ban (chi cap nhat khi co gia tri)
        if domain is not None:
            conn.execute(text("UPDATE TaiLieu SET Domain = :d WHERE DocID = :id"), {"d": domain, "id": doc_id})
        if security_level is not None:
            conn.execute(text("UPDATE TaiLieu SET SecurityLevel = :s WHERE DocID = :id"), {"s": security_level, "id": doc_id})
        if site is not None:
            conn.execute(text("UPDATE TaiLieu SET Site = :st WHERE DocID = :id"), {"st": (site or None), "id": doc_id})
        if loai_tai_lieu is not None:
            loai_tai_lieu = _r_document._normalize_doc_type_label(loai_tai_lieu)
            conn.execute(text("UPDATE TaiLieuKyThuat SET LoaiTaiLieu = :l WHERE DocID = :id"),
                         {"l": loai_tai_lieu, "id": doc_id})
        if norm_base:
            f_row = conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": norm_base}).fetchone()
            if f_row:
                conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :id"), {"fid": f_row[0], "id": doc_id})
            else:
                conn.execute(text("INSERT INTO DocumentFamily (BaseCode, FamilyName) VALUES (:b, :n)"), {"b": norm_base, "n": f"Family {norm_base}"})
                f_row2 = conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": norm_base}).fetchone()
                conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :id"), {"fid": f_row2[0], "id": doc_id})

    qmeta = {}
    if norm_base is not None: qmeta["base_code"] = norm_base
    if version_no is not None: qmeta["version_no"] = version_no
    if variant_code is not None: qmeta["variant_code"] = variant_code
    if variant_group is not None: qmeta["variant_group"] = variant_group
    if loai_tai_lieu is not None: qmeta["loai_tai_lieu"] = loai_tai_lieu
    if domain is not None: qmeta["domain"] = domain
    if security_level is not None: qmeta["security_level"] = security_level
    if site is not None: qmeta["site"] = (site or None)
    if cong_doan is not None: qmeta["cong_doan"] = (cong_doan or None)

    ok = _sync_qdrant_metadata_best_effort(doc_id, qmeta, "update_metadata") if qmeta else True
    # P3-2: metadata da doi -> feedback cu cua tai lieu nay tro thanh stale
    _r_feedback.mark_feedback_stale_for_doc(doc_id, resolved_by_doc_id=doc_id)
    _r_audit.write_audit_log(reviewer, "update_metadata", "TaiLieu", doc_id,
                    {"base_code": norm_base, "version": version_no, "variant": variant_code})
    return ok

def publish_as_new_version(doc_id, reviewer="System", reviewer_id=None, reviewer_roles=None):
    return bool(
        _r_publication.publish_document(
            doc_id, action="new_version", reviewer=reviewer,
            reviewer_id=reviewer_id, reviewer_roles=reviewer_roles,
        )
    )

def publish_as_new_variant(doc_id, reviewer="System", reviewer_id=None, reviewer_roles=None):
    return bool(
        _r_publication.publish_document(
            doc_id, action="new_variant", reviewer=reviewer,
            reviewer_id=reviewer_id, reviewer_roles=reviewer_roles,
        )
    )

def publish_as_standalone(doc_id, reviewer="System", reviewer_id=None, reviewer_roles=None):
    return bool(
        _r_publication.publish_document(
            doc_id, action="standalone", reviewer=reviewer,
            reviewer_id=reviewer_id, reviewer_roles=reviewer_roles,
        )
    )

def reject_document(doc_id, reviewer="System"):
    if not _r_qdrant.update_qdrant_metadata(
        doc_id,
        {
            "servable": False,
            "publication_state": "failed",
            "lifecycle_status": "rejected",
            "review_status": "rejected",
        },
    ):
        logger.error("reject_document: khong disable duoc Qdrant cho DocID %s", doc_id)
        return False
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET LifecycleStatus = 'rejected', ReviewStatus = 'rejected',
                NguoiDuyet = :rev, ReviewedBy = :rev, Servable = 0,
                PublicationState = 'failed', PublicationUpdatedAt = GETDATE()
            WHERE DocID = :id
        """), {"id": doc_id, "rev": reviewer})

    _r_audit.write_audit_log(reviewer, "reject_document", "TaiLieu", doc_id, {})
    _r_semantic_cache._invalidate_semantic_cache("doc.reject")
    return True

def archive_document(doc_id, reviewer="System"):
    if not _r_qdrant.update_qdrant_metadata(
        doc_id,
        {
            "servable": False,
            "is_current": False,
            "is_archived": True,
            "lifecycle_status": "archived",
        },
        require_points=True,
    ):
        logger.error("archive_document: khong disable duoc Qdrant cho DocID %s", doc_id)
        return False
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET IsCurrent = 0, IsArchived = 1, Servable = 0,
                LifecycleStatus = 'archived', ArchivedAt = GETDATE(),
                PublicationState = 'published', PublicationUpdatedAt = GETDATE()
            WHERE DocID = :id
        """), {"id": doc_id})
    _r_audit.write_audit_log(reviewer, "archive_document", "TaiLieu", doc_id, {})
    _r_semantic_cache._invalidate_semantic_cache("doc.archive")
    return True

def rollback_to_version(base_code, version_no, variant_code="default", reviewer="System", reviewer_id=None, reviewer_roles=None):
    """
    [DEPRECATED] Chuyen sang dung rollback_to_version_by_family.
    Wrapper tam thoi de giu tuong thich nguoc.
    """
    logger.warning("rollback_to_version (BaseCode) is deprecated. Use rollback_to_version_by_family instead.")
    _ensure_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT TOP 1 FamilyID FROM TaiLieu WHERE BaseCode = :bc"), {"bc": base_code}).fetchone()
            if not row or not row[0]:
                logger.error(f"Cannot find FamilyID for BaseCode {base_code}")
                return False
            return rollback_to_version_by_family(
                row[0], version_no, variant_code, reviewer,
                reviewer_id=reviewer_id, reviewer_roles=reviewer_roles,
            )
    except Exception as e:
        logger.error(f"Loi wrapper rollback: {e}")
        return False

def rollback_to_version_by_family(family_id, version_no, variant_code="default", reviewer="System", reviewer_id=None, reviewer_roles=None):
    """Restore a historical row through the publication outbox."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            target = conn.execute(text("""
                SELECT TOP 1 DocID FROM TaiLieu
                WHERE FamilyID = :fid
                  AND ISNULL(VariantCode, 'default') = :vc
                  AND VersionNo = :vn
                ORDER BY DocID DESC
            """), {"fid": family_id, "vc": variant_code or "default", "vn": version_no}).fetchone()
        if not target:
            return False
        return bool(_r_publication.publish_document(
            int(target[0]), action="new_version", reviewer=reviewer,
            reviewer_id=reviewer_id, reviewer_roles=reviewer_roles,
        ))
    except Exception as e:
        logger.error(f"Loi rollback_to_version_by_family: {e}", exc_info=True)
        return False
