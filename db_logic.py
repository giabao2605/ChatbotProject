import os
import re
import json
import urllib.parse  # Fix: phai import urllib.parse tuong minh (import urllib khong du)
from datetime import datetime
 
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from logger_config import logger
 
load_dotenv()
 
SQL_SERVER = os.getenv("SQL_SERVER", r"localhost\SQLEXPRESS")
SQL_DATABASE = os.getenv("SQL_DATABASE", "Mech_Chatbot_DB")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
 
params = urllib.parse.quote_plus(
    f"DRIVER={SQL_DRIVER};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};Trusted_Connection=yes;"
)
 
try:
    engine = create_engine(
        f"mssql+pyodbc:///?odbc_connect={params}",
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    logger.info("Da khoi tao SQLAlchemy Engine thanh cong.")
except Exception as e:
    logger.error(f"Loi khoi tao SQLAlchemy Engine: {e}", exc_info=True)
    engine = None  # Fix #1: gan tuong minh de cac ham co the kiem tra
 
def _ensure_engine():
    """Fix #1: Bao loi ro rang thay vi NameError khi engine khong khoi tao duoc."""
    if engine is None:
        raise RuntimeError(
            "SQLAlchemy Engine chua san sang. Kiem tra connection string / ODBC driver / SQL Server."
        )
 
# ==========================================
# SANITIZATION HELPERS (dua len module-level)
# ==========================================
def _sanitize_text(val, max_len=None):
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("khong ro", "khong ro", "none", "null", "n/a", ""):
        return None
    if max_len and len(s) > max_len:
        s = s[:max_len]  # Fix: cat chuoi de tranh loi "String or binary data would be truncated"
    return s
 
def _sanitize_int(val, default=1):
    try:
        nums = re.findall(r"\d+", str(val))
        return int(nums[0]) if nums else default
    except Exception:
        return default
        
import unicodedata

def normalize_base_code(code):
    if not code:
        return ""
    code = str(code).lower().strip()
    code = ''.join(c for c in unicodedata.normalize('NFD', code) if unicodedata.category(c) != 'Mn')
    code = code.replace(".pdf", "").replace(".docx", "").replace(".xlsx", "")
    code = re.sub(r"[_\s]+", "-", code)
    return code
 
def _sanitize_date(val):
    """Fix: parse chat che; that bai tra None de tranh loi conversion cot DATE."""
    s = _sanitize_text(val)
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None
 
# FIX C6: gioi han kich thuoc input chat (chong payload GB lam sap DB). Co the chinh qua env.
MAX_USER_MSG_LEN = int(os.getenv("MAX_USER_MSG_LEN", "20000"))
MAX_BOT_MSG_LEN = int(os.getenv("MAX_BOT_MSG_LEN", "200000"))
 
def _cap_len(val, max_len):
    """C6: chi cat bot khi vuot gioi han, KHONG doi gia tri (khac _sanitize_text -> tranh bien 'null'/'none' thanh None)."""
    if val is None:
        return None
    s = str(val)
    if len(s) > max_len:
        logger.warning(f"Input vuot {max_len} ky tu, da cat bot de chong payload qua lon.")
        return s[:max_len]
    return s
 
# ==========================================
# CHAT HISTORY
# ==========================================
def save_chat_history(session_id, user_msg, bot_msg, image_path=None, ref_images=None):
    _ensure_engine()
    try:
        # FIX C5: serialize danh sach duong dan ban ve can cu thanh JSON string de luu DB
        ref_images_json = json.dumps(ref_images or [], ensure_ascii=False)
        # FIX C6: gioi han do dai input truoc khi luu (chong payload qua lon lam sap DB)
        session_id = _cap_len(session_id, 100)
        user_msg = _cap_len(user_msg, MAX_USER_MSG_LEN)
        bot_msg = _cap_len(bot_msg, MAX_BOT_MSG_LEN)
        image_path = _cap_len(image_path, 500)
        with engine.begin() as conn:
            query = text(
                """
                INSERT INTO LichSuChat (SessionID, CauHoi_User, TraLoi_Bot, HinhAnhUpload, RefImages)
                OUTPUT INSERTED.ChatID
                VALUES (:session_id, :user_msg, :bot_msg, :image_path, :ref_images)
                """
            )
            result = conn.execute(
                query,
                {
                    "session_id": session_id,
                    "user_msg": user_msg,
                    "bot_msg": bot_msg,
                    "image_path": image_path,
                    "ref_images": ref_images_json,
                },
            )
            row = result.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"Loi khi luu lich su chat: {e}", exc_info=True)
        return None
 
