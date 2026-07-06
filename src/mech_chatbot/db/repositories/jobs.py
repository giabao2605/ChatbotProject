"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from . import audit as _r_audit

__all__ = [
    'cancel_job',
    'create_ingestion_job',
    'get_pending_job',
    'mark_job_failed',
    'mark_job_waiting_quota',
    'queue_eta_seconds',
    'requeue_job',
    'set_job_priority',
    'update_ingestion_job',
    'update_ingestion_report',
]

# ==========================================
# BACKGROUND JOBS
# ==========================================
def create_ingestion_job(file_name, file_path, thu_muc, uploaded_by=None,
                         domain=None, security_level=None, cong_doan=None,
                         site=None, phong_ban=None, upload_meta=None):
    """Tao IngestionJob. GD4: luu kem phan loai chon tu form upload
    (domain / security_level / cong_doan / site / phong_ban) de worker dung
    lam override thay vi chi suy tu folder. Cac tham so nay deu optional;
    neu None thi ingest se tu suy theo folder (backward compatible).
    PhongBan mac dinh = thu_muc neu khong truyen rieng.
    upload_meta: dict metadata nhap luc upload (common fields + domain attrs),
    luu JSON vao IngestionJobs.UploadMetaJson de worker ap xuong TaiLieu.
    """
    import json as _json
    _ensure_engine()

    # P0.2 / P4.1: Server-side guard — block phong ban disabled HOAC archived.
    # Phan biet ro "bang chua co" (legacy OK) vs "DB loi thuc su" (nen block).
    # Check Status truoc (mo hinh moi), fallback IsActive (mo hinh cu).
    try:
        with engine.connect() as _chk:
            _dept_row = _chk.execute(
                text("SELECT IsActive, Status FROM dbo.Departments WHERE DeptCode = :c"),
                {"c": thu_muc},
            ).fetchone()
            if _dept_row is not None:
                _is_active, _status = _dept_row[0], (_dept_row[1] or 'active')
                # Uu tien Status (mo hinh moi P2); fallback IsActive neu Status NULL
                _blocked = (
                    _status.lower() in ('disabled', 'archived')
                    or (not _is_active and _status.lower() not in ('active',))
                )
                if _blocked:
                    logger.warning(
                        f"[P4.1] Blocked create_ingestion_job: phong ban '{thu_muc}'"
                        f" Status='{_status}' IsActive={_is_active}."
                        f" file='{file_name}' uploaded_by='{uploaded_by}'"
                    )
                    return None
    except Exception as _chk_err:
        import sqlalchemy.exc as _sa_exc
        if isinstance(_chk_err, (_sa_exc.ProgrammingError, _sa_exc.OperationalError)):
            # Bang Departments chua ton tai (DB legacy) -> fallback an toan, cho qua
            logger.debug(f"[P4.1] Bang Departments chua co, bo qua check cho '{thu_muc}'")
        else:
            # Loi DB thuc su -> block de tranh tao job vao phong loi
            logger.error(f"[P4.1] Loi DB khi kiem tra phong ban '{thu_muc}': {_chk_err}")
            return None

    _upload_meta_json = (_json.dumps(upload_meta, ensure_ascii=False) if upload_meta else None)
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO dbo.IngestionJobs
                        (TenFile, FilePath, ThuMuc, Status, UploadedBy,
                         Domain, SecurityLevel, PhongBan, CongDoan, Site, UploadMetaJson)
                    OUTPUT INSERTED.JobID
                    VALUES (:f, :p, :t, 'pending', :u,
                            :dom, :sec, :pb, :cd, :site, :upload_meta_json)
                    """
                ),
                {
                    "f": file_name, "p": file_path, "t": thu_muc, "u": uploaded_by,
                    "dom": domain, "sec": security_level,
                    "pb": (",".join(str(x).strip() for x in phong_ban if str(x).strip()) if isinstance(phong_ban, (list, tuple, set)) else phong_ban) or thu_muc, "cd": cong_doan, "site": site,
                    "upload_meta_json": _upload_meta_json,
                }
            )
            row = result.fetchone()
            job_id = row[0] if row else None
            if job_id:
                _r_audit.write_audit_log(uploaded_by or "System", "upload", "IngestionJobs", job_id, {
                    "file_name": file_name, "thu_muc": thu_muc,
                    "domain": domain, "security_level": security_level,
                    "cong_doan": cong_doan, "site": site, "phong_ban": phong_ban or thu_muc,
                })
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
                    UPDATE dbo.IngestionJobs
                    SET Status = :s,
                        ErrorMessage = :e,
                        UpdatedAt = GETDATE()
                    WHERE JobID = :id
                    """
                ),
                {"s": status, "e": error_message, "id": job_id}
            )
    except Exception as e:
        logger.error(f"Loi cap nhat IngestionJob {job_id}: {e}", exc_info=True)

