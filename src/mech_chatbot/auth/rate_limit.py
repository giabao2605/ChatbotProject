"""P0 #5 — Rate-limit / khoa tai khoan dung STORE CHUNG qua SQL Server.

Van de cu: so lan dang nhap sai luu trong RAM tung tien trinh (defaultdict).
Chay nhieu worker uvicorn -> moi worker dem rieng -> attacker xoay vong giua
cac worker co the NE duoc lockout.

Giai phap: ghi lan sai vao bang SQL Server `LoginAttempts` -> moi tien trinh
deu thay chung. Co FALLBACK in-process khi engine None / DB loi (de dev/local
va luc DB tam chet van khong vo luong dang nhap).

Logic nguong/cua so duoc tach thanh ham THUAN de unit-test khong can DB.
"""
import threading
import time
from collections import defaultdict

from sqlalchemy import text

MAX_FAILURES = 5         # So lan sai toi da trong cua so
WINDOW_SECONDS = 600     # Cua so dem (10 phut)
LOCKOUT_SECONDS = 300    # Thoi gian khoa hien thi cho nguoi dung (5 phut)


# ---------------------------------------------------------------------------
# Ham THUAN (unit-test duoc, khong can DB)
# ---------------------------------------------------------------------------
def is_over_threshold(recent_count, max_failures=MAX_FAILURES):
    """True neu so lan sai gan day >= nguong."""
    return recent_count >= max_failures


def count_within_window(timestamps, now, window_seconds=WINDOW_SECONDS):
    """Dem so moc thoi gian con nam trong cua so [now - window, now]."""
    return len([t for t in timestamps if now - t < window_seconds])


# ---------------------------------------------------------------------------
# Fallback in-process (dung khi engine None hoac DB loi)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_fails = defaultdict(list)  # username -> [monotonic_ts, ...]


def _mem_count(username, now, window_seconds):
    with _lock:
        _fails[username] = [t for t in _fails[username] if now - t < window_seconds]
        return len(_fails[username])


def _mem_record(username, now):
    with _lock:
        _fails[username].append(now)


def _mem_clear(username):
    with _lock:
        _fails.pop(username, None)


def _reset_mem():
    """Chi dung trong test."""
    with _lock:
        _fails.clear()


# ---------------------------------------------------------------------------
# SQL backend
# ---------------------------------------------------------------------------
def _sql_count(engine, username, window_seconds):
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM LoginAttempts
                WHERE Username = :u
                  AND AttemptAt > DATEADD(SECOND, :w, SYSUTCDATETIME())
                """
            ),
            {"u": username, "w": -int(window_seconds)},
        ).fetchone()
        return int(row[0]) if row else 0


def _sql_record(engine, username):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO LoginAttempts (Username, AttemptAt) VALUES (:u, SYSUTCDATETIME())"),
            {"u": username},
        )


def _sql_clear(engine, username):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM LoginAttempts WHERE Username = :u"),
            {"u": username},
        )


def _log_db_error(msg):
    try:
        from mech_chatbot.config.logging import logger
        logger.warning("[rate-limit] " + msg, exc_info=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# API cong khai (tu dong chon backend)
# ---------------------------------------------------------------------------
def is_rate_limited(engine, username, *, max_failures=MAX_FAILURES, window_seconds=WINDOW_SECONDS):
    """True neu username dang bi khoa do qua nhieu lan sai trong cua so."""
    if engine is not None:
        try:
            return is_over_threshold(_sql_count(engine, username, window_seconds), max_failures)
        except Exception:
            _log_db_error("DB loi khi dem -> fallback in-process")
    return is_over_threshold(_mem_count(username, time.monotonic(), window_seconds), max_failures)


def record_failure(engine, username):
    """Ghi nhan 1 lan dang nhap sai."""
    if engine is not None:
        try:
            _sql_record(engine, username)
            return
        except Exception:
            _log_db_error("DB loi khi ghi -> fallback in-process")
    _mem_record(username, time.monotonic())


def clear_failures(engine, username):
    """Xoa lich su sai sau khi dang nhap thanh cong."""
    if engine is not None:
        try:
            _sql_clear(engine, username)
            return
        except Exception:
            _log_db_error("DB loi khi xoa -> fallback in-process")
    _mem_clear(username)