def get_all_sessions():
    """Fix #2: Lay cau hoi DAU TIEN theo thoi gian (khong dung MIN tren text)."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT SessionID, ThoiGianBatDau, CauHoiDauTien FROM (
                    SELECT SessionID,
                           CauHoi_User AS CauHoiDauTien,
                           ThoiGian AS ThoiGianBatDau,
                           ROW_NUMBER() OVER (PARTITION BY SessionID ORDER BY ThoiGian ASC, ChatID ASC) AS rn
                    FROM LichSuChat
                ) t
                WHERE rn = 1
                ORDER BY ThoiGianBatDau DESC
                """
            )
            result = conn.execute(query)
            sessions = result.fetchall()
            out = []
            for row in sessions:
                cau_hoi = row[2] or ""
                if len(cau_hoi) > 30:
                    label = cau_hoi[:30] + "..."
                else:
                    label = cau_hoi or "(Khong co tieu de)"
                out.append({"session_id": row[0], "thoi_gian": row[1], "cau_hoi": label})
            return out
    except Exception as e:
        logger.error(f"Loi khi lay danh sach session: {e}", exc_info=True)
        return []
 
def get_chat_history(session_id):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            query = text(
                """
                SELECT ChatID, CauHoi_User, TraLoi_Bot, HinhAnhUpload, DanhGia, RefImages
                FROM LichSuChat
                WHERE SessionID = :session_id
                ORDER BY ThoiGian ASC, ChatID ASC
                """
            )
            rows = conn.execute(query, {"session_id": session_id}).fetchall()
            history = []
            for row in rows:
                # FIX C5: doc lai ref_images tu DB (JSON string -> list duong dan)
                try:
                    ref_images = json.loads(row[5]) if row[5] else []
                except (json.JSONDecodeError, TypeError):
                    ref_images = []
                history.append({"role": "user", "content": row[1], "image": row[3]})
                history.append(
                    {
                        "role": "assistant",
                        "content": row[2],
                        "chat_id": row[0],
                        "danh_gia": row[4],
                        "ref_images": ref_images,
                    }
                )
            return history
    except Exception as e:
        logger.error(f"Loi khi lay lich su chat: {e}", exc_info=True)
        return []
 
def clear_chat_history(session_id):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM LichSuChat WHERE SessionID = :session_id"), {"session_id": session_id})
    except Exception as e:
        logger.error(f"Loi khi xoa lich su chat: {e}", exc_info=True)
 
def update_chat_feedback(chat_id, danh_gia):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE LichSuChat SET DanhGia = :danh_gia WHERE ChatID = :chat_id"),
                {"danh_gia": danh_gia, "chat_id": chat_id},
            )
            if danh_gia == -1:
                row = conn.execute(
                    text("SELECT CauHoi_User, TraLoi_Bot FROM LichSuChat WHERE ChatID = :chat_id"),
                    {"chat_id": chat_id}
                ).fetchone()
                if row:
                    # Check if already in FeedbackReview
                    exists = conn.execute(text("SELECT 1 FROM FeedbackReview WHERE ChatID = :c"), {"c": chat_id}).fetchone()
                    if not exists:
                        conn.execute(
                            text("INSERT INTO FeedbackReview (ChatID, Question, BotAnswer) VALUES (:c, :q, :b)"),
                            {"c": chat_id, "q": row[0], "b": row[1]}
                        )
    except Exception as e:
        logger.error(f"Loi khi cap nhat danh gia chat: {e}", exc_info=True)
 
