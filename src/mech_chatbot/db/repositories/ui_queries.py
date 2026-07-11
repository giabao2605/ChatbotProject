"""P2.5 (bọc SQL thô) — các truy vấn/thao tác DB được chuyển TỪ trảng UI (ui/pages/*)
xuống đúng tầng data-access (L4).

Nguyên tắc: giữ **nguyên nội dung SQL** và **đúng ranh giới transaction** (engine.connect =
đọc; engine.begin = ghi/transaction) so với code gốc trong UI -> không đổi hành vi.
Mọi việc render (st.*) và dịch (t()) ở lại UI; module này chỉ trả về dữ liệu thô.

KHÔNG import streamlit / ui ở tầng này.
"""
import os

from sqlalchemy import text

from mech_chatbot.db.engine import engine


def is_engine_ready():
	"""Thay cho check `engine is None` trong UI (để UI không phải import engine)."""
	return engine is not None


def ping_database():
	"""Healthcheck: chạy SELECT 1. Ném exception nếu lỗi (UI bắt và hiển thị)."""
	with engine.connect() as conn:
		conn.execute(text("SELECT 1"))


# ---------------------------------------------------------------------------
# audit.py
# ---------------------------------------------------------------------------

def list_audit_logs(row_limit, only_confidential=False, action_filter=None,
                    username_filter=None, start_dt=None, end_dt=None):
	query = f"""
        SELECT TOP {int(row_limit)} AuditID, Username, Action, EntityType, EntityID, Details, CreatedAt
        FROM AuditLog
        WHERE 1 = 1
    """
	params = {}
	if only_confidential:
		query += " AND Action = :action"
		params["action"] = "read_confidential"
	elif action_filter:
		query += " AND Action LIKE :action"
		params["action"] = f"%{action_filter}%"
	if username_filter:
		query += " AND Username LIKE :username"
		params["username"] = f"%{username_filter}%"
	if start_dt is not None and end_dt is not None:
		query += " AND CreatedAt BETWEEN :start_dt AND :end_dt"
		params["start_dt"] = start_dt
		params["end_dt"] = end_dt
	query += " ORDER BY CreatedAt DESC"
	with engine.connect() as conn:
		return conn.execute(text(query), params).fetchall()


# ---------------------------------------------------------------------------
# dashboard.py
# ---------------------------------------------------------------------------

def _scalar(conn, sql, params=None):
	try:
		return conn.execute(text(sql), params or {}).scalar() or 0
	except Exception:
		return 0


def get_dashboard_stats():
	with engine.connect() as conn:
		return {
			"total_docs": _scalar(conn, "SELECT COUNT(*) FROM TaiLieu WHERE LifecycleStatus <> 'deleting'"),
			"pending_review": _scalar(conn, "SELECT COUNT(*) FROM TaiLieu WHERE LifecycleStatus <> 'deleting' AND ReviewStatus = 'pending_review'"),
			"published_docs": _scalar(conn, "SELECT COUNT(*) FROM TaiLieu WHERE LifecycleStatus = 'published' AND ReviewStatus = 'approved'"),
			"running_jobs": _scalar(conn, "SELECT COUNT(*) FROM dbo.IngestionJobs WHERE Status IN ('pending','pending_retry','classifying','extracting','embedding','publishing')"),
			"failed_jobs": _scalar(conn, "SELECT COUNT(*) FROM dbo.IngestionJobs WHERE Status IN ('failed','waiting_quota')"),
			"today_chats": _scalar(conn, "SELECT COUNT(*) FROM LichSuChat WHERE CAST(ThoiGian AS DATE) = CAST(GETDATE() AS DATE)"),
			"pending_feedback": _scalar(conn, "SELECT COUNT(*) FROM FeedbackReview WHERE ISNULL(AddedToGoldenSet, 0) = 0 AND ISNULL(IsStale, 0) = 0"),
		}


