"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import json
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from ..engine import _ensure_engine, engine, resolve_engine as _resolve_engine
from mech_chatbot.config.logging import logger
from ._shared import _cap_len
from .chat_provenance import SCHEMA, provenance_record, load_structured_history, table
from mech_chatbot.application.chat_citations import citation_source_ids
from mech_chatbot.application.protected_files import SHARE_ALL_DEPARTMENT
from .chat_provenance import save_history_result, history_result_column_available, history_result_basis_current
from ..evidence_snapshot import evidence_read_connection, evidence_snapshot_active, EvidenceSnapshotLost


def resolve_engine(candidate=None):
    return _resolve_engine(engine if candidate is None else candidate)

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

def save_chat_history(
    session_id,
    user_msg,
    bot_msg,
    image_path=None,
    ref_images=None,
    username=None,
    *,
    db_engine=None,
    max_user_message_length=None,
    max_bot_message_length=None,
):
    selected_engine = resolve_engine(db_engine)
    if max_user_message_length is None or max_bot_message_length is None:
        from mech_chatbot.config.repository_runtime import (
            current_repository_policy,
        )

        policy = current_repository_policy()
        max_user_message_length = (
            max_user_message_length or policy.max_user_message_length
        )
        max_bot_message_length = (
            max_bot_message_length or policy.max_bot_message_length
        )
    try:
        ref_images_json = json.dumps(ref_images or [], ensure_ascii=False)
        session_id  = _cap_len(session_id, 100)
        user_msg = _cap_len(user_msg, int(max_user_message_length))
        bot_msg = _cap_len(bot_msg, int(max_bot_message_length))
        image_path  = _cap_len(image_path, 500)
        username    = _cap_len(username, 255)

        with selected_engine.begin() as conn:
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


def save_answer_sources(chat_id, retrieved_docs, *, db_engine=None):
    """P3-1: Luu cac tai lieu/chunk RAG da dung de sinh cau tra loi (truy vet nguon)."""
    if not chat_id or not retrieved_docs:
        return
    selected_engine = resolve_engine(db_engine)

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
        with selected_engine.begin() as conn:
            for rank_no, d in enumerate(retrieved_docs, start=1):
                if not isinstance(d, dict):
                    continue
                is_cur = d.get("is_current")
                provenance = provenance_record(d) if 'locator' in d else None
                extra_column = ', SourceLocatorJson' if provenance else ''
                extra_value = ', :locator_json' if provenance else ''
                conn.execute(
                    text(f"""
                        INSERT INTO AnswerSource
                            (ChatID, DocID, FileName, VersionNo, VariantCode, ChunkRef, Score, RankNo, IsCurrent{extra_column})
                        VALUES
                            (:cid, :doc_id, :fn, :vn, :vc, :chunk, :score, :rank_no, :is_cur{extra_value})
                    """),
                    {
                        "cid": chat_id,
                        "doc_id": _to_int(d.get("doc_id")),
                        "fn": _cap_len(d.get("file_goc"), 500),
                        "vn": _to_int(d.get("version_no")),
                        "vc": _cap_len(d.get("variant_code"), 100),
                        "chunk": None if provenance else _cap_len(None if d.get("trang") is None else str(d.get("trang")), 200),
                        "score": _to_float(d.get("score")),
                        "rank_no": rank_no,
                        "is_cur": (None if is_cur is None else (1 if is_cur else 0)),
                        **({'locator_json':json.dumps(provenance,ensure_ascii=False)} if provenance else {}),
                    },
                )
        return True
    except Exception as e:
        logger.error(f"Loi khi luu nguon cau tra loi (AnswerSource): {e}", exc_info=True)
        return False


