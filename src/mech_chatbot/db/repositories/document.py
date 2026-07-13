"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import os
import json
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from mech_chatbot.config.settings import QDRANT_COLLECTION
from ._shared import _sanitize_date, _sanitize_int, _sanitize_text, normalize_base_code
from . import audit as _r_audit
from . import catalog as _r_catalog
from . import doc_metadata as _r_doc_metadata
from . import feedback as _r_feedback
from . import qdrant as _r_qdrant
from . import semantic_cache as _r_semantic_cache

__all__ = [
    '_get_or_create_doc',
    '_normalize_doc_type_label',
    '_prepare_metadata_params',
    'delete_document_completely',
    'find_current_docs',
    'get_doc',
    'get_document_departments',
    'get_document_info',
    'mark_document_ingest_failed',
    'set_document_departments',
    'update_document_classification',
]

 
# ==========================================
# DOCUMENT METADATA (Fix #1: tach reset / insert)
# ==========================================
def _get_or_create_doc(conn, file_name, thu_muc):
    # Fetch classification json tu IngestionJobs (neu co) de update metadata
    job = conn.execute(
        text("SELECT TOP 1 ClassificationJson, FilePath, UploadMetaJson, Site FROM dbo.IngestionJobs WHERE TenFile = :f AND ThuMuc = :t ORDER BY CreatedAt DESC"),
        {"f": file_name, "t": thu_muc}
    ).fetchone()
    
    cls_data = {}
    if job and job[0]:
        try:
            cls_data = json.loads(job[0])
        except (TypeError, ValueError):
            cls_data = {}
            
    base_code = cls_data.get("base_code")
    base_code = normalize_base_code(base_code) if base_code else None
    version_label = cls_data.get("version_label")
    version_no = cls_data.get("version_no", 1)
    variant_code = cls_data.get("variant_code") or "default"
    classification_rationale = (
        cls_data.get("classification_rationale")
        or cls_data.get("reason")
        or cls_data.get("classification_reason")
        or "folder_and_content_classification"
    )
    classification_model = (
        cls_data.get("classification_model")
        or cls_data.get("model")
        or os.getenv("GPT_MODEL_NAME")
        or "rule_based"
    )

    # GD2: luon ghi Domain + SecurityLevel vao TaiLieu (truoc day chi ghi Site/family).
    # Uu tien gia tri tu classification; fallback resolve theo phong ban (data-driven Departments).
    from mech_chatbot.db.registry_ports import resolve_domain_by_department, resolve_security_by_department
    domain = cls_data.get("domain") or resolve_domain_by_department(thu_muc)
    security_level = cls_data.get("security_level") or resolve_security_by_department(thu_muc)

    # Every new document inherits its department's governance assignment and
    # taxonomy version.  Empty values are intentional: publication validation
    # will hold a document in review until a real owner/approver is configured.
    governance = conn.execute(
        text(
            """
            SELECT KnowledgeOwnerUserID, KnowledgeApproverUserID,
                   TaxonomyVersion, ExternalProcessingPolicy
            FROM dbo.DepartmentKnowledgeGovernance
            WHERE DeptCode = :dept AND IsActive = 1
            """
        ),
        {"dept": thu_muc or "CHUNG"},
    ).mappings().first()
    knowledge_owner_user_id = (
        governance["KnowledgeOwnerUserID"] if governance else None
    )
    knowledge_approver_user_id = (
        governance["KnowledgeApproverUserID"] if governance else None
    )
    taxonomy_version = (
        (governance["TaxonomyVersion"] if governance else None) or "v1"
    )
    external_processing_policy = (
        (governance["ExternalProcessingPolicy"] if governance else None)
        or cls_data.get("external_processing_policy")
        or "all_external"
    )
    resolved_site = (job[3] if job and len(job) > 3 else None) or _r_catalog._resolve_site(thu_muc)
    
    f_id = None
    if base_code:
        f_row = conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": base_code}).fetchone()
        if f_row:
            f_id = f_row[0]
        else:
            # Auto-create DocumentFamily neu chua ton tai
            f_insert = conn.execute(
                text("INSERT INTO DocumentFamily (BaseCode, FamilyName) OUTPUT INSERTED.FamilyID VALUES (:b, :n)"),
                {"b": base_code, "n": base_code}
            )
            inserted = f_insert.fetchone()
            if inserted:
                f_id = inserted[0]

    row = conn.execute(
        text("SELECT DocID, LifecycleStatus, ReviewStatus, IsCurrent FROM TaiLieu WHERE TenFile = :f AND ThuMuc = :t"),
        {"f": file_name, "t": thu_muc},
    ).fetchone()
    
    if row:
        doc_id, lifecycle_status, review_status, is_current = row
        if lifecycle_status == 'published' and review_status == 'approved':
            raise ValueError(f"Tài liệu {file_name} đã được published. Không cho phép re-ingest để bảo toàn dữ liệu.")
        # Update metadata neu re-ingest draft/rejected
        conn.execute(
            text("""UPDATE TaiLieu SET 
                ReviewStatus = 'pending_review',
                FamilyID = :fid, BaseCode = :bc, VersionNo = :vn, VersionLabel = :vl, VariantCode = :vc,
                Site = :site, Domain = :domain, SecurityLevel = :seclvl, FilePath = :fp,
                OwnerDepartment = :owner_department, SourceSystem = 'upload',
                ExternalProcessingPolicy = :external_processing_policy,
                ClassificationRationale = :classification_rationale,
                ClassificationModel = :classification_model,
                ClassificationJson = :classification_json,
                KnowledgeOwnerUserID = COALESCE(KnowledgeOwnerUserID, :knowledge_owner_user_id),
                KnowledgeApproverUserID = COALESCE(KnowledgeApproverUserID, :knowledge_approver_user_id),
                TaxonomyVersion = COALESCE(NULLIF(TaxonomyVersion, ''), :taxonomy_version),
                PublicationState = 'draft', PublicationError = NULL, Servable = 0
                WHERE DocID = :d"""),
            {"d": doc_id, "fid": f_id, "bc": base_code, "vn": version_no, "vl": version_label, "vc": variant_code, "site": resolved_site, "domain": domain, "seclvl": security_level, "fp": (job[1] if job else None), "owner_department": thu_muc or "CHUNG", "external_processing_policy": external_processing_policy, "classification_rationale": str(classification_rationale)[:1000], "classification_model": str(classification_model)[:100], "classification_json": json.dumps(cls_data, ensure_ascii=False), "knowledge_owner_user_id": knowledge_owner_user_id, "knowledge_approver_user_id": knowledge_approver_user_id, "taxonomy_version": str(taxonomy_version)[:100]}
        )
        _r_doc_metadata._apply_upload_meta_to_doc(conn, doc_id, (job[2] if job else None), domain)
        return doc_id
        
    res = conn.execute(
        text(
            """INSERT INTO TaiLieu (
                TenFile, ThuMuc, TrangThaiVector, ReviewStatus, FamilyID, BaseCode,
                VersionNo, VersionLabel, VariantCode, Site, Domain, SecurityLevel,
                FilePath, OwnerDepartment, SourceSystem, ExternalProcessingPolicy,
                ClassificationRationale, ClassificationModel, ClassificationJson, KnowledgeOwnerUserID,
                KnowledgeApproverUserID, TaxonomyVersion, PublicationState, Servable
            )
            OUTPUT INSERTED.DocID 
            VALUES (
                :f, :t, 1, 'pending_review', :fid, :bc, :vn, :vl, :vc, :site,
                :domain, :seclvl, :fp, :owner_department, 'upload', :external_processing_policy,
                :classification_rationale, :classification_model, :classification_json, :knowledge_owner_user_id,
                :knowledge_approver_user_id, :taxonomy_version, 'draft', 0
            )"""
        ),
        {"f": file_name, "t": thu_muc, "fid": f_id, "bc": base_code, "vn": version_no, "vl": version_label, "vc": variant_code, "site": resolved_site, "domain": domain, "seclvl": security_level, "fp": (job[1] if job else None), "owner_department": thu_muc or "CHUNG", "external_processing_policy": external_processing_policy, "classification_rationale": str(classification_rationale)[:1000], "classification_model": str(classification_model)[:100], "classification_json": json.dumps(cls_data, ensure_ascii=False), "knowledge_owner_user_id": knowledge_owner_user_id, "knowledge_approver_user_id": knowledge_approver_user_id, "taxonomy_version": str(taxonomy_version)[:100]},
    )
    row = res.fetchone()
    new_doc_id = row[0] if row else None
    if new_doc_id is not None:
        _r_doc_metadata._apply_upload_meta_to_doc(conn, new_doc_id, (job[2] if job else None), domain)
    return new_doc_id
 