def list_recent_documents():
	with engine.connect() as conn:
		return conn.execute(text("""
                SELECT TOP 10 DocID, TenFile, ThuMuc, ReviewStatus, LifecycleStatus, NgayTaiLen
                FROM TaiLieu
                ORDER BY NgayTaiLen DESC
            """)).fetchall()


def list_recent_failed_jobs():
	with engine.connect() as conn:
		return conn.execute(text("""
                SELECT TOP 10 JobID, TenFile, ThuMuc, Status, ErrorMessage, UpdatedAt
                FROM dbo.IngestionJobs
                WHERE Status IN ('failed','waiting_quota')
                ORDER BY UpdatedAt DESC
            """)).fetchall()


# ---------------------------------------------------------------------------
# queue.py
# ---------------------------------------------------------------------------

def list_ingestion_jobs(status=None, dept=None, search=None, is_admin=False,
                        username=None, allowed_departments=None):
	query_str = """
            SELECT 
                JobID, TenFile, ThuMuc, Status, ErrorMessage, 
                CreatedAt, UploadedBy, RetryCount, MaxRetry, 
                LockedBy, LockedAt, ProgressPercent, UpdatedAt,
                FailureType, NextRetryAt, QualityScore, QualityStatus, ExtractionReport,
                ISNULL(Priority, 100) AS Priority,
                Domain, SecurityLevel, CongDoan, Site
            FROM dbo.IngestionJobs
            WHERE Status NOT IN ('pending_review', 'published', 'archived', 'superseded')
            """
	params = {}
	if status is not None:
		query_str += " AND Status = :status"
		params["status"] = status
	if dept:
		query_str += " AND ThuMuc = :dept_pick"
		params["dept_pick"] = dept
	if search:
		query_str += " AND TenFile LIKE :search"
		params["search"] = f"%{search}%"
	# Queue rows expose filenames, extraction reports, and failure details. Like
	# document browse, they are never globally visible because of an admin role.
	# ``is_admin`` is retained only for backwards-compatible callers and ignored.
	params["uname"] = username
	if allowed_departments:
		_dept_clauses = []
		for _idx, _dept in enumerate(sorted(set(allowed_departments))):
			_k = f"allowed_dept_{_idx}"
			_dept_clauses.append(f"ThuMuc = :{_k}")
			params[_k] = _dept
		query_str += " AND ((" + " OR ".join(_dept_clauses) + ") OR UploadedBy = :uname)"
	else:
		query_str += " AND UploadedBy = :uname"

	query_str += " ORDER BY ISNULL(Priority, 100) ASC, CreatedAt DESC"
	with engine.connect() as conn:
		return conn.execute(text(query_str), params).fetchall()


def bulk_delete_ingestion_jobs(ids):
	ok, fail = 0, 0
	with engine.begin() as conn:
		for jid in ids:
			try:
				conn.execute(text("DELETE FROM dbo.IngestionJobs WHERE JobID = :jid"), {"jid": jid})
				ok += 1
			except Exception:
				fail += 1
	return ok, fail


# ---------------------------------------------------------------------------
# feedback.py
# ---------------------------------------------------------------------------

def list_feedbacks(only_pending):
	query = """
        SELECT FeedbackID, ChatID, Question, BotAnswer, FailureType,
               CorrectAnswer, AddedToGoldenSet, CreatedAt,
               DocVersionNo, Department, IsStale
        FROM FeedbackReview
        WHERE 1 = 1
    """
	if only_pending:
		query += " AND ISNULL(AddedToGoldenSet, 0) = 0 AND ISNULL(IsStale, 0) = 0"
	query += " ORDER BY CreatedAt DESC"
	with engine.connect() as conn:
		return conn.execute(text(query)).fetchall()


def classify_feedback_and_get_source(fid, failure_type, correct_answer, reviewer_note):
	with engine.begin() as conn:
		conn.execute(text("""
                    UPDATE FeedbackReview
                    SET FailureType = :ft,
                        CorrectAnswer = :ca,
                        ReviewerNote = :note,
                        AddedToGoldenSet = 1
                    WHERE FeedbackID = :fid
                """), {"ft": failure_type, "ca": correct_answer, "note": reviewer_note, "fid": fid})
		src_row = conn.execute(
			text("SELECT SourceDocID, Department, Site FROM FeedbackReview WHERE FeedbackID = :fid"),
			{"fid": fid},
		).fetchone()
	return src_row