# ==========================================
# DOCUMENT METADATA (Fix #1: tach reset / insert)
# ==========================================
def _get_or_create_doc(conn, file_name, thu_muc):
    # Fetch classification json tu IngestionJobs (neu co) de update metadata
    job = conn.execute(
        text("SELECT TOP 1 ClassificationJson FROM IngestionJobs WHERE TenFile = :f AND ThuMuc = :t ORDER BY CreatedAt DESC"),
        {"f": file_name, "t": thu_muc}
    ).fetchone()
    
    cls_data = {}
    if job and job[0]:
        try:
            import json
            cls_data = json.loads(job[0])
        except:
            pass
            
    base_code = cls_data.get("base_code")
    base_code = normalize_base_code(base_code) if base_code else None
    version_label = cls_data.get("version_label")
    version_no = cls_data.get("version_no", 1)
    variant_code = cls_data.get("variant_code") or "default"
    
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
                FamilyID = :fid, BaseCode = :bc, VersionNo = :vn, VersionLabel = :vl, VariantCode = :vc
                WHERE DocID = :d"""),
            {"d": doc_id, "fid": f_id, "bc": base_code, "vn": version_no, "vl": version_label, "vc": variant_code}
        )
        return doc_id
        
    res = conn.execute(
        text(
            """INSERT INTO TaiLieu (TenFile, ThuMuc, TrangThaiVector, ReviewStatus, FamilyID, BaseCode, VersionNo, VersionLabel, VariantCode) 
            OUTPUT INSERTED.DocID 
            VALUES (:f, :t, 1, 'pending_review', :fid, :bc, :vn, :vl, :vc)"""
        ),
        {"f": file_name, "t": thu_muc, "fid": f_id, "bc": base_code, "vn": version_no, "vl": version_label, "vc": variant_code},
    )
    row = res.fetchone()
    return row[0] if row else None
 
def reset_document_metadata(file_name, thu_muc):
    """Fix #1: GOI MOT LAN truoc khi nap file. Xoa metadata cu, tra ve DocID dung chung."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            doc_id = _get_or_create_doc(conn, file_name, thu_muc)
            if doc_id is not None:
                conn.execute(text("DELETE FROM TaiLieuKyThuat WHERE DocID = :d"), {"d": doc_id})
                conn.execute(text("DELETE FROM BangKeVatTu WHERE DocID = :d"), {"d": doc_id})
            return doc_id
    except Exception as e:
        logger.error(f"Loi reset metadata {file_name}: {e}", exc_info=True)
        if isinstance(e, ValueError) and "published" in str(e):
            raise e
        return None

def get_document_info(doc_id):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT FamilyID, BaseCode, VersionNo, VersionLabel, VariantCode, VariantGroup, LifecycleStatus, ReviewStatus, IsCurrent, IsArchived, SupersedesDocID FROM TaiLieu WHERE DocID = :d"), {"d": doc_id}).fetchone()
            if row:
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
                }
            return {}
    except Exception as e:
        logger.error(f"Loi get_document_info {doc_id}: {e}", exc_info=True)
        return {}