def set_document_departments(conn, doc_id, dept_codes):
    """E1: ghi lai danh sach phong ban duoc chia se cho 1 tai lieu vao bang
    nhieu-nhieu dbo.PhongBanChiaSe (nguon su that). Xoa het dong cu cua doc_id
    roi insert lai tap moi. Chay TRONG transaction cua caller (nhan `conn`).
    dept_codes: list/tuple/set hoac chuoi CSV.
    """
    if doc_id is None:
        return
    codes = _r_catalog._split_csv_tokens(dept_codes)
    conn.execute(text("DELETE FROM dbo.PhongBanChiaSe WHERE DocID = :d"), {"d": doc_id})
    # Perf (GD1): bulk insert thay N+1 (executemany). Giu nguyen tap dong chen.
    if codes:
        conn.execute(
            text("INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode) VALUES (:d, :c)"),
            [{"d": doc_id, "c": code} for code in codes],
        )


def get_document_departments(doc_id):
    """E1: doc danh sach phong ban duoc chia se cua 1 tai lieu tu PhongBanChiaSe."""
    if doc_id is None:
        return []
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DeptCode FROM dbo.PhongBanChiaSe WHERE DocID = :d ORDER BY DeptCode"),
                {"d": doc_id},
            ).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.error(f"get_document_departments loi doc_id={doc_id}: {e}", exc_info=True)
        return []