def delete_feedback(fid):
	with engine.begin() as conn:
		conn.execute(
			text("DELETE FROM FeedbackReview WHERE FeedbackID = :fid"),
			{"fid": fid},
		)


# ---------------------------------------------------------------------------
# documents.py
# ---------------------------------------------------------------------------

def _strict_site_filter_enabled():
	raw = os.getenv("RBAC_STRICT_SITE_FILTER", "true")
	return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _allowed_security_levels(max_security_level):
	levels = {"public": 0, "internal": 1, "confidential": 2}
	max_order = levels.get(str(max_security_level or "public").strip().lower(), 0)
	return [name for name, order in levels.items() if order <= max_order]


def list_documents(allowed_departments=None, max_security_level="public", allowed_sites=None,
                   dept=None, domain=None, sec=None, eff_mode=None, search_kw=None,
                   global_read_admin=False):
	params = {}
	filters = []

	filters.append("d.LifecycleStatus IN ('published', 'archived', 'superseded')")
	filters.append("d.ReviewStatus = 'approved'")
	# The catalog must expose the exact same serving set as retrieval.  Pending
	# staging points can exist in Qdrant for publish latency, but they are never
	# browser-searchable or visible through document metadata APIs.
	filters.append("d.Servable = 1")
	filters.append("d.PublicationState = 'published'")

	# The legacy admin role is the single business-approved global-read role.
	# Every newer control-plane role remains subject to normal document RBAC.
	if not global_read_admin and allowed_departments:
		_dept_placeholders = []
		for _idx, _dept in enumerate(sorted({str(value).strip() for value in allowed_departments if str(value).strip()})):
			_k = f"allowed_dept_{_idx}"
			_dept_placeholders.append(f":{_k}")
			params[_k] = _dept
		if _dept_placeholders:
			filters.append(
				"EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe pb "
				"WHERE pb.DocID = d.DocID AND pb.DeptCode IN (" + ", ".join(_dept_placeholders) + "))"
			)
		else:
			filters.append("1 = 0")
	elif not global_read_admin:
		filters.append("1 = 0")

	if not global_read_admin:
		_security_placeholders = []
		for _idx, _level in enumerate(_allowed_security_levels(max_security_level)):
			_k = f"allowed_level_{_idx}"
			_security_placeholders.append(f":{_k}")
			params[_k] = _level
		filters.append(
			"COALESCE(NULLIF(LOWER(LTRIM(RTRIM(d.SecurityLevel))), ''), 'confidential') "
			"IN (" + ", ".join(_security_placeholders) + ")"
		)

		_normalized_sites = sorted({str(value).strip() for value in (allowed_sites or []) if str(value).strip()})
		if not _normalized_sites:
			filters.append("1 = 0")
		else:
			_site_placeholders = []
			for _idx, _site in enumerate(_normalized_sites):
				_k = f"allowed_site_{_idx}"
				_site_placeholders.append(f":{_k}")
				params[_k] = _site
			_site_match = "d.Site IN (" + ", ".join(_site_placeholders) + ")"
			if _strict_site_filter_enabled():
				filters.append(_site_match)
			else:
				filters.append("(" + _site_match + " OR d.Site IS NULL OR LTRIM(RTRIM(d.Site)) = '')")

	if dept:
		filters.append("d.ThuMuc = :dept")
		params["dept"] = dept

	if domain:
		filters.append("d.Domain = :domain")
		params["domain"] = domain

	if sec:
		filters.append("d.SecurityLevel = :sec")
		params["sec"] = sec

	if eff_mode == "con":
		filters.append("(d.ExpiryDate IS NULL OR d.ExpiryDate > GETDATE())")
		filters.append("(d.EffectiveStatus IS NULL OR d.EffectiveStatus = 'active')")
	elif eff_mode == "sap":
		filters.append("d.ExpiryDate IS NOT NULL")
		filters.append("d.ExpiryDate BETWEEN GETDATE() AND DATEADD(day, 30, GETDATE())")
	elif eff_mode == "het":
		filters.append("d.ExpiryDate IS NOT NULL AND d.ExpiryDate < GETDATE()")

	if search_kw:
		filters.append("(d.TenFile LIKE :kw OR d.Title LIKE :kw OR d.Tags LIKE :kw OR d.Summary LIKE :kw)")
		params["kw"] = f"%{search_kw}%"

	where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

	q = f"""
        SELECT d.DocID, d.TenFile AS OriginalFileName, d.ThuMuc AS Department, d.Domain, d.SecurityLevel,
               d.Title, d.Tags, d.Summary, d.VersionNo, d.IsCurrent AS IsCurrentVersion,
               d.UploadedBy, d.NgayTaiLen AS CreatedAt, d.ExpiryDate, d.EffectiveStatus,
               d.EffectiveDate AS EffectiveDateStart, d.ReviewDate, d.OwnerSigner, d.DocLanguage AS Language,
               d.DocNumber, d.Site, d.VariantGroup, d.VariantCode AS BranchLabel, d.LifecycleStatus, d.ReviewStatus
        FROM TaiLieu d
        {where_clause}
        ORDER BY d.NgayTaiLen DESC
    """
	with engine.connect() as conn:
		return conn.execute(text(q), params).fetchall()


