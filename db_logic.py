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
    except Exception as e:
        logger.error(f"Loi khi cap nhat danh gia chat: {e}", exc_info=True)
 
# ==========================================
# DOCUMENT METADATA (Fix #1: tach reset / insert)
# ==========================================
def _get_or_create_doc(conn, file_name, thu_muc):
    row = conn.execute(
        text("SELECT DocID FROM TaiLieu WHERE TenFile = :f AND ThuMuc = :t"),
        {"f": file_name, "t": thu_muc},
    ).fetchone()
    if row:
        return row[0]
    res = conn.execute(
        text(
            "INSERT INTO TaiLieu (TenFile, ThuMuc, TrangThaiVector) "
            "OUTPUT INSERTED.DocID VALUES (:f, :t, 1)"
        ),
        {"f": file_name, "t": thu_muc},
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
            return doc_id
    except Exception as e:
        logger.error(f"Loi reset metadata {file_name}: {e}", exc_info=True)
        return None
 
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