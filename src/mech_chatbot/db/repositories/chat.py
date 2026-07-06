"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import json
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import MAX_BOT_MSG_LEN, MAX_USER_MSG_LEN, _cap_len

__all__ = [
    'clear_chat_history',
    'get_all_sessions',
    'get_chat_history',
    'save_answer_sources',
    'save_chat_history',
]

 
 
# ==========================================
# CHAT HISTORY
# ==========================================

def save_chat_history(session_id, user_msg, bot_msg, image_path=None, ref_images=None, username=None):
    _ensure_engine()
    try:
        ref_images_json = json.dumps(ref_images or [], ensure_ascii=False)
        session_id  = _cap_len(session_id, 100)
        user_msg    = _cap_len(user_msg, MAX_USER_MSG_LEN)
        bot_msg     = _cap_len(bot_msg, MAX_BOT_MSG_LEN)
        image_path  = _cap_len(image_path, 500)
        username    = _cap_len(username, 255)

        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO LichSuChat
                        (SessionID, CauHoi_User, TraLoi_Bot, HinhAnhUpload, RefImages, Username)
                    OUTPUT INSERTED.ChatID
                    VALUES (:session_id, :user_msg, :bot_msg, :image_path, :ref_images, :username)
                """),
                {
                    "session_id": session_id,
                    "user_msg":   user_msg,
                    "bot_msg":    bot_msg,
                    "image_path": image_path,
                    "ref_images": ref_images_json,
                    "username":   username,
                },
            )
            row = result.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"Loi khi luu lich su chat: {e}", exc_info=True)
        return None


def save_answer_sources(chat_id, retrieved_docs):
    """P3-1: Luu cac tai lieu/chunk RAG da dung de sinh cau tra loi (truy vet nguon)."""
    if not chat_id or not retrieved_docs:
        return
    _ensure_engine()

    def _to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    try:
        with engine.begin() as conn:
            for rank_no, d in enumerate(retrieved_docs, start=1):
                if not isinstance(d, dict):
                    continue
                is_cur = d.get("is_current")
                conn.execute(
                    text("""
                        INSERT INTO AnswerSource
                            (ChatID, DocID, FileName, VersionNo, VariantCode, ChunkRef, Score, RankNo, IsCurrent)
                        VALUES
                            (:cid, :doc_id, :fn, :vn, :vc, :chunk, :score, :rank_no, :is_cur)
                    """),
                    {
                        "cid": chat_id,
                        "doc_id": _to_int(d.get("doc_id")),
                        "fn": _cap_len(d.get("file_goc"), 500),
                        "vn": _to_int(d.get("version_no")),
                        "vc": _cap_len(d.get("variant_code"), 100),
                        "chunk": _cap_len(None if d.get("trang") is None else str(d.get("trang")), 200),
                        "score": _to_float(d.get("score")),
                        "rank_no": rank_no,
                        "is_cur": (None if is_cur is None else (1 if is_cur else 0)),
                    },
                )
    except Exception as e:
        logger.error(f"Loi khi luu nguon cau tra loi (AnswerSource): {e}", exc_info=True)


def get_all_sessions(username=None, is_admin=False):
    """Lay danh sach session chat.

    - User thuong chi thay session cua minh (loc theo Username).
    - Admin thay toan bo.
    """
    _ensure_engine()
    try:
        params = {}
        where_clause = ""
        if not is_admin:
            where_clause = "WHERE Username = :username"
            params["username"] = username

        with engine.connect() as conn:
            query = text(
                f"""
                SELECT SessionID, ThoiGianBatDau, CauHoiDauTien, Owner FROM (
                    SELECT SessionID,
                           CauHoi_User AS CauHoiDauTien,
                           ThoiGian AS ThoiGianBatDau,
                           Username AS Owner,
                           ROW_NUMBER() OVER (PARTITION BY SessionID ORDER BY ThoiGian ASC, ChatID ASC) AS rn
                    FROM LichSuChat
                    {where_clause}
                ) t
                WHERE rn = 1
                ORDER BY ThoiGianBatDau DESC
                """
            )
            result = conn.execute(query, params)
            sessions = result.fetchall()
            out = []
            for row in sessions:
                cau_hoi = row[2] or ""
                if len(cau_hoi) > 30:
                    label = cau_hoi[:30] + "..."
                else:
                    label = cau_hoi or "(Khong co tieu de)"
                out.append({"session_id": row[0], "thoi_gian": row[1], "cau_hoi": label, "owner": row[3]})
            return out
    except Exception as e:
        logger.error(f"Loi khi lay danh sach session: {e}", exc_info=True)
        return []
 

def get_chat_history(session_id, username=None, is_admin=False, user_clearance="confidential"):
    """Lay noi dung mot session chat, chi tra ve cua user hien tai (tru admin)."""
    _ensure_engine()
    try:
        params = {"session_id": session_id}
        user_filter = ""
        if not is_admin:
            user_filter = "AND Username = :username"
            params["username"] = username

        with engine.connect() as conn:
            query = text(
                f"""
                SELECT ChatID, CauHoi_User, TraLoi_Bot, HinhAnhUpload, DanhGia, RefImages
                FROM LichSuChat
                WHERE SessionID = :session_id
                {user_filter}
                ORDER BY ThoiGian ASC, ChatID ASC
                """
            )
            rows = conn.execute(query, params).fetchall()

            # P0-2 (bao mat lich su): AN cau tra loi dua tren tai lieu MAT vuot clearance HIEN TAI.
            # Gate tai thoi diem DOC: sau khi bi thu hoi quyen, user khong the doc lai noi dung mat qua lich su chat.
            _redact_ids = set()
            if not is_admin and rows:
                try:
                    _order = {"public": 0, "internal": 1, "confidential": 2}
                    _uorder = _order.get((user_clearance or "public"), 0)
                    _cids = [int(r[0]) for r in rows if r[0] is not None]
                    if _cids:
                        _in = ",".join(str(c) for c in _cids)
                        _lvl_rows = conn.execute(text(
                            "SELECT a.ChatID, MAX(CASE t.SecurityLevel WHEN 'confidential' THEN 2 "
                            "WHEN 'internal' THEN 1 ELSE 0 END) AS lvl "
                            "FROM AnswerSource a JOIN TaiLieu t ON a.DocID = t.DocID "
                            f"WHERE a.ChatID IN ({_in}) GROUP BY a.ChatID"
                        )).fetchall()
                        for _cid, _lvl in _lvl_rows:
                            if (_lvl or 0) > _uorder:
                                _redact_ids.add(_cid)
                except Exception as _e:
                    logger.error(f"Loi tinh redaction lich su chat: {_e}", exc_info=True)

            history = []
            for row in rows:
                # FIX C5: doc lai ref_images tu DB (JSON string -> list duong dan)
                try:
                    ref_images = json.loads(row[5]) if row[5] else []
                except (json.JSONDecodeError, TypeError):
                    ref_images = []
                history.append({"role": "user", "content": row[1], "image": row[3]})
                _assistant_content = row[2]
                _assistant_imgs = ref_images
                if row[0] in _redact_ids:
                    _assistant_content = (
                        "🔒 Nội dung câu trả lời này dựa trên tài liệu MẬT mà bạn hiện "
                        "không còn quyền xem. Nội dung đã được ẩn theo phân quyền."
                    )
                    _assistant_imgs = []
                history.append(
                    {
                        "role": "assistant",
                        "content": _assistant_content,
                        "chat_id": row[0],
                        "danh_gia": row[4],
                        "ref_images": _assistant_imgs,
                    }
                )
            return history
    except Exception as e:
        logger.error(f"Loi khi lay lich su chat: {e}", exc_info=True)
        return []
 

def clear_chat_history(session_id, username=None, is_admin=False):
    """Xoa session chat — user thuong chi xoa duoc session cua minh."""
    _ensure_engine()
    try:
        params = {"session_id": session_id}
        user_filter = ""
        if not is_admin:
            user_filter = "AND Username = :username"
            params["username"] = username

        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    DELETE FROM LichSuChat
                    WHERE SessionID = :session_id
                    {user_filter}
                    """
                ),
                params,
            )
    except Exception as e:
        logger.error(f"Loi khi xoa lich su chat: {e}", exc_info=True)
