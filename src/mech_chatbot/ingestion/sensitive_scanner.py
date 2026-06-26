"""
Sensitive content scanner (GD4).

Duong nap hang loat (nap_them_file / fileserver) KHONG duoc tin tuong tuyet doi
vao folder goc: mot file luong/CMND/hop dong co the nam nham trong folder ky thuat.
Module nay quet nhanh noi dung van ban da trich xuat de phat hien tin hieu nhay cam;
neu co, ingest se NANG muc mat len 'confidential' va danh dau can review thu cong.

Thuan tuy regex/keyword, khong goi LLM, dam bao re va khong lam gay luong ingest.
"""
import re

# Cac nhom tin hieu nhay cam (tieng Viet co/khong dau + tieng Anh).
_SENSITIVE_PATTERNS = {
    "payroll": [
        r"b\s*ng\s*l\u01b0\u01a1ng", r"bang\s*luong", r"phi\u1ebfu\s*l\u01b0\u01a1ng",
        r"phieu\s*luong", r"\bpayroll\b", r"\bsalary\b", r"thu\s*nh\u1eadp\s*c\u00e1\s*nh\u00e2n",
        r"l\u01b0\u01a1ng\s*net", r"l\u01b0\u01a1ng\s*gross", r"b\u1ea3o\s*hi\u1ec3m\s*x\u00e3\s*h\u1ed9i", r"\bBHXH\b",
    ],
    "national_id": [
        r"\bCMND\b", r"\bCCCD\b", r"c\u0103n\s*c\u01b0\u1edbc\s*c\u00f4ng\s*d\u00e2n",
        r"can\s*cuoc\s*cong\s*dan", r"ch\u1ee9ng\s*minh\s*nh\u00e2n\s*d\u00e2n",
        r"chung\s*minh\s*nhan\s*dan", r"s\u1ed1\s*h\u1ed9\s*chi\u1ebfu", r"\bpassport\b",
        r"\b\d{9}\b", r"\b\d{12}\b",  # so CMND 9 / CCCD 12 chu so
    ],
    "contract": [
        r"h\u1ee3p\s*\u0111\u1ed3ng\s*lao\s*\u0111\u1ed9ng", r"hop\s*dong\s*lao\s*dong",
        r"\blabou?r\s*contract\b", r"th\u1ecf\a?\s*thu\u1eadn\s*l\u01b0\u01a1ng",
    ],
    "banking": [
        r"s\u1ed1\s*t\u00e0i\s*kho\u1ea3n", r"so\s*tai\s*khoan", r"\baccount\s*number\b",
        r"\bIBAN\b", r"\bSWIFT\b",
    ],
}


def scan_sensitive_content(text, max_chars=20000):
    """Quet text, tra ve dict:
        {
          "is_sensitive": bool,
          "categories": ["payroll", ...],
          "matched": {category: [mau dau tien]}
        }
    Chi quet max_chars dau de tiet kiem chi phi.
    """
    result = {"is_sensitive": False, "categories": [], "matched": {}}
    if not text:
        return result
    sample = text[:max_chars]
    for category, patterns in _SENSITIVE_PATTERNS.items():
        for pat in patterns:
            try:
                m = re.search(pat, sample, re.IGNORECASE)
            except re.error:
                continue
            if m:
                result["categories"].append(category)
                result["matched"][category] = m.group(0)
                break
    result["is_sensitive"] = len(result["categories"]) > 0
    return result


def escalate_security(current_level, scan_result):
    """Neu phat hien nhay cam -> nang len 'confidential'. Khong bao gio ha xuong."""
    if scan_result and scan_result.get("is_sensitive"):
        return "confidential"
    return current_level