def _prepare_metadata_params(info):
    ma = info.get("ma_doi_tuong", [])
    if not isinstance(ma, list):
        ma = [str(ma)] if ma and str(ma).strip() != "Khong ro" else []
    return {
        "trang_so": _sanitize_int(info.get("trang_so"), 1),
        "loai_tai_lieu": _sanitize_text(info.get("loai_tai_lieu"), 255) or "Khong ro",
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
 
def save_page_metadata(file_name, thu_muc, info, doc_id=None):
    """Fix #1: Chi INSERT 1 dong cho 1 trang. KHONG xoa du lieu trang khac."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            if doc_id is None:
                doc_id = _get_or_create_doc(conn, file_name, thu_muc)
            p = _prepare_metadata_params(info)
            p["doc_id"] = doc_id
            conn.execute(
                text(
                    """
                    INSERT INTO TaiLieuKyThuat (
                        DocID, TrangSo, LoaiTaiLieu, MaDoiTuong, TenSanPham, CongDoan,
                        VatLieu, SoLuong, NguoiLap, NgayVe, DungSaiDay, DungSaiKhac,
                        KichThuocTongThe, HDCV, YCKT
                    ) VALUES (
                        :doc_id, :trang_so, :loai_tai_lieu, :ma_doi_tuong, :ten_sp, :cong_doan,
                        :vat_lieu, :so_luong, :nguoi_lap, :ngay_ve, :dung_sai_day, :dung_sai_khac,
                        :kich_thuoc, :hdcv, :yckt
                    )
                    """
                ),
                p,
            )
            return doc_id
    except Exception as e:
        logger.error(
            f"Loi save_page_metadata {file_name} trang {info.get('trang_so')}: {e}",
            exc_info=True,
        )
        return None
 
def save_document_metadata(file_name, thu_muc, info):
    """Tuong thich nguoc cho file 1 trang (non-PDF): reset + insert mot lan."""
    doc_id = reset_document_metadata(file_name, thu_muc)
    return save_page_metadata(file_name, thu_muc, info, doc_id=doc_id)

import re

def normalize_material_name(raw):
    if not raw:
        return None

    s = str(raw).strip().lower()
    s = s.replace("inox", "stainless steel")
    s = s.replace("sus304", "sus 304")
    s = s.replace("ss304", "sus 304")
    s = re.sub(r"\s+", " ", s)

    return s

def save_bom_records(doc_id, trang_so, records):
    """Luu danh sach cac vat tu cua bang ke vao SQL"""
    if not doc_id or not records:
        return
    _ensure_engine()
    try:
        with engine.begin() as conn:
            for rec in records:
                conn.execute(
                    text("""
                        INSERT INTO BangKeVatTu (DocID, TrangSo, MaHang, TenVatTu, VatLieu, NormalizedMaterial, SoLuong, GhiChu, Unit, Confidence, RawRowJson, SourceTableIndex)
                        VALUES (:doc_id, :trang_so, :ma_hang, :ten, :vat_lieu, :normalized_material, :sl, :ghi_chu, :unit, :conf, :raw, :idx)
                    """),
                    {
                        "doc_id": doc_id,
                        "trang_so": trang_so,
                        "ma_hang": _sanitize_text(rec.get("ma_hang"), 255),
                        "ten": _sanitize_text(rec.get("ten_vat_tu"), 500),
                        "vat_lieu": _sanitize_text(rec.get("vat_lieu"), 255),
                        "normalized_material": _sanitize_text(normalize_material_name(rec.get("vat_lieu")), 255),
                        "sl": _sanitize_int(rec.get("so_luong"), None),
                        "ghi_chu": _sanitize_text(rec.get("ghi_chu"), 4000),
                        "unit": _sanitize_text(rec.get("don_vi"), 50),
                        "conf": rec.get("confidence", None),
                        "raw": rec.get("raw_row_json", None),
                        "idx": rec.get("source_table_index", None)
                    }
                )
    except Exception as e:
        logger.error(f"Loi save_bom_records cho doc_id {doc_id}, trang {trang_so}: {e}", exc_info=True)

def search_bom_by_code(ma_hang_list, version_policy="current_only", detected_versions=None, user_department=None, user_roles=None):
    """Tim kiem bang ke vat tu tren SQL theo ma hang hoac ma doi tuong (parent assembly)"""
    if not ma_hang_list:
        return []
    _ensure_engine()
    try:
        with engine.connect() as conn:
            # Tao dieu kien OR cho tung ma bang EXISTS de tranh bo sot khi khac trang
            conditions = []
            for i in range(len(ma_hang_list)):
                conditions.append(f"""
                (
                    b.MaHang LIKE :m{i} 
                    OR EXISTS (
                        SELECT 1 
                        FROM TaiLieuKyThuat tk 
                        WHERE tk.DocID = b.DocID 
                        AND tk.MaDoiTuong LIKE :m{i}
                    )
                )
                """)
            
            filter_sql = "1=1"
            if version_policy in ["current_only", "all_current_variants"]:
                filter_sql += " AND t.LifecycleStatus = 'published' AND t.ReviewStatus = 'approved' AND t.IsCurrent = 1"
            elif version_policy == "specific_version":
                filter_sql += " AND t.LifecycleStatus IN ('published', 'archived', 'superseded') AND t.ReviewStatus = 'approved'"
                if detected_versions:
                    filter_sql += f" AND t.VersionNo = {int(detected_versions[0])}"
            elif version_policy == "compare_versions":
                filter_sql += " AND t.LifecycleStatus IN ('published', 'archived', 'superseded') AND t.ReviewStatus = 'approved'"
                if detected_versions:
                    vers_str = ",".join(str(int(v)) for v in detected_versions)
                    filter_sql += f" AND t.VersionNo IN ({vers_str})"
            else:
                filter_sql += " AND t.LifecycleStatus = 'published' AND t.ReviewStatus = 'approved' AND t.IsCurrent = 1"
                
            # RBAC
            if not user_roles or "admin" not in user_roles:
                filter_sql += " AND (t.ThuMuc = :dept OR t.ThuMuc = 'CHUNG')"
            
            query = text(f"""
                SELECT DISTINCT b.MaHang, b.TenVatTu, b.VatLieu, b.SoLuong, b.GhiChu, t.TenFile, t.VersionNo 
                FROM BangKeVatTu b
                JOIN TaiLieu t ON b.DocID = t.DocID
                WHERE {filter_sql} AND (
                    {" OR ".join(conditions)}
                )
            """)
            params = {f"m{i}": f"%{m}%" for i, m in enumerate(ma_hang_list)}
            if not user_roles or "admin" not in user_roles:
                params["dept"] = user_department
                
            result = conn.execute(query, params).fetchall()
            return result
    except Exception as e:
        logger.error(f"Loi search_bom_by_code: {e}", exc_info=True)
        return []

# ==========================================
# BACKGROUND JOBS
# ==========================================
def create_ingestion_job(file_name, file_path, thu_muc, uploaded_by=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO IngestionJobs (TenFile, FilePath, ThuMuc, Status, UploadedBy)
                    OUTPUT INSERTED.JobID
                    VALUES (:f, :p, :t, 'pending', :u)
                    """
                ),
                {"f": file_name, "p": file_path, "t": thu_muc, "u": uploaded_by}
            )
            row = result.fetchone()
            job_id = row[0] if row else None
            if job_id:
                write_audit_log(uploaded_by or "System", "upload", "IngestionJobs", job_id, {"file_name": file_name, "thu_muc": thu_muc})
            return job_id
    except Exception as e:
        logger.error(f"Loi tao IngestionJob: {e}", exc_info=True)
        return None

