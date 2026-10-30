"""Ma tran phan quyen KY VONG (nguon su that cho test bao mat).

Moi dong: (clearance cua user) -> tap security_level tai lieu user DUOC thay.
Dung trong test_security_filter.py va co the mo rong cho test integration.

Quy uoc he thong (xem rag/service.py):
  LEVEL_ORDER = public(0) < internal(1) < confidential(2)
  - User thay duoc moi tai lieu co level <= clearance cua minh.
  - Tai lieu THIEU security_level => coi nhu 'confidential' (chi clearance
    confidential moi thay) -> mac dinh AN TOAN.
"""

EXPECTED_VISIBLE_LEVELS = {
    "public": {"public"},
    "internal": {"public", "internal"},
    "confidential": {"public", "internal", "confidential"},
}

# Clearance khong hop le / None -> he thong fallback 'internal'.
FALLBACK_CLEARANCE = "internal"

# Ai duoc thay tai lieu CHUA gan security_level (empty)?
EMPTY_LEVEL_VISIBLE_ONLY_TO = {"confidential"}