def set_document_current(doc_id):
	with engine.begin() as conn:
		# P4.3: Chi reset cac ban trong cung VariantGroup neu VariantGroup khong NULL.
		conn.execute(text("""
                        UPDATE TaiLieu SET IsCurrent = 0
                        WHERE VariantGroup IS NOT NULL
                          AND VariantGroup = (SELECT VariantGroup FROM TaiLieu WHERE DocID = :id)
                    """), {"id": doc_id})
		conn.execute(
			text("UPDATE TaiLieu SET IsCurrent = 1 WHERE DocID = :id"),
			{"id": doc_id},
		)


def mark_document_expired(doc_id):
	with engine.begin() as conn:
		conn.execute(
			text("UPDATE TaiLieu SET EffectiveStatus = 'expired' WHERE DocID = :id"),
			{"id": doc_id},
		)


def list_expiring_documents():
	with engine.connect() as conn:
		q = text("""
                    SELECT DocID, TenFile AS OriginalFileName, ThuMuc AS Department, EffectiveStatus,
                           ExpiryDate, ReviewDate, IsCurrent AS IsCurrentVersion
                    FROM TaiLieu
                    WHERE (
                        (ExpiryDate IS NOT NULL AND ExpiryDate <= DATEADD(day, 60, GETDATE()))
                        OR (ReviewDate IS NOT NULL AND ReviewDate <= DATEADD(day, 60, GETDATE()))
                    )
                    AND IsCurrent = 1
                    AND LifecycleStatus <> 'deleting'
                    ORDER BY ExpiryDate ASC
                """)
		return conn.execute(q).fetchall()


# ---------------------------------------------------------------------------
# admin.py
# ---------------------------------------------------------------------------

def list_pending_review_docs():
	with engine.connect() as conn:
		return conn.execute(text("""
            SELECT j.JobID, j.TenFile, j.ThuMuc, j.UploadedBy, j.UpdatedAt, j.ExtractionReport,
                   j.Domain, j.SecurityLevel, j.Site, j.UploadMetaJson,
                   d.DocID, d.Title, d.Summary, d.Tags, d.DocNumber, d.DocLanguage AS Language,
                   d.IssuedDate, d.EffectiveDate AS EffectiveDateStart, d.ExpiryDate, d.ReviewDate,
                   d.OwnerSigner, d.EffectiveStatus, d.VersionNo, d.IsCurrent AS IsCurrentVersion,
                   d.VariantGroup, d.VariantCode AS BranchLabel
            FROM IngestionJobs j
            LEFT JOIN TaiLieu d
              ON d.TenFile = j.TenFile
             AND d.ThuMuc = j.ThuMuc
             AND d.ReviewStatus = 'pending_review'
             AND d.LifecycleStatus <> 'deleting'
            WHERE j.Status = 'pending_review'
            ORDER BY j.UpdatedAt ASC
        """)).fetchall()


