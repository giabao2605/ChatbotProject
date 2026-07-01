"""Hang so dung chung toan he thong (KHONG phu thuoc gi nang -> moi module import an toan).

F1: Chinh thuc hoa CHUNG thanh SENTINEL "chia se cho moi phong ban" (share-all),
KHONG phai mot phong ban that. Tap trung ve mot noi de tranh magic string rai rac.
"""

# Sentinel phong ban: tai lieu gan quyen doc cho SHARE_ALL_DEPARTMENT nghia la
# MOI phong ban deu duoc doc (dung o RBAC filter, seed quyen, fallback...).
SHARE_ALL_DEPARTMENT = "CHUNG"


def is_share_all(dept) -> bool:
    """True neu gia tri phong ban la sentinel share-all.

    Chuan hoa: bo khoang trang + khong phan biet hoa/thuong de tranh sai sot du lieu.
    """
    return str(dept or "").strip().upper() == SHARE_ALL_DEPARTMENT