def update_document_classification(doc_id, domain=None, security_level=None, phong_ban=None):
    """GD5 fix ro ri: dong bo lai Domain/SecurityLevel/PhongBan cho TaiLieu sau khi co
    override tu form va escalation tu sensitive_scanner. Truoc day _get_or_create_doc ghi
    TaiLieu theo ClassificationJson (suy tu folder) nen khi override/escalate muc mat,
    TaiLieu lech voi payload Qdrant -> duong SQL BOM (t.SecurityLevel) co the lo tai lieu mat.
    """
    if doc_id is None:
        return
    _ensure_engine()
    try:
        sets = []
        params = {"d": doc_id}
        if domain is not None:
            sets.append("Domain = :domain")
            params["domain"] = domain
        if security_level is not None:
            sets.append("SecurityLevel = :seclvl")
            params["seclvl"] = security_level
        # E1: PhongBan da chuyen sang bang nhieu-nhieu dbo.PhongBanChiaSe (khong con cot CSV).
        if not sets and phong_ban is None:
            return
        with engine.begin() as conn:
            if sets:
                conn.execute(text("UPDATE TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :d"), params)
            if phong_ban is not None:
                set_document_departments(conn, doc_id, phong_ban)
        _r_semantic_cache._invalidate_semantic_cache("doc.classification")
    except Exception as e:
        logger.error(f"Loi update_document_classification doc_id={doc_id}: {e}", exc_info=True)


