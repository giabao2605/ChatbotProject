"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import json
import os
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import MAX_BOT_MSG_LEN, MAX_USER_MSG_LEN, _cap_len

__all__ = [
    'clear_chat_history',
    'get_all_sessions',
    'get_chat_history',
    'save_answer_evidence',
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


def save_answer_evidence(chat_id, evidence_docs, requires_authorization=None):
    """Persist the complete access basis separately from display citations.

    ``AnswerSource`` intentionally contains only the citations attributed in
    the final answer.  Authorization on history replay must instead cover all
    document evidence supplied to generation, including a document that was
    not rendered as a citation card.
    """
    if not chat_id:
        return False
    _ensure_engine()

    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    normalized = []
    seen = set()
    input_items = list(evidence_docs or [])
    complete = True
    for rank_no, item in enumerate(input_items, start=1):
        if not isinstance(item, dict):
            complete = False
            continue
        doc_id = _to_int(item.get("doc_id"))
        if doc_id is None:
            # Synthetic user-image context is not a knowledge-document read.
            if str(item.get("loai_du_lieu") or "").strip().lower() not in {
                "image_summary", "user_image", ""
            }:
                complete = False
            continue
        page_no = _to_int(item.get("trang") or item.get("trang_so") or item.get("page_no"))
        key = (doc_id, page_no)
        if key in seen:
            continue
        seen.add(key)
        source_ref = str(item.get("source_id") or "").strip()[:80] or None
        if page_no is not None and source_ref is None:
            source_ref = f"D{doc_id}P{page_no}"
        normalized.append(
            {
                "doc_id": doc_id,
                "page_no": page_no,
                "source_ref": source_ref,
                "security_level": _cap_len(item.get("security_level"), 30),
                "rank_no": rank_no,
            }
        )

    if input_items and not normalized:
        complete = False
    requires = bool(normalized) if requires_authorization is None else bool(requires_authorization)
    if requires and not normalized:
        complete = False

    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM dbo.AnswerEvidence WHERE ChatID = :chat_id"),
                {"chat_id": int(chat_id)},
            )
            if normalized:
                conn.execute(
                    text(
                        """
                        INSERT INTO dbo.AnswerEvidence
                            (ChatID, DocID, PageNo, SourceRef, SecurityLevel, RankNo)
                        VALUES
                            (:chat_id, :doc_id, :page_no, :source_ref, :security_level, :rank_no)
                        """
                    ),
                    [{"chat_id": int(chat_id), **row} for row in normalized],
                )
            conn.execute(
                text(
                    """
                    MERGE dbo.ChatEvidenceManifest AS target
                    USING (SELECT :chat_id AS ChatID) AS source
                    ON target.ChatID = source.ChatID
                    WHEN MATCHED THEN UPDATE SET
                        RequiresAuthorization = :requires_authorization,
                        IsComplete = :is_complete,
                        EvidenceCount = :evidence_count,
                        SchemaVersion = 'v1',
                        UpdatedAt = GETDATE()
                    WHEN NOT MATCHED THEN INSERT
                        (ChatID, RequiresAuthorization, IsComplete, EvidenceCount, SchemaVersion)
                    VALUES
                        (:chat_id, :requires_authorization, :is_complete,
                         :evidence_count, 'v1');
                    """
                ),
                {
                    "chat_id": int(chat_id),
                    "requires_authorization": 1 if requires else 0,
                    "is_complete": 1 if complete else 0,
                    "evidence_count": len(normalized),
                },
            )
        return bool(complete)
    except Exception as exc:
        logger.error("Loi khi luu evidence cua cau tra loi: %s", exc, exc_info=True)
        return False


def get_all_sessions(username=None, is_admin=False):
    """Lay danh sach session chat.

    Chat history can contain internal content, so every caller is scoped to its
    own username. ``is_admin`` remains only for signature compatibility and is
    intentionally ignored.
    """
    _ensure_engine()
    try:
        params = {"username": username}
        where_clause = "WHERE Username = :username"

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
 

