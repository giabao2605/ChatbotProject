"""
Sensitive content scanner (GD4 / P1 hardening).

Duong nap hang loat (nap_them_file / fileserver) KHONG duoc tin tuong tuyet doi
vao folder goc: mot file luong/CMND/hop dong co the nam nham trong folder ky thuat.
Module nay quet noi dung van ban da trich xuat de phat hien tin hieu nhay cam;
neu co, ingest se NANG muc mat len 'confidential' va danh dau can review thu cong.

Thuan tuy regex/keyword, khong goi LLM, dam bao re va khong lam gay luong ingest.

P1 fixes:
- Khong chi quet 20.000 ky tu dau nua: mac dinh quet TOAN VAN de khong bo sot
  tin nhay cam o cuoi tai lieu dai.
- Khong coi moi so 9/12 chu so la CMND/CCCD nua: so dinh danh chi duoc bat khi
  co ngu canh CMND/CCCD/passport/so dinh danh gan do, tranh false positive voi
  ma part/ma ban ve/so luong.
"""
import re

# Cac nhom tin hieu nhay cam (tieng Viet co/khong dau + tieng Anh).
# Luu y national_id:
# - Giu keyword CMND/CCCD/passport de bat tai lieu co nhan ro rang.
# - Pattern so 9/12 chu so BAT BUOC co ngu canh, khong match so dung doc lap.
_SENSITIVE_PATTERNS = {
    "payroll": [
        r"b\s*[aả]ng\s*l[uư][oơ]ng",
        r"bang\s*luong",
        r"phi[eế]u\s*l[uư][oơ]ng",
        r"phieu\s*luong",
        r"\bpayroll\b",
        r"\bsalary\b",
        r"thu\s*nh[aậ]p\s*c[aá]\s*nh[aâ]n",
        r"l[uư][oơ]ng\s*net",
        r"l[uư][oơ]ng\s*gross",
        r"b[aả]o\s*hi[eể]m\s*x[aã]\s*h[oộ]i",
        r"\bBHXH\b",
    ],
    "national_id": [
        r"\bCMND\b",
        r"\bCCCD\b",
        r"c[aă]n\s*c[uư][oơ]c\s*c[oô]ng\s*d[aâ]n",
        r"can\s*cuoc\s*cong\s*dan",
        r"ch[uứ]ng\s*minh\s*nh[aâ]n\s*d[aâ]n",
        r"chung\s*minh\s*nhan\s*dan",
        r"s[oố]\s*h[oộ]\s*chi[eế]u",
        r"\bpassport\b",
        # Contextual ID numbers: label phai o gan so, tranh match ma part 123456789.
        r"(?:\bCMND\b|\bCCCD\b|c[aă]n\s*c[uư][oơ]c|can\s*cuoc|ch[uứ]ng\s*minh|chung\s*minh|s[oố]\s*(?:dinh\s*danh|h[oộ]\s*chi[eế]u)|passport)\D{0,40}\b\d{9}\b",
        r"(?:\bCMND\b|\bCCCD\b|c[aă]n\s*c[uư][oơ]c|can\s*cuoc|ch[uứ]ng\s*minh|chung\s*minh|s[oố]\s*(?:dinh\s*danh|h[oộ]\s*chi[eế]u)|passport)\D{0,40}\b\d{12}\b",
    ],
    "contract": [
        r"h[oợ]p\s*d[oồ]ng\s*lao\s*d[oộ]ng",
        r"hop\s*dong\s*lao\s*dong",
        r"\blabou?r\s*contract\b",
        r"th[oỏ]a?\s*thu[aậ]n\s*l[uư][oơ]ng",
    ],
    "banking": [
        r"s[oố]\s*t[aà]i\s*kho[aả]n",
        r"so\s*tai\s*khoan",
        r"\baccount\s*number\b",
        r"\bIBAN\b",
        r"\bSWIFT\b",
    ],
    # Credential patterns require both a recognizable label/format and a value.
    # Matches are counted only; the value is never returned to callers or logs.
    "api_key_or_token": [
        r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+\-=]{12,}",
        r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{12,}\b",
        r"\bgh[opusr]_[A-Za-z0-9]{20,}\b",
    ],
    "password_assignment": [
        r"\b(?:password|passwd|pwd|mat\s*khau|m[aậ]t\s*kh[aẩ]u)\b\s*[:=]\s*(?!<[^>]+>|\*{3,}|x{3,}|change[_ -]?me\b)[\"']?[^\s\"']{8,}",
    ],
    "private_key": [
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    ],
    "bearer_or_jwt": [
        r"\bBearer\s+[A-Za-z0-9._~+/\-=]{16,}",
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    ],
    "database_connection_string": [
        r"\b(?:Server|Data Source)\s*=\s*[^;\r\n]+;[^\r\n]{0,300}\b(?:Password|Pwd)\s*=\s*[^;\s\r\n]+",
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^\s:/]+:[^\s@]+@[^\s]+",
    ],
    "cloud_service_credential": [
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\b(?:aws_secret_access_key|azure_client_secret|google_application_credentials)\b\s*[:=]\s*[\"']?[^\s\"']{12,}",
        r'"type"\s*:\s*"service_account"[^}]{0,2000}"private_key"\s*:\s*"-----BEGIN PRIVATE KEY-----',
    ],
}