def update_ingestion_report(job_id, report):
    _ensure_engine()
    try:
        import json
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET ExtractionReport = :report,
                    QualityScore = :score,
                    QualityStatus = :status,
                    UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {
                "id": job_id,
                "report": json.dumps(report, ensure_ascii=False),
                "score": report.get("quality_score"),
                "status": report.get("quality_status")
            })
        return True
    except Exception as e:
        logger.error(f"Loi cap nhat report cho job {job_id}: {e}", exc_info=True)
        return False
def get_pending_job(worker_id="worker-1"):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            # Atomic picking:
            # - READPAST: bỏ qua job đang bị worker khác lock
            # - UPDLOCK: giữ update lock cho dòng được chọn
            # - ROWLOCK: ưu tiên lock cấp dòng
            result = conn.execute(
                text(
                    """
                    WITH CTE AS (
                        SELECT TOP 1
                            JobID,
                            TenFile,
                            FilePath,
                            ThuMuc,
                            Status,
                            RetryCount,
                            MaxRetry,
                            LockedBy,
                            LockedAt,
                            ProgressPercent,
                            Domain,
                            SecurityLevel,
                            PhongBan,
                            CongDoan,
                            Site,
                            CreatedAt,
                            UpdatedAt
                        FROM dbo.IngestionJobs WITH (READPAST, UPDLOCK, ROWLOCK)
                        WHERE (
                            (
                                Status IN ('pending', 'pending_retry')
                                AND (
                                    LockedAt IS NULL
                                    OR LockedAt < DATEADD(minute, -15, GETDATE())
                                )
                                AND ISNULL(RetryCount, 0) < ISNULL(MaxRetry, 3)
                            )
                            OR (
                                Status = 'waiting_quota'
                                AND NextRetryAt IS NOT NULL
                                AND NextRetryAt <= GETDATE()
                            )
                            OR (
                                Status IN ('classifying', 'extracting', 'embedding')
                                AND LockedAt < DATEADD(minute, -15, GETDATE())
                                AND ISNULL(RetryCount, 0) < ISNULL(MaxRetry, 3)
                            )
                        )
                        -- P1.5: uu tien theo Priority (nho hon = uu tien hon), roi FIFO theo CreatedAt
                        ORDER BY ISNULL(Priority, 100) ASC, CreatedAt ASC
                    )
                    UPDATE CTE
                    SET Status = 'classifying',
                        LockedBy = :worker_id,
                        LockedAt = GETDATE(),
                        ProgressPercent = 5,
                        UpdatedAt = GETDATE()
                    OUTPUT
                        inserted.JobID,
                        inserted.TenFile,
                        inserted.FilePath,
                        inserted.ThuMuc,
                        inserted.Domain,
                        inserted.SecurityLevel,
                        inserted.PhongBan,
                        inserted.CongDoan,
                        inserted.Site;
                    """
                ),
                {"worker_id": worker_id}
            )

            row = result.fetchone()

            if row:
                return {
                    "job_id": row[0],
                    "ten_file": row[1],
                    "file_path": row[2],
                    "thu_muc": row[3],
                    "domain": row[4],
                    "security_level": row[5],
                    "phong_ban": row[6],
                    "cong_doan": row[7],
                    "site": row[8],
                }

            return None

    except Exception as e:
        logger.error(f"Loi lay pending job: {e}", exc_info=True)
        return None