def reject_ingestion_job(job_id, reason):
	with engine.begin() as conn:
		res = conn.execute(text("""
                    UPDATE IngestionJobs
                    SET Status = 'rejected', RejectReason = :reason, UpdatedAt = GETDATE()
                    WHERE JobID = :jid
                """), {"reason": reason, "jid": job_id})
	return (getattr(res, "rowcount", 0) or 0) > 0


def mark_job_pending_review(job_id):
	with engine.begin() as conn:
		res = conn.execute(text("""
                    UPDATE IngestionJobs SET Status = 'pending_review', UpdatedAt = GETDATE() WHERE JobID = :jid
                """), {"jid": job_id})
	return (getattr(res, "rowcount", 0) or 0) > 0


def mark_job_published(job_id):
	with engine.begin() as conn:
		res = conn.execute(text("""
            UPDATE IngestionJobs
            SET Status = 'published', UpdatedAt = GETDATE()
            WHERE JobID = :jid
        """), {"jid": job_id})
	return (getattr(res, "rowcount", 0) or 0) > 0


def delete_ingestion_job(job_id):
	with engine.begin() as conn:
		res = conn.execute(text("DELETE FROM IngestionJobs WHERE JobID = :jid"), {"jid": job_id})
	return (getattr(res, "rowcount", 0) or 0) > 0


def list_bulk_action_jobs():
	with engine.connect() as conn:
		return conn.execute(text("""
            SELECT j.JobID, j.TenFile, j.ThuMuc, j.Status, j.UpdatedAt, d.DocID
            FROM IngestionJobs j
            LEFT JOIN TaiLieu d
              ON d.TenFile = j.TenFile
             AND d.ThuMuc = j.ThuMuc
             AND d.LifecycleStatus <> 'deleting'
            WHERE j.Status IN ('pending_review', 'failed', 'rejected', 'publishing')
            ORDER BY j.UpdatedAt ASC
        """)).fetchall()


def mark_job_rejected(job_id):
	with engine.begin() as conn:
		res = conn.execute(text("""
                        UPDATE IngestionJobs SET Status = 'rejected', UpdatedAt = GETDATE()
                        WHERE JobID = :jid
                    """), {"jid": job_id})
	return (getattr(res, "rowcount", 0) or 0) > 0


def list_docs_for_bulk_meta(dept=None, domain=None):
	q = "SELECT DocID, TenFile, ThuMuc, Domain FROM TaiLieu WHERE IsCurrent = 1 AND LifecycleStatus <> 'deleting'"
	params = {}
	if dept:
		q += " AND ThuMuc = :dept"
		params["dept"] = dept
	if domain:
		q += " AND Domain = :domain"
		params["domain"] = domain
	q += " ORDER BY ThuMuc, TenFile"
	with engine.connect() as conn:
		return conn.execute(text(q), params).fetchall()


def list_bulk_meta_departments():
	"""P4.5: Lay danh sach phong ban tu TaiLieu.ThuMuc (bao gom ca phong da disabled/archived
	neu con tai lieu cu). The hien badge trang thai de admin nhan biet.
	"""
	try:
		with engine.connect() as conn:
			try:
				rows = conn.execute(text("""
                    SELECT DISTINCT t.ThuMuc,
                           ISNULL(d.Status, 'active') AS DeptStatus
                    FROM TaiLieu t
                    LEFT JOIN dbo.Departments d ON d.DeptCode = t.ThuMuc
                    WHERE t.ThuMuc IS NOT NULL
                    ORDER BY t.ThuMuc
                """)).fetchall()
				result = []
				for thu_muc, dept_status in rows:
					if not thu_muc:
						continue
					if dept_status in ('disabled', 'archived'):
						result.append(f"{thu_muc} ({dept_status})")
					else:
						result.append(thu_muc)
				return result
			except Exception:
				rows = conn.execute(text(
					"SELECT DISTINCT ThuMuc FROM TaiLieu WHERE ThuMuc IS NOT NULL ORDER BY ThuMuc"
				)).fetchall()
				return [r[0] for r in rows if r[0]]
	except Exception:
		return []