def update_ingestion_job(job_id, status, error_message=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE IngestionJobs
                    SET Status = :s, ErrorMessage = :e, UpdatedAt = GETDATE()
                    WHERE JobID = :id
                    """
                ),
                {"s": status, "e": error_message, "id": job_id}
            )
    except Exception as e:
        logger.error(f"Loi cap nhat IngestionJob {job_id}: {e}", exc_info=True)

def get_pending_job(worker_id="worker-1"):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            # Dung UPDATE voi OUTPUT cho atomic picking (chong race condition neu co nhieu worker)
            result = conn.execute(
                text(
                    """
                    WITH CTE AS (
                        SELECT TOP 1 JobID, Status
                        FROM IngestionJobs
                        WHERE (
                            (Status = 'pending' AND (LockedAt IS NULL OR LockedAt < DATEADD(minute, -15, GETDATE())))
                            OR (Status IN ('classifying', 'extracting', 'embedding') AND LockedAt < DATEADD(minute, -15, GETDATE()))
                          )
                          AND ISNULL(RetryCount, 0) < ISNULL(MaxRetry, 3)
                        ORDER BY CreatedAt ASC
                    )
                    UPDATE CTE
                    SET Status = 'classifying',
                        LockedBy = :worker_id,
                        LockedAt = GETDATE(),
                        ProgressPercent = 5,
                        UpdatedAt = GETDATE()
                    OUTPUT inserted.JobID, inserted.TenFile, inserted.FilePath, inserted.ThuMuc;
                    """
                ),
                {"worker_id": worker_id}
            )
            row = result.fetchone()
            if row:
                conn.commit()
                return {"job_id": row[0], "ten_file": row[1], "file_path": row[2], "thu_muc": row[3]}
            return None
    except Exception as e:
        logger.error(f"Loi lay pending job: {e}", exc_info=True)
        return None

def mark_job_failed(job_id, error_message):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE IngestionJobs
                SET RetryCount = ISNULL(RetryCount, 0) + 1,
                    Status = CASE 
                        WHEN ISNULL(RetryCount, 0) + 1 >= ISNULL(MaxRetry, 3)
                        THEN 'failed'
                        ELSE 'pending'
                    END,
                    ErrorMessage = :e,
                    LockedBy = NULL,
                    LockedAt = NULL,
                    UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {"id": job_id, "e": error_message})
    except Exception as e:
        logger.error(f"Loi danh dau job fail {job_id}: {e}", exc_info=True)

# ==========================================
# PHAN QUAN LY VONG DOI & REVIEW (PHASE 3)
# ==========================================

def write_audit_log(username, action, entity_type=None, entity_id=None, details=None, user_id=None):
    _ensure_engine()
    import json
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO AuditLog (UserID, Username, Action, EntityType, EntityID, Details)
                VALUES (:uid, :username, :action, :etype, :eid, :details)
            """), {
                "uid": user_id,
                "username": username,
                "action": action,
                "etype": entity_type,
                "eid": entity_id,
                "details": json.dumps(details or {}, ensure_ascii=False)
            })
    except Exception as e:
        logger.error(f"Loi write_audit_log: {e}", exc_info=True)