def mark_job_failed(job_id, error_message):
    _ensure_engine()
    lower_msg = str(error_message).lower()
    if (
        "[quota_exceeded]" in lower_msg
        or "quota exceeded" in lower_msg
        or "resource_exhausted" in lower_msg
        or "free_tier_requests" in lower_msg
    ):
        return mark_job_waiting_quota(job_id, error_message)
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET RetryCount = ISNULL(RetryCount, 0) + 1,
                    Status = CASE 
                        WHEN :e LIKE '%[AUTH_ERROR]%' THEN 'failed'
                        WHEN ISNULL(RetryCount, 0) + 1 >= ISNULL(MaxRetry, 3) THEN 'failed'
                        ELSE 'pending_retry'
                    END,
                    ErrorMessage = :e,
                    LockedBy = NULL,
                    LockedAt = NULL,
                    ProgressPercent = 0,
                    UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {"id": job_id, "e": error_message})
    except Exception as e:
        logger.error(f"Loi danh dau job fail {job_id}: {e}", exc_info=True)

def mark_job_waiting_quota(job_id, error_message, retry_after_hours=24):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET Status = 'waiting_quota',
                    FailureType = 'gemini_quota',
                    ErrorMessage = :e,
                    NextRetryAt = DATEADD(hour, :h, GETDATE()),
                    LockedBy = NULL,
                    LockedAt = NULL,
                    ProgressPercent = 0,
                    UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {
                "id": job_id,
                "e": error_message,
                "h": retry_after_hours
            })
    except Exception as e:
        logger.error(f"Loi danh dau waiting_quota job {job_id}: {e}", exc_info=True)


def set_job_priority(job_id, priority):
    """Dat do uu tien cho job (nho hon = uu tien hon). Vd: 10 = gap, 100 = thuong."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE dbo.IngestionJobs SET Priority = :p, UpdatedAt = GETDATE() WHERE JobID = :id"
            ), {"p": int(priority), "id": job_id})
        return True
    except Exception as e:
        logger.error(f"set_job_priority loi: {e}", exc_info=True)
        return False


def cancel_job(job_id, canceled_by="System"):
    """Huy 1 job dang cho/loi. Khong huy job dang chay giua chung neu da publish."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            res = conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET Status = 'rejected',
                    CanceledBy = :by, CanceledAt = GETDATE(),
                    ErrorMessage = ISNULL(ErrorMessage, '') + ' [Huy boi ' + :by + ']',
                    LockedBy = NULL, LockedAt = NULL, UpdatedAt = GETDATE()
                WHERE JobID = :id
                  AND Status NOT IN ('published', 'pending_review', 'publishing')
            """), {"by": canceled_by, "id": job_id})
        return res.rowcount > 0
    except Exception as e:
        logger.error(f"cancel_job loi: {e}", exc_info=True)
        return False


def requeue_job(job_id):
    """Dua lai job ve 'pending' (retry thu cong)."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET Status = 'pending', ErrorMessage = NULL, FailureType = NULL,
                    NextRetryAt = NULL, RetryCount = 0, LockedBy = NULL, LockedAt = NULL,
                    ProgressPercent = 0, CanceledBy = NULL, CanceledAt = NULL, UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {"id": job_id})
        return True
    except Exception as e:
        logger.error(f"requeue_job loi: {e}", exc_info=True)
        return False


def queue_eta_seconds():
    """Uoc luong ETA (giay) de don het hang doi = so job cho * thoi gian TB/job gan day.
    Tra ve dict {pending, avg_seconds, eta_seconds}."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            pending = conn.execute(text(
                "SELECT COUNT(*) FROM dbo.IngestionJobs WHERE Status IN ('pending','pending_retry','waiting_quota')"
            )).scalar() or 0
            # Thoi gian TB xu ly cua 50 job published gan nhat
            avg = conn.execute(text("""
                SELECT AVG(CAST(DATEDIFF(second, CreatedAt, UpdatedAt) AS FLOAT))
                FROM (
                    SELECT TOP 50 CreatedAt, UpdatedAt FROM dbo.IngestionJobs
                    WHERE Status = 'published' AND UpdatedAt IS NOT NULL
                    ORDER BY UpdatedAt DESC
                ) x
            """)).scalar()
        avg = float(avg) if avg else 90.0  # mac dinh 90s/job neu chua co lich su
        return {"pending": int(pending), "avg_seconds": round(avg, 1), "eta_seconds": int(pending * avg)}
    except Exception as e:
        logger.error(f"queue_eta_seconds loi: {e}", exc_info=True)
        return {"pending": 0, "avg_seconds": 0, "eta_seconds": 0}