def mark_document_ingest_failed(file_name, thu_muc, error_message=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT DocID
                    FROM TaiLieu
                    WHERE TenFile = :f AND ThuMuc = :t
                """),
                {"f": file_name, "t": thu_muc}
            ).fetchone()

            if not row:
                return

            doc_id = row[0]

            conn.execute(text("DELETE FROM TaiLieuKyThuat WHERE DocID = :d"), {"d": doc_id})
            conn.execute(text("DELETE FROM BangKeVatTu WHERE DocID = :d"), {"d": doc_id})
            conn.execute(text("DELETE FROM DocumentPages WHERE DocID = :d"), {"d": doc_id})
            conn.execute(text("DELETE FROM TechnicalAttributes WHERE DocID = :d"), {"d": doc_id})

            conn.execute(
                text("""
                    UPDATE TaiLieu
                    SET LifecycleStatus = 'rejected',
                        ReviewStatus = 'rejected',
                        LyDoTuChoi = :msg,
                        TrangThaiVector = 0
                    WHERE DocID = :d
                """),
                {"d": doc_id, "msg": error_message or "Ingest failed"}
            )
    except Exception as e:
        logger.error(f"Loi mark_document_ingest_failed: {e}", exc_info=True)

def get_document_info(doc_id):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT t.FamilyID, t.BaseCode, t.VersionNo, t.VersionLabel, t.VariantCode, t.VariantGroup, t.LifecycleStatus, t.ReviewStatus, t.IsCurrent, t.IsArchived, t.SupersedesDocID, t.PublicationState, t.Servable, t.PublicationVersion, t.OwnerDepartment, t.SourceSystem, t.ExternalProcessingPolicy, t.KnowledgeOwnerUserID, t.KnowledgeApproverUserID, t.TaxonomyVersion, t.ParentApplicable, t.ParentSection, t.ParentPage, t.ServingEpoch, ISNULL(p.ParentContextEnabled, 1) AS ParentContextEnabled, t.ClassificationJson, t.Title, t.DocNumber FROM TaiLieu t LEFT JOIN dbo.DepartmentDomainProfile p ON p.DeptCode = t.OwnerDepartment AND p.IsActive = 1 WHERE t.DocID = :d"), {"d": doc_id}).fetchone()
            if row:
                try:
                    classification = json.loads(row[25] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    classification = {}
                document_type = ""
                if isinstance(classification, dict):
                    document_type = str(classification.get("document_type") or "").strip()
                return {
                    "family_id": row[0],
                    "base_code": row[1] or "",
                    "version_no": row[2] or 1,
                    "version_label": row[3] or "",
                    "variant_code": row[4] or "default",
                    "variant_group": row[5] or "",
                    "lifecycle_status": row[6] or "draft",
                    "review_status": row[7] or "pending_review",
                    "is_current": bool(row[8]),
                    "is_archived": bool(row[9]),
                    "supersedes_doc_id": row[10],
                    "publication_state": row[11] or "draft",
                    "servable": bool(row[12]),
                    "publication_version": row[13] or 1,
                    "owner_department": row[14] or "",
                    "source_system": row[15] or "upload",
                    "external_processing_policy": row[16] or "all_external",
                    "knowledge_owner_user_id": row[17],
                    "knowledge_approver_user_id": row[18],
                    "taxonomy_version": row[19] or "v1",
                    "parent_applicable": bool(row[20]),
                    "parent_section": row[21] or "",
                    "parent_page": row[22],
                    "serving_epoch": row[23] or 0,
                    "parent_context_enabled": bool(row[24]),
                    "document_type": document_type,
                    "classification_failed": bool(classification.get("classification_failed")) if isinstance(classification, dict) else False,
                    "title": row[26] or "",
                    "doc_number": row[27] or "",
                }
            return {}
    except Exception as e:
        logger.error(f"Loi get_document_info {doc_id}: {e}", exc_info=True)
        return {}

def _normalize_doc_type_label(raw):
    """P2: chuan hoa nhan loai tai lieu ve dang hien thi chuan (tieng Viet co dau)."""
    try:
        from mech_chatbot.db.registry_ports import canonical_label
        label = canonical_label(raw, "vi")
        if label:
            return _sanitize_text(label, 255)
    except Exception:
        pass
    return _sanitize_text(raw, 255) or "Khong ro"


def _prepare_metadata_params(info):
    ma = info.get("ma_doi_tuong", [])
    if not isinstance(ma, list):
        ma = [str(ma)] if ma and str(ma).strip() != "Khong ro" else []
    return {
        "trang_so": _sanitize_int(info.get("trang_so"), 1),  # default=1: so trang ban ve, hop le
        "loai_tai_lieu": _normalize_doc_type_label(info.get("loai_tai_lieu")),
        "ma_doi_tuong": json.dumps(ma, ensure_ascii=False),
        "ten_sp": _sanitize_text(info.get("ten_tai_lieu"), 500),
        "cong_doan": _sanitize_text(info.get("cong_doan"), 255),
        "vat_lieu": _sanitize_text(info.get("vat_lieu"), 255),
        "so_luong": _sanitize_int(info.get("so_luong"), None),
        "nguoi_lap": _sanitize_text(info.get("nguoi_lap"), 255),
        "ngay_ve": _sanitize_date(info.get("ngay_ve")),
        "dung_sai_day": _sanitize_text(info.get("dung_sai_day"), 100),
        "dung_sai_khac": _sanitize_text(info.get("dung_sai_khac"), 100),
        "kich_thuoc": _sanitize_text(info.get("kich_thuoc"), 100),
        "hdcv": _sanitize_text(info.get("hdcv")),
        "yckt": _sanitize_text(info.get("yckt")),
    }


def delete_document_completely(doc_id, reviewer="System"):
    """Xoa VINH VIEN toan bo du lieu cua 1 tai lieu (safe 3-buoc).

    Quy trinh an toan de tranh tai lieu 'ma':
      1. SQL soft-delete: danh dau LifecycleStatus='deleting', IsCurrent=0
         (tai lieu bi an khoi RAG nhung SQL van con — co the rollback)
      2. Xoa vector Qdrant
         - Neu Qdrant loi -> rollback buoc 1 (khoi phuc LifecycleStatus + IsCurrent)
      3. SQL hard-delete (xoa han row)
         - Neu loi o buoc nay: vector da mat, SQL con trang thai 'deleting'
           -> khong xuat hien trong RAG, co the retry delete_document_completely() an toan.
    """
    _ensure_engine()

    # Doc thong tin + trang thai hien tai (can de rollback)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT TenFile, ThuMuc, LifecycleStatus FROM TaiLieu WHERE DocID = :id"),
            {"id": doc_id}
        ).fetchone()

    if not row:
        logger.warning(f"delete_document_completely: khong tim thay DocID {doc_id}")
        return False

    ten_file, thu_muc, prev_status = row[0], row[1], row[2]

    # ------------------------------------------------------------------
    # Buoc 1: SQL soft-delete — danh dau 'deleting'
    # ------------------------------------------------------------------
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE TaiLieu SET LifecycleStatus = 'deleting', IsCurrent = 0 WHERE DocID = :id"),
                {"id": doc_id}
            )
        logger.info(f"[delete] 1/3 OK — soft-delete DocID {doc_id} (prev: {prev_status})")
    except Exception as e:
        logger.error(f"[delete] 1/3 THAT BAI (soft-delete) DocID {doc_id}: {e}", exc_info=True)
        return False

    # ------------------------------------------------------------------
    # Buoc 2: Xoa vector Qdrant
    # Neu loi -> rollback buoc 1
    # ------------------------------------------------------------------
    try:
        from qdrant_client import models
        client = _r_qdrant._get_qdrant_client()
        client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id))]
                )
            )
        )
        logger.info(f"[delete] 2/3 OK — da xoa Qdrant points cua DocID {doc_id}")
    except Exception as e:
        logger.error(
            f"[delete] 2/3 THAT BAI (Qdrant) DocID {doc_id}: {e}. Dang rollback soft-delete.",
            exc_info=True
        )
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE TaiLieu SET LifecycleStatus = :s, IsCurrent = 1 WHERE DocID = :id"),
                    {"s": prev_status or "published", "id": doc_id}
                )
            logger.info(f"[delete] Rollback OK — DocID {doc_id} khoi phuc ve '{prev_status}'")
        except Exception as rb_err:
            logger.error(
                f"[delete] ROLLBACK THAT BAI DocID {doc_id}: {rb_err}. "
                "Tai lieu bi ket o trang thai 'deleting' — can xu ly thu cong!",
                exc_info=True
            )
        return False

    # ------------------------------------------------------------------
    # Buoc 3: SQL hard-delete (bo sung xoa PNG + DocumentAttributes)
    # Neu loi: vector da mat, SQL con trang thai 'deleting' (an khoi RAG)
    # -> co the retry bang cach goi lai ham nay
    # ------------------------------------------------------------------
    img_rows = []
    try:
        with engine.begin() as conn:
            # (a) Lay duong dan anh PNG de xoa file vat ly sau
            img_rows = conn.execute(
                text("SELECT ImagePath FROM DocumentPages WHERE DocID = :id AND ImagePath IS NOT NULL"),
                {"id": doc_id},
            ).fetchall()

            conn.execute(text("DELETE FROM DocumentPages       WHERE DocID = :id"), {"id": doc_id})
            conn.execute(text("DELETE FROM TechnicalAttributes WHERE DocID = :id"), {"id": doc_id})
            conn.execute(text("DELETE FROM DocumentAttributes  WHERE DocID = :id"), {"id": doc_id})  # (b) truoc day khong xoa
            # TaiLieuKyThuat + BangKeVatTu + PhongBanChiaSe + DocQualityScore tu xoa theo CASCADE
            conn.execute(text("DELETE FROM TaiLieu             WHERE DocID = :id"), {"id": doc_id})
            if ten_file and thu_muc:
                conn.execute(
                    text("DELETE FROM dbo.IngestionJobs WHERE TenFile = :f AND ThuMuc = :t"),
                    {"f": ten_file, "t": thu_muc},
                )
        logger.info(f"[delete] 3/3 OK — hard-delete SQL DocID {doc_id} ({ten_file})")
    except Exception as e:
        logger.error(
            f"[delete] 3/3 THAT BAI (SQL hard-delete) DocID {doc_id}: {e}. "
            "Vector Qdrant DA XOA. Ban ghi SQL con trang thai 'deleting'. "
            f"Retry: delete_document_completely({doc_id})",
            exc_info=True
        )
        return False

    # (c) Xoa file PNG vat ly (ngoai transaction, best-effort)
    for r in img_rows:
        p = r[0]
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception as _e:
            logger.warning(f"[delete] khong xoa duoc anh {p}: {_e}")

    # (d) Null hoa / don con tro mo coi o FeedbackReview/GoldenAnswer/AnswerSource/DocQualityScore
    try:
        _r_feedback.cleanup_dangling_records()
    except Exception as _e:
        logger.warning(f"[delete] cleanup_dangling_records loi: {_e}")

    # (e) Clear semantic cache lien quan (don gian nhat: clear all)
    try:
        _r_semantic_cache.sc_clear_all()
    except Exception as _e:
        logger.warning(f"[delete] sc_clear_all loi: {_e}")

    _r_audit.write_audit_log(reviewer, "delete_document", "TaiLieu", doc_id, {"ten_file": ten_file, "thu_muc": thu_muc})
    return True

def get_doc(doc_id):
    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT DocID, FamilyID, BaseCode, VersionNo, VariantCode, LifecycleStatus, IsCurrent FROM TaiLieu WHERE DocID = :d"), {"d": doc_id}).fetchone()
        if row:
            return type('Doc', (object,), {
                'DocID': row[0], 'FamilyID': row[1], 'BaseCode': row[2], 
                'VersionNo': row[3], 'VariantCode': row[4],
                'LifecycleStatus': row[5], 'IsCurrent': bool(row[6])
            })
    return None

def find_current_docs(base_code, variant_code=None):
    _ensure_engine()
    query = "SELECT DocID FROM TaiLieu WHERE BaseCode = :b AND IsCurrent = 1 AND LifecycleStatus = 'published'"
    params = {"b": base_code}
    if variant_code:
        query += " AND VariantCode = :v"
        params["v"] = variant_code
        
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        return [get_doc(r[0]) for r in rows]