def update_qdrant_metadata(doc_id, metadata_updates):
    from rag_logic import client
    from qdrant_client import models
    try:
        scroll_res = client.scroll(
            collection_name="TaiLieuKyThuat_v2",
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id))]
            ),
            limit=10000,
            with_payload=True
        )
        points, _ = scroll_res
        if not points:
            return
            
        for p in points:
            meta = p.payload.get("metadata", {}) if p.payload else {}
            meta.update(metadata_updates)
            
            client.set_payload(
                collection_name="TaiLieuKyThuat_v2",
                payload={"metadata": meta},
                points=[p.id]
            )
            
        logger.info(f"Updated Qdrant payload cho {len(points)} chunks cua DocID {doc_id}")
    except Exception as e:
        logger.error(f"Loi update Qdrant payload cho DocID {doc_id}: {e}")

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

def publish_as_new_version(doc_id, reviewer="System"):
    doc = get_doc(doc_id)
    if not doc: return False
    
    old_docs = find_current_docs(base_code=doc.BaseCode, variant_code=doc.VariantCode)
    old_id = old_docs[0].DocID if old_docs else None
    
    with engine.begin() as conn:
        for old in old_docs:
            conn.execute(text("""
                UPDATE TaiLieu SET IsCurrent = 0, IsArchived = 1, LifecycleStatus = 'superseded', ArchivedAt = GETDATE() WHERE DocID = :id
            """), {"id": old.DocID})
            update_qdrant_metadata(old.DocID, {
                "is_current": False,
                "is_archived": True,
                "lifecycle_status": "superseded"
            })
            
        conn.execute(text("""
            UPDATE TaiLieu SET IsCurrent = 1, IsArchived = 0, LifecycleStatus = 'published', ReviewStatus = 'approved',
                PublishedAt = GETDATE(), NgayDuyet = GETDATE(), NguoiDuyet = :rev, ReviewedBy = :rev,
                SupersedesDocID = :old_id, TrangThai = 'published'
            WHERE DocID = :id
        """), {"id": doc.DocID, "rev": reviewer, "old_id": old_id})
        
        update_qdrant_metadata(doc.DocID, {
            "doc_status": "published",
            "lifecycle_status": "published",
            "review_status": "approved",
            "is_current": True,
            "is_archived": False,
            "published_at": datetime.now().isoformat(),
            "supersedes_doc_id": old_id
        })
        
    write_audit_log(reviewer, "publish_new_version", "TaiLieu", doc.DocID, {"base_code": doc.BaseCode, "version": doc.VersionNo, "superseded": old_id})
    return True