def save_answer_evidence(chat_id, evidence_docs, requires_authorization=None, *, db_engine=None,
                         advanced_rag=None, citation_cards=()):
    """Persist the complete access basis separately from display citations.

    ``AnswerSource`` intentionally contains only the citations attributed in
    the final answer.  Authorization on history replay must instead cover all
    document evidence supplied to generation, including a document that was
    not rendered as a citation card.
    """
    if not chat_id:
        return False
    selected_engine = resolve_engine(db_engine)

    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    normalized = []
    seen = set()
    source_versions = {}
    input_items = list(evidence_docs or [])
    complete = True
    structured = any(isinstance(item,dict) and 'locator' in item for item in input_items)
    result_history = advanced_rag is not None and any(key in advanced_rag for key in ('query','text_query'))
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
        try:
            provenance = provenance_record(item) if 'locator' in item else None
        except (ValueError,TypeError):
            complete = False
            continue
        page_no = provenance['locator']['page'] if provenance else _to_int(item.get("trang") or item.get("trang_so") or item.get("page_no"))
        key = provenance['source_id'] if provenance else (doc_id, page_no)
        version = provenance['version_no'] if provenance else item.get('version_no')
        source_version = _to_int(version) if type(version) in (int,str) else None
        source_version = source_version if source_version is not None and source_version>0 else None
        if key in seen:
            if result_history and source_version!=source_versions.get(key):
                complete = False
            continue
        seen.add(key)
        source_versions[key] = source_version
        source_ref = provenance['source_id'] if provenance else str(item.get("source_id") or "").strip()[:80] or None
        if result_history and source_version is None:
            complete = False
        if page_no is not None and source_ref is None:
            source_ref = f"D{doc_id}P{page_no}"
        normalized.append(
            {
                "doc_id": doc_id,
                "page_no": page_no,
                "source_ref": source_ref,
                "security_level": _cap_len(item.get("security_level"), 30),
                "rank_no": rank_no,
                **({'locator_json':json.dumps(provenance,ensure_ascii=False) if provenance else None} if structured else {}),
                **({'source_version':source_version} if result_history else {}),
            }
        )

    if input_items and not normalized:
        complete = False
    requires = bool(normalized) if requires_authorization is None else bool(requires_authorization)
    if requires and not normalized:
        complete = False

    try:
        with selected_engine.begin() as conn:
            extra_column = ', SourceLocatorJson' if structured else ''
            extra_value = ', :locator_json' if structured else ''
            if result_history:
                extra_column += ', SourceVersion'
                extra_value += ', :source_version'
            conn.execute(
                text(f"DELETE FROM {table(conn,'AnswerEvidence')} WHERE ChatID = :chat_id"),
                {"chat_id": int(chat_id)},
            )
            if normalized:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {table(conn,'AnswerEvidence')}
                            (ChatID, DocID, PageNo, SourceRef, SecurityLevel, RankNo{extra_column})
                        VALUES
                            (:chat_id, :doc_id, :page_no, :source_ref, :security_level, :rank_no{extra_value})
                        """
                    ),
                    [{"chat_id": int(chat_id), **row} for row in normalized],
                )
            if getattr(getattr(conn,'dialect',None),'name',None) == 'sqlite':
                conn.execute(text('INSERT INTO ChatEvidenceManifest(ChatID,RequiresAuthorization,IsComplete,EvidenceCount,SchemaVersion,IsStructuredComplete) '
                                  'VALUES(:chat,:requires,:complete,:count,:schema,:structured_complete) ON CONFLICT(ChatID) DO UPDATE SET '
                                  'RequiresAuthorization=excluded.RequiresAuthorization,IsComplete=excluded.IsComplete,'
                                  'EvidenceCount=excluded.EvidenceCount,SchemaVersion=excluded.SchemaVersion,IsStructuredComplete=excluded.IsStructuredComplete'),
                             {'chat':int(chat_id),'requires':int(requires),'complete':0 if structured else int(complete),
                              'count':len(normalized),'schema':SCHEMA if structured else 'v1','structured_complete':int(complete) if structured else 0})
                save_history_result(conn, int(chat_id), advanced_rag, citation_cards)
                return bool(complete)
            conn.execute(
                text(
                    f"""
                    MERGE dbo.ChatEvidenceManifest AS target
                    USING (SELECT :chat_id AS ChatID) AS source
                    ON target.ChatID = source.ChatID
                    WHEN MATCHED THEN UPDATE SET
                        RequiresAuthorization = :requires_authorization,
                        IsComplete = :is_complete,
                        EvidenceCount = :evidence_count,
                        {'IsStructuredComplete = :structured_complete,' if structured else ''}
                        SchemaVersion = '{SCHEMA if structured else 'v1'}',
                        UpdatedAt = GETDATE()
                    WHEN NOT MATCHED THEN INSERT
                        (ChatID, RequiresAuthorization, IsComplete, EvidenceCount, SchemaVersion{', IsStructuredComplete' if structured else ''})
                    VALUES
                        (:chat_id, :requires_authorization, :is_complete,
                         :evidence_count, '{SCHEMA if structured else 'v1'}'{', :structured_complete' if structured else ''});
                    """
                ),
                {
                    "chat_id": int(chat_id),
                    "requires_authorization": 1 if requires else 0,
                    # V1 readers cannot certify v2 pins; keep their completeness false.
                    "is_complete": 0 if structured else int(complete),
                    "evidence_count": len(normalized),
                    **({'structured_complete':int(complete)} if structured else {}),
                },
            )
            save_history_result(conn, int(chat_id), advanced_rag, citation_cards)
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
    strict_site_filter=True,
    structured_sources=None,
):
    """Return chat IDs whose complete evidence basis is no longer readable."""
    if not chat_ids:
        return set()

    levels = {"public": 0, "internal": 1, "confidential": 2}
    clearance = levels.get(str(user_clearance or "public").strip().lower(), 0)
    departments = sorted({str(value).strip() for value in (allowed_departments or []) if str(value).strip()})
    sites = {str(value).strip() for value in (allowed_sites or []) if str(value).strip()}
    strict_site = bool(strict_site_filter)

    chat_keys = []
    params = {}
    for index, chat_id in enumerate(sorted({int(value) for value in chat_ids})):
        key = f"chat_{index}"
        chat_keys.append(f":{key}")
        params[key] = chat_id

    if is_global_read_admin:
        department_match = "1 = 1"
    elif departments:
        params['shared_department'] = SHARE_ALL_DEPARTMENT
        department_keys = []
        for index, department in enumerate(departments):
            key = f"dept_{index}"
            department_keys.append(f":{key}")
            params[key] = department
        department_match = (
            f"EXISTS (SELECT 1 FROM {table(conn,'PhongBanChiaSe')} pb "
            "WHERE pb.DocID = t.DocID AND (pb.DeptCode=:shared_department OR pb.DeptCode IN (" + ", ".join(department_keys) + ")))"
        )
    else:
        params['shared_department'] = SHARE_ALL_DEPARTMENT
        department_match = f"EXISTS (SELECT 1 FROM {table(conn,'PhongBanChiaSe')} pb WHERE pb.DocID=t.DocID AND pb.DeptCode=:shared_department)"

    manifest_rows = conn.execute(
        text(
            f"""
            SELECT ChatID, RequiresAuthorization, IsComplete, EvidenceCount, SchemaVersion
            FROM {table(conn,'ChatEvidenceManifest')}
            WHERE ChatID IN ("""
            + ", ".join(chat_keys)
            + ")"
        ),
        params,
    ).fetchall()
    manifests = {
        int(row[0]): {
            "requires": bool(row[1]),
            "complete": bool(row[2]),
            "count": int(row[3] or 0),
            "schema":row[4] if len(row)>4 else 'v1',
        }
        for row in manifest_rows
    }
    redacted = set()
    for chat_id in {int(value) for value in chat_ids}:
        manifest = manifests.get(chat_id)
        # Older rows lack a complete evidence manifest.  Do not replay their
        # answer text after a permission change because it cannot be audited.
        if not manifest or (not manifest["complete"] and manifest['schema'] != SCHEMA):
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
            + f""" THEN 1 ELSE 0 END AS DepartmentAllowed
            FROM {table(conn,'AnswerEvidence')} e
            LEFT JOIN {table(conn,'TaiLieu')} t ON t.DocID = e.DocID
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
        if manifest['schema'] == SCHEMA and chat_id not in redacted:
            profile = {'roles':['admin'] if is_global_read_admin else [],'allowed_departments':departments,
                       'allowed_sites':tuple(sites),'max_security_level':user_clearance}
            try:
                cards = load_structured_history(conn,chat_id,profile)
                if structured_sources is not None:
                    structured_sources[chat_id] = cards
            except (EvidenceSnapshotLost, TimeoutError):
                raise
            except Exception as error:
                if isinstance(error, SQLAlchemyError) and evidence_snapshot_active():
                    raise
                logger.warning('Structured history provenance could not be verified for chat %s',chat_id)
                redacted.add(chat_id)
    return redacted


def get_chat_history(
    session_id,
    username=None,
    is_admin=False,
    user_clearance="confidential",
    allowed_departments=None,
    allowed_sites=None,
    strict_site_filter=True,
    *,
    db_engine=None,
):
    """Return only the caller's own chat session with evidence re-authorization."""
    selected_engine = resolve_engine(db_engine)
    try:
        params = {"session_id": session_id, "username": username}
        user_filter = "AND Username = :username"

        with evidence_read_connection(selected_engine) as conn:
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
            structured_sources = {}
            if rows:
                try:
                    _redact_ids = _history_source_redactions(
                        conn,
                        [row[0] for row in rows if row[0] is not None],
                        user_clearance=user_clearance,
                        allowed_departments=allowed_departments,
                        allowed_sites=allowed_sites,
                        is_global_read_admin=bool(is_admin),
                        strict_site_filter=strict_site_filter,
                        structured_sources=structured_sources,
                    )
                except (EvidenceSnapshotLost, TimeoutError):
                    raise
                except Exception as _e:
                    if isinstance(_e, SQLAlchemyError) and evidence_snapshot_active():
                        raise
                    # History is a data-read surface. A failed authorization
                    # recheck must fail closed for sourced answers.
                    logger.error(f"Loi tinh redaction lich su chat: {_e}", exc_info=True)
                    _redact_ids = {row[0] for row in rows if row[0] is not None}

            history = []
            has_result_column = any(row[0] not in _redact_ids for row in rows) and history_result_column_available(conn)
            for row in rows:
                mentioned = {source for source in citation_source_ids(row[2]) if 'L' in source}
                pinned = {card['source_id'] for card in structured_sources.get(row[0],())}
                if (mentioned and not pinned) or (pinned and (not mentioned or not mentioned.issubset(pinned))):
                    _redact_ids.add(row[0])
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
                result_payload = None
                if has_result_column and row[0] not in _redact_ids:
                    try:
                        result_payload = conn.execute(text(f'SELECT AdvancedRagJson FROM {table(conn,"ChatEvidenceManifest")} WHERE ChatID=:chat'),
                                                      {'chat':row[0]}).scalar()
                        if result_payload is not None and not history_result_basis_current(conn, row[0]):
                            _redact_ids.add(row[0])
                            _assistant_content = 'Nội dung đã được ẩn vì không còn xác minh được nguồn.'
                            _assistant_imgs = []
                    except SQLAlchemyError:
                        if evidence_snapshot_active():
                            raise
                        _redact_ids.add(row[0])
                        _assistant_content = 'Nội dung đã được ẩn vì không còn xác minh được nguồn.'
                        _assistant_imgs = []
                history.append(
                    {
                        "role": "assistant",
                        "content": _assistant_content,
                        "chat_id": row[0],
                        "danh_gia": row[4],
                        "ref_images": _assistant_imgs,
                        **({'_advanced_rag_payload':result_payload} if result_payload is not None and row[0] not in _redact_ids else {}),
                        **({'sources_redacted':True} if row[0] in _redact_ids else {}),
                        **({'structured_citations':structured_sources[row[0]]} if row[0] in structured_sources and row[0] not in _redact_ids else {}),
                    }
                )
            return history
    except (EvidenceSnapshotLost, TimeoutError):
        raise
    except Exception as e:
        if isinstance(e, SQLAlchemyError) and evidence_snapshot_active():
            raise
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