# ---------------------------------------------------------------------------
# users.py
# ---------------------------------------------------------------------------

def count_dept_users(dept_code):
	"""So user dang duoc gan vao phong ban nay (qua UserDepartments)."""
	try:
		with engine.connect() as conn:
			row = conn.execute(
				text("SELECT COUNT(*) FROM dbo.UserDepartments WHERE Department = :c"),
				{"c": dept_code},
			).fetchone()
			return int(row[0]) if row else 0
	except Exception:
		return 0


def count_dept_pending_jobs(dept_code):
	"""So jobs dang pending/pending_review/processing cho phong ban nay."""
	try:
		with engine.connect() as conn:
			row = conn.execute(
				text("""
                    SELECT COUNT(*) FROM dbo.IngestionJobs
                    WHERE ThuMuc = :c
                    AND Status IN (
                        'pending', 'pending_retry', 'pending_review',
                        'extracting', 'embedding', 'classifying'
                    )
                """),
				{"c": dept_code},
			).fetchone()
			return int(row[0]) if row else 0
	except Exception:
		return 0


def list_users_basic():
	with engine.connect() as conn:
		return conn.execute(text("""
            SELECT UserID, Username, DisplayName, Department, IsActive, CreatedAt
            FROM Users
            ORDER BY CreatedAt DESC
        """)).fetchall()


def update_user_active_and_roles(user_id, is_active, add_roles, del_roles):
	with engine.begin() as conn:
		conn.execute(
			text("UPDATE Users SET IsActive = :active WHERE UserID = :uid"),
			{"active": 1 if is_active else 0, "uid": user_id},
		)
		for _role in add_roles:
			conn.execute(text("""
                                INSERT INTO UserRoles (UserID, RoleID)
                                SELECT :uid, r.RoleID FROM Roles r
                                WHERE r.RoleName = :role
                                  AND NOT EXISTS (
                                      SELECT 1 FROM UserRoles ur WHERE ur.UserID = :uid AND ur.RoleID = r.RoleID
                                  )
                            """), {"uid": user_id, "role": _role})
		for _role in del_roles:
			conn.execute(text("""
                                DELETE ur FROM UserRoles ur
                                JOIN Roles r ON ur.RoleID = r.RoleID
                                WHERE ur.UserID = :uid AND r.RoleName = :role
			"""), {"uid": user_id, "role": _role})
	return True


def _user_is_admin(conn, user_id):
	row = conn.execute(text("""
		SELECT 1
		FROM Users u
		JOIN UserRoles ur ON ur.UserID = u.UserID
		JOIN Roles r ON r.RoleID = ur.RoleID
		WHERE u.UserID = :uid AND r.RoleName = 'admin'
	"""), {"uid": user_id}).fetchone()
	return bool(row)


def _other_active_admin_count(conn, user_id):
	row = conn.execute(text("""
		SELECT COUNT(*)
		FROM Users u
		JOIN UserRoles ur ON ur.UserID = u.UserID
		JOIN Roles r ON r.RoleID = ur.RoleID
		WHERE u.UserID <> :uid
		  AND u.IsActive = 1
		  AND r.RoleName = 'admin'
	"""), {"uid": user_id}).fetchone()
	return int(row[0]) if row else 0