def _history_source_redactions(
    conn,
    chat_ids,
    *,
    user_clearance,
    allowed_departments,
    allowed_sites,
    is_global_read_admin=False,
):
    """Return chat IDs whose complete evidence basis is no longer readable."""
    if not chat_ids:
        return set()

    levels = {"public": 0, "internal": 1, "confidential": 2}
    clearance = levels.get(str(user_clearance or "public").strip().lower(), 0)
    departments = sorted({str(value).strip() for value in (allowed_departments or []) if str(value).strip()})
    sites = {str(value).strip() for value in (allowed_sites or []) if str(value).strip()}
    strict_site = str(os.getenv("RBAC_STRICT_SITE_FILTER", "true")).strip().lower() in {
        "1", "true", "yes", "on"
    }

    chat_keys = []
    params = {}
    for index, chat_id in enumerate(sorted({int(value) for value in chat_ids})):
        key = f"chat_{index}"
        chat_keys.append(f":{key}")
        params[key] = chat_id

    if is_global_read_admin:
        department_match = "1 = 1"
    elif departments:
        department_keys = []
        for index, department in enumerate(departments):
            key = f"dept_{index}"
            department_keys.append(f":{key}")
            params[key] = department
        department_match = (
            "EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe pb "
            "WHERE pb.DocID = t.DocID AND pb.DeptCode IN (" + ", ".join(department_keys) + "))"
        )
    else:
        department_match = "1 = 0"

    manifest_rows = conn.execute(
        text(
            """
            SELECT ChatID, RequiresAuthorization, IsComplete, EvidenceCount
            FROM dbo.ChatEvidenceManifest
            WHERE ChatID IN ("""
            + ", ".join(chat_keys)
            + ")"
        ),
        params,
    ).fetchall()
    manifests = {
        int(chat_id): {
            "requires": bool(requires),
            "complete": bool(complete),
            "count": int(count or 0),
        }
        for chat_id, requires, complete, count in manifest_rows
    }
    redacted = set()
    for chat_id in {int(value) for value in chat_ids}:
        manifest = manifests.get(chat_id)
        # Older rows lack a complete evidence manifest.  Do not replay their
        # answer text after a permission change because it cannot be audited.
        if not manifest or not manifest["complete"]:
            redacted.add(chat_id)
        elif manifest["requires"] and manifest["count"] <= 0:
            redacted.add(chat_id)

    rows = conn.execute(
        text(
            """
            SELECT e.ChatID, e.EvidenceID, t.DocID, t.SecurityLevel, t.Site,
                   t.Servable, t.PublicationState, t.LifecycleStatus, t.ReviewStatus,
                   CASE WHEN """
            + department_match
            + """ THEN 1 ELSE 0 END AS DepartmentAllowed
            FROM dbo.AnswerEvidence e
            LEFT JOIN dbo.TaiLieu t ON t.DocID = e.DocID
            WHERE e.ChatID IN ("""
            + ", ".join(chat_keys)
            + ")"
        ),
        params,
    ).fetchall()

    observed_counts = {}
    for (
        chat_id,
        _evidence_id,
        doc_id,
        security_level,
        site,
        servable,
        publication_state,
        lifecycle_status,
        review_status,
        department_allowed,
    ) in rows:
        chat_id = int(chat_id)
        observed_counts[chat_id] = observed_counts.get(chat_id, 0) + 1
        if doc_id is None:
            redacted.add(chat_id)
            continue
        if not bool(servable) or str(publication_state or "").strip().lower() != "published":
            redacted.add(chat_id)
            continue
        if str(lifecycle_status or "").strip().lower() != "published" or str(review_status or "").strip().lower() != "approved":
            redacted.add(chat_id)
            continue
        if is_global_read_admin:
            continue
        level = levels.get(str(security_level or "confidential").strip().lower(), 2)
        normalized_site = str(site or "").strip()
        site_allowed = normalized_site in sites
        if level > clearance or not bool(department_allowed):
            redacted.add(int(chat_id))
        elif strict_site and not site_allowed:
            redacted.add(int(chat_id))
        elif not strict_site and normalized_site and not site_allowed:
            redacted.add(int(chat_id))
    for chat_id, manifest in manifests.items():
        if manifest["requires"] and observed_counts.get(chat_id, 0) != manifest["count"]:
            redacted.add(chat_id)
    return redacted


def get_chat_history(
    session_id,
    username=None,
    is_admin=False,
    user_clearance="confidential",
    allowed_departments=None,
    allowed_sites=None,
):
    """Return only the caller's own chat session with evidence re-authorization."""
    _ensure_engine()
    try:
        params = {"session_id": session_id, "username": username}
        user_filter = "AND Username = :username"

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

            # Re-evaluate every cited source on read. This protects history
            # after a clearance, department, or site grant is revoked.
            _redact_ids = set()
            if rows:
                try:
                    _redact_ids = _history_source_redactions(
                        conn,
                        [row[0] for row in rows if row[0] is not None],
                        user_clearance=user_clearance,
                        allowed_departments=allowed_departments,
                        allowed_sites=allowed_sites,
                        is_global_read_admin=bool(is_admin),
                    )
                except Exception as _e:
                    # History is a data-read surface. A failed authorization
                    # recheck must fail closed for sourced answers.
                    logger.error(f"Loi tinh redaction lich su chat: {_e}", exc_info=True)
                    _redact_ids = {row[0] for row in rows if row[0] is not None}

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
                        "Nội dung câu trả lời này dựa trên tài liệu MẬT mà bạn hiện "
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
    """Delete only the caller's own chat session; admin is not a bypass."""
    _ensure_engine()
    params = {"session_id": session_id, "username": username}
    user_filter = "AND Username = :username"

    target_chat_ids_sql = f"""
        SELECT ChatID FROM LichSuChat
        WHERE SessionID = :session_id
        {user_filter}
    """

    try:
        with engine.begin() as conn:
            # DB moi co ON DELETE CASCADE, nhung DB cu co the thieu cascade.
            # Xoa bang con truoc de thao tac xoa session khong bi chan FK.
            conn.execute(
                text(f"DELETE FROM AnswerSource WHERE ChatID IN ({target_chat_ids_sql})"),
                params,
            )
            conn.execute(
                text(f"DELETE FROM FeedbackReview WHERE ChatID IN ({target_chat_ids_sql})"),
                params,
            )
            res = conn.execute(
                text(
                    f"""
                    DELETE FROM LichSuChat
                    WHERE SessionID = :session_id
                    {user_filter}
                    """
                ),
                params,
            )
            return int(getattr(res, "rowcount", 0) or 0)
    except Exception as e:
        logger.error(f"Loi khi xoa lich su chat: {e}", exc_info=True)
        raise