def publish_as_new_variant(doc_id, reviewer="System"):
    doc = get_doc(doc_id)
    if not doc: return False
    
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET IsCurrent = 1, IsArchived = 0, LifecycleStatus = 'published', ReviewStatus = 'approved',
                PublishedAt = GETDATE(), NgayDuyet = GETDATE(), NguoiDuyet = :rev, ReviewedBy = :rev, TrangThai = 'published'
            WHERE DocID = :id
        """), {"id": doc.DocID, "rev": reviewer})
        
        update_qdrant_metadata(doc.DocID, {
            "doc_status": "published",
            "lifecycle_status": "published",
            "review_status": "approved",
            "is_current": True,
            "is_archived": False,
            "published_at": datetime.now().isoformat()
        })
        
    write_audit_log(reviewer, "publish_variant", "TaiLieu", doc.DocID, {"base_code": doc.BaseCode, "variant": doc.VariantCode})
    return True

def publish_as_standalone(doc_id, reviewer="System"):
    return publish_as_new_variant(doc_id, reviewer=reviewer)

def reject_document(doc_id, reviewer="System"):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET LifecycleStatus = 'rejected', ReviewStatus = 'rejected', NguoiDuyet = :rev, ReviewedBy = :rev
            WHERE DocID = :id
        """), {"id": doc_id, "rev": reviewer})
        
        update_qdrant_metadata(doc_id, {
            "lifecycle_status": "rejected",
            "review_status": "rejected"
        })
        
    write_audit_log(reviewer, "reject_document", "TaiLieu", doc_id, {})
    return True

def archive_document(doc_id, reviewer="System"):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET IsCurrent = 0, IsArchived = 1, LifecycleStatus = 'archived', ArchivedAt = GETDATE()
            WHERE DocID = :id
        """), {"id": doc_id})
        
        update_qdrant_metadata(doc_id, {
            "is_current": False,
            "is_archived": True,
            "lifecycle_status": "archived"
        })
        
    write_audit_log(reviewer, "archive_document", "TaiLieu", doc_id, {})
    return True

def rollback_to_version(base_code, version_no, variant_code="default", reviewer="System"):
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
            return rollback_to_version_by_family(row[0], version_no, variant_code, reviewer)
    except Exception as e:
        logger.error(f"Loi wrapper rollback: {e}")
        return False

def rollback_to_version_by_family(family_id, version_no, variant_code="default", reviewer="System"):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            current_rows = conn.execute(text("""
                SELECT DocID FROM TaiLieu
                WHERE FamilyID = :fid
                  AND VariantCode = :vc
                  AND IsCurrent = 1
                  AND LifecycleStatus = 'published'
            """), {
                "fid": family_id,
                "vc": variant_code
            }).fetchall()

            for row in current_rows:
                old_doc_id = row[0]
                conn.execute(text("""
                    UPDATE TaiLieu
                    SET IsCurrent = 0,
                        IsArchived = 1,
                        LifecycleStatus = 'superseded',
                        ArchivedAt = GETDATE()
                    WHERE DocID = :id
                """), {"id": old_doc_id})

                update_qdrant_metadata(old_doc_id, {
                    "is_current": False,
                    "is_archived": True,
                    "lifecycle_status": "superseded"
                })

            target = conn.execute(text("""
                SELECT DocID FROM TaiLieu
                WHERE FamilyID = :fid
                  AND VariantCode = :vc
                  AND VersionNo = :vn
            """), {
                "fid": family_id,
                "vc": variant_code,
                "vn": version_no
            }).fetchone()

            if not target:
                return False

            target_doc_id = target[0]

            conn.execute(text("""
                UPDATE TaiLieu
                SET IsCurrent = 1,
                    IsArchived = 0,
                    LifecycleStatus = 'published',
                    ReviewStatus = 'approved',
                    ReviewedBy = :rev,
                    NguoiDuyet = :rev,
                    PublishedAt = GETDATE()
                WHERE DocID = :id
            """), {
                "id": target_doc_id,
                "rev": reviewer
            })

            update_qdrant_metadata(target_doc_id, {
                "is_current": True,
                "is_archived": False,
                "lifecycle_status": "published",
                "review_status": "approved"
            })

            write_audit_log(reviewer, "rollback", "TaiLieu", target_doc_id, {"family_id": family_id, "target_version": version_no})
            return True

    except Exception as e:
        logger.error(f"Loi rollback_to_version_by_family: {e}", exc_info=True)
        return False