def set_user_active_status(user_id, is_active, actor_username=None, actor_id=None):
	with engine.begin() as conn:
		target = conn.execute(text("""
			SELECT UserID, Username, DisplayName, IsActive
			FROM Users
			WHERE UserID = :uid
		"""), {"uid": user_id}).fetchone()
		if not target:
			return {"ok": False, "message": "Không tìm thấy tài khoản."}
		if actor_id is not None and int(actor_id) == int(user_id) and not is_active:
			return {"ok": False, "message": "Không thể vô hiệu hóa tài khoản đang đăng nhập."}
		if not is_active and _user_is_admin(conn, user_id) and _other_active_admin_count(conn, user_id) <= 0:
			return {"ok": False, "message": "Không thể vô hiệu hóa admin hoạt động cuối cùng."}

		old_active = bool(target[3])
		conn.execute(
			text("UPDATE Users SET IsActive = :active WHERE UserID = :uid"),
			{"active": 1 if is_active else 0, "uid": user_id},
		)
		conn.execute(text("""
			INSERT INTO AuditLog (UserID, Username, Action, EntityType, EntityID, Details)
			VALUES (:actor_id, :actor_username, :action, 'Users', :target_id, :details)
		"""), {
			"actor_id": actor_id,
			"actor_username": actor_username,
			"action": "user_activate" if is_active else "user_disable",
			"target_id": user_id,
			"details": f"target={target[1]}; from={old_active}; to={bool(is_active)}",
		})
	return {"ok": True, "message": "Đã cập nhật trạng thái tài khoản."}


def delete_user_account(user_id, actor_username=None, actor_id=None):
	with engine.begin() as conn:
		target = conn.execute(text("""
			SELECT UserID, Username, DisplayName, IsActive
			FROM Users
			WHERE UserID = :uid
		"""), {"uid": user_id}).fetchone()
		if not target:
			return {"ok": False, "message": "Không tìm thấy tài khoản."}
		if actor_id is not None and int(actor_id) == int(user_id):
			return {"ok": False, "message": "Không thể xóa tài khoản đang đăng nhập."}
		if _user_is_admin(conn, user_id) and _other_active_admin_count(conn, user_id) <= 0:
			return {"ok": False, "message": "Không thể xóa admin hoạt động cuối cùng."}

		username = target[1]
		conn.execute(text("""
			IF OBJECT_ID(N'dbo.AccessRequests', N'U') IS NOT NULL
				UPDATE dbo.AccessRequests
				SET Status = 'rejected',
					ReviewerID = :actor_id,
					ReviewerUsername = :actor_username,
					ReviewNote = COALESCE(ReviewNote + CHAR(10), '') + :note,
					ReviewedAt = GETDATE()
				WHERE UserID = :uid AND Status = 'pending'
		"""), {
			"uid": user_id,
			"actor_id": actor_id,
			"actor_username": actor_username,
			"note": "Account deleted by admin.",
		})
		conn.execute(text("DELETE FROM UserRoles WHERE UserID = :uid"), {"uid": user_id})
		conn.execute(text("DELETE FROM UserDepartments WHERE UserID = :uid"), {"uid": user_id})
		conn.execute(text("DELETE FROM UserSites WHERE UserID = :uid"), {"uid": user_id})
		conn.execute(text("DELETE FROM UserSecurityClearance WHERE UserID = :uid"), {"uid": user_id})
		conn.execute(text("""
			IF OBJECT_ID(N'dbo.LoginAttempts', N'U') IS NOT NULL
				DELETE FROM dbo.LoginAttempts WHERE Username = :username
		"""), {"username": username})
		res = conn.execute(text("DELETE FROM Users WHERE UserID = :uid"), {"uid": user_id})
		deleted = getattr(res, "rowcount", 0) or 0
		conn.execute(text("""
			INSERT INTO AuditLog (UserID, Username, Action, EntityType, EntityID, Details)
			VALUES (:actor_id, :actor_username, 'user_delete', 'Users', :target_id, :details)
		"""), {
			"actor_id": actor_id,
			"actor_username": actor_username,
			"target_id": user_id,
			"details": f"target={username}; deleted={deleted}",
		})
	return {"ok": bool(deleted), "deleted": deleted, "message": "Đã xóa tài khoản." if deleted else "Xóa tài khoản thất bại."}


