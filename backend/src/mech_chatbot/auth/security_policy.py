"""P0 #7 — Chinh sach bao mat auth (THUAN, khong import nang).

Van de cu: user THIEU ban ghi clearance -> mac dinh 'internal' (rong hon public)
-> co the CAP DU QUYEN (user le ra chi duoc xem public lai thay ca internal).

Giai phap an toan mac dinh (fail-safe): thieu/khong hop le -> 'public' (thap nhat).
Muon cap quyen cao hon PHAI co ban ghi ro rang trong UserSecurityClearance.
"""

# An toan mac dinh: muc thap nhat.
DEFAULT_MAX_SECURITY_LEVEL = "public"

# Cac muc hop le (khop LEVEL_ORDER trong rag/rbac.py)
VALID_CLEARANCES = {"public", "internal", "confidential"}


def resolve_clearance(raw, default=DEFAULT_MAX_SECURITY_LEVEL):
    """Chuan hoa clearance doc tu DB.

    - None / rong / khong hop le  -> default an toan (public)
    - Hop le                       -> tra ve dang chu thuong da chuan hoa
    """
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v not in VALID_CLEARANCES:
        return default
    return v
