"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import os
import re
import unicodedata
from datetime import datetime
from mech_chatbot.config.logging import logger

__all__ = [
    'MAX_BOT_MSG_LEN',
    'MAX_USER_MSG_LEN',
    '_cap_len',
    '_sanitize_date',
    '_sanitize_int',
    '_sanitize_text',
    'normalize_base_code',
]

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
 
def _sanitize_int(val, default=None):
    """Parse so nguyen tu chuoi.

    Tra ve None (khong phai 1) khi khong parse duoc, de caller tu quyet dinh
    co ghi NULL hay reject. default=1 truoc day co the am tham ghi so luong sai.
    """
    try:
        nums = re.findall(r"\d+", str(val))
        return int(nums[0]) if nums else default
    except Exception:
        return default
        

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