def update_user_password(user_id, password_hash):
	with engine.begin() as conn:
		res = conn.execute(
			text("UPDATE Users SET PasswordHash = :p WHERE UserID = :uid"),
			{"p": password_hash, "uid": user_id},
		)
	return (getattr(res, "rowcount", 0) or 0) > 0


def create_user_with_roles(username, password_hash, display_name, department, selected_roles, depts):
	with engine.begin() as conn:
		row = conn.execute(text("""
                    INSERT INTO Users (Username, PasswordHash, DisplayName, Department, IsActive)
                    OUTPUT INSERTED.UserID
                    VALUES (:u, :p, :d, :dept, 1)
                """), {"u": username, "p": password_hash, "d": display_name, "dept": department}).fetchone()
		user_id = row[0]
		for role in selected_roles:
			conn.execute(text("""
                        INSERT INTO UserRoles (UserID, RoleID)
                        SELECT :uid, RoleID FROM Roles WHERE RoleName = :role
                    """), {"uid": user_id, "role": role})
		_dept_vals = [{"uid": user_id, "dept": d} for d in depts if d]
		if _dept_vals:
			conn.execute(
				text("INSERT INTO UserDepartments (UserID, Department) VALUES (:uid, :dept)"),
				_dept_vals,  # Perf (GD1): bulk insert
			)
	return user_id


def get_user_roles(user_id):
	with engine.connect() as conn:
		rows = conn.execute(text("""
            SELECT r.RoleName FROM Roles r JOIN UserRoles ur ON r.RoleID = ur.RoleID WHERE ur.UserID = :uid
        """), {"uid": user_id}).fetchall()
	return [r[0] for r in rows]


def get_user_departments(user_id):
	with engine.connect() as conn:
		rows = conn.execute(
			text("SELECT Department FROM UserDepartments WHERE UserID = :uid"),
			{"uid": user_id},
		).fetchall()
	return [r[0] for r in rows]


def get_user_clearance(user_id):
	try:
		with engine.connect() as conn:
			row = conn.execute(
				text("SELECT MaxLevel FROM UserSecurityClearance WHERE UserID = :uid"),
				{"uid": user_id},
			).fetchone()
		return row[0] if row else "internal"
	except Exception:
		return "internal"


# ---------------------------------------------------------------------------
# chatbot.py
# ---------------------------------------------------------------------------

def fetch_sources_meta_rows(ids):
	keys, params = [], {}
	for i, did in enumerate(ids):
		k = "id_%d" % i
		params[k] = did
		keys.append(":" + k)
	with engine.connect() as conn:
		return conn.execute(text(
			"SELECT DocID, TenFile, ThuMuc, SecurityLevel, FilePath "
			"FROM TaiLieu WHERE DocID IN (" + ", ".join(keys) + ")"
		), params).fetchall()


__all__ = [
	"is_engine_ready",
	"ping_database",
	"list_audit_logs",
	"get_dashboard_stats",
	"list_recent_documents",
	"list_recent_failed_jobs",
	"list_ingestion_jobs",
	"bulk_delete_ingestion_jobs",
	"list_feedbacks",
	"classify_feedback_and_get_source",
	"delete_feedback",
	"list_documents",
	"set_document_current",
	"mark_document_expired",
	"list_expiring_documents",
	"list_pending_review_docs",
	"reject_ingestion_job",
	"mark_job_pending_review",
	"mark_job_published",
	"delete_ingestion_job",
	"list_bulk_action_jobs",
	"mark_job_rejected",
	"list_docs_for_bulk_meta",
	"list_bulk_meta_departments",
	"count_dept_users",
	"count_dept_pending_jobs",
	"list_users_basic",
	"update_user_active_and_roles",
	"set_user_active_status",
	"delete_user_account",
	"update_user_password",
	"create_user_with_roles",
	"get_user_roles",
	"get_user_departments",
	"get_user_clearance",
	"fetch_sources_meta_rows",
]