CREDENTIAL_CATEGORIES = frozenset({
    "api_key_or_token",
    "password_assignment",
    "private_key",
    "bearer_or_jwt",
    "database_connection_string",
    "cloud_service_credential",
})


def _text_to_scan(text, max_chars=None):
    """Tra ve phan text se quet.

    Mac dinh P1: quet toan van. Neu caller muon gioi han chi phi cho tai lieu
    cuc lon, truyen max_chars > 0 de quet phan dau + phan cuoi, thay vi chi
    phan dau. Nhu vay van bat duoc tin nhay cam bi day xuong cuoi file.
    """
    if text is None:
        return ""
    text = str(text)
    if not max_chars or max_chars <= 0 or len(text) <= max_chars:
        return text

    # Neu bat buoc gioi han, chia budget cho HEAD + TAIL de khong bo sot cuoi file.
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n...<TRUNCATED_MIDDLE>...\n" + text[-tail:]


def scan_sensitive_content(text, max_chars=None):
    """Quet text, tra ve dict:
        {
          "is_sensitive": bool,
          "categories": ["payroll", ...],
          "match_counts": {category: count}
        }

    P1: mac dinh quet TOAN VAN. Tham so max_chars chi la tuy chon gioi han
    head+tail cho tai lieu cuc lon, khong con chi cat phan dau.
    """
    result = {"is_sensitive": False, "categories": [], "match_counts": {}}
    sample = _text_to_scan(text, max_chars=max_chars)
    if not sample:
        return result

    for category, patterns in _SENSITIVE_PATTERNS.items():
        count = 0
        for pat in patterns:
            try:
                matches = re.findall(pat, sample, re.IGNORECASE)
            except re.error:
                continue
            count += len(matches)
        if count:
            result["categories"].append(category)
            result["match_counts"][category] = count
    result["is_sensitive"] = len(result["categories"]) > 0
    return result


def escalate_security(current_level, scan_result):
    """Neu phat hien nhay cam -> nang len 'confidential'. Khong bao gio ha xuong."""
    if scan_result and scan_result.get("is_sensitive"):
        return "confidential"
    return current_level


def merge_scan_results(current, incoming):
    """Merge page/file scans without retaining matched plaintext."""
    current = current or {"is_sensitive": False, "categories": [], "match_counts": {}}
    incoming = incoming or {}
    counts = dict(current.get("match_counts") or {})
    for category, count in (incoming.get("match_counts") or {}).items():
        counts[category] = counts.get(category, 0) + int(count or 0)
    categories = sorted(counts)
    return {"is_sensitive": bool(categories), "categories": categories, "match_counts": counts}


def has_credential_signal(scan_result):
    return bool(CREDENTIAL_CATEGORIES.intersection((scan_result or {}).get("categories") or []))


def requires_mandatory_scan(*department_values):
    """IT content is always scanned, including shared-department overrides."""
    for value in department_values:
        if isinstance(value, (list, tuple, set)):
            candidates = value
        else:
            candidates = re.split(r"[,;|]", str(value or ""))
        if any(str(item or "").strip().upper() == "IT" for item in candidates):
            return True
    return False


def apply_sensitive_quality_policy(report, scan_result):
    """Force successful sensitive ingests to manual review, metadata-only."""
    if not report or report.get("status") != "success" or not (scan_result or {}).get("is_sensitive"):
        return report
    safe_scan = {
        "is_sensitive": True,
        "categories": sorted(set(scan_result.get("categories") or [])),
        "match_counts": {
            str(category): int(count or 0)
            for category, count in (scan_result.get("match_counts") or {}).items()
            if int(count or 0) > 0
        },
    }
    report["sensitive_scan"] = safe_scan
    report["quality_status"] = "needs_review"
    report["quality_label"] = "Cần rà soát thủ công"
    warning = (
        "Phat hien noi dung nhay cam (%s) -> muc mat confidential, can review thu cong."
        % ", ".join(safe_scan["categories"])
    )
    if warning not in report.setdefault("warnings", []):
        report["warnings"].append(warning)
    code = "credential_detected" if has_credential_signal(safe_scan) else "sensitive_content_detected"
    reason_codes = report.setdefault("quality_reason_codes", [])
    if code not in reason_codes:
        reason_codes.append(code)
        report.setdefault("quality_reasons", []).append({
            "code": code,
            "component": "sensitive_scan",
            "message": "Nội dung nhạy cảm cần rà soát trước khi publish.",
        })
    return report
