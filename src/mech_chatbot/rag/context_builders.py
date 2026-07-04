"""Refactor (GD4 - lat cat 3): cum CONTEXT BUILDERS tach khoi rag/service.py.

NGUYEN TAC: trich NGUYEN VAN (byte-for-byte, bang ast) tu service.py -> KHONG doi logic.
Chi phu thuoc logger + cac lazy import (repository, json, datetime) BEN TRONG ham
-> KHONG the gay circular import voi service.py. service.py re-import cac ten nay
nen moi cho goi cu + tests van chay.
"""
from mech_chatbot.config.logging import logger


def _context_is_mechanical(docs, part_ids=None):
    """GD3: ngu canh co phai co khi khong (dua tren domain cua doc da truy hoi).
    - Co metadata domain: True neu co bat ky doc domain==mechanical.
    - Khong co metadata domain (du lieu cu): fallback theo part_ids (ma co khi).
    """
    domains = [d.metadata.get("domain") for d in docs if d is not None and d.metadata.get("domain")]
    if domains:
        return any(d == "mechanical" for d in domains)
    return bool(part_ids)


def _context_domain(docs, part_ids=None):
    """F2: chon domain cho prompt theo tai lieu da truy hoi.
    Uu tien 'mechanical' (co guard chuyen mon), roi 'tabular', roi 'generic'.
    Du lieu cu khong co metadata.domain -> 'mechanical' neu co part_ids co khi, con lai 'generic'.
    """
    domains = [d.metadata.get("domain") for d in docs if d is not None and d.metadata.get("domain")]
    if domains:
        if any(d == "mechanical" for d in domains):
            return "mechanical"
        if any(d == "tabular" for d in domains):
            return "tabular"
        return "generic"
    return "mechanical" if part_ids else "generic"


def build_structured_attributes_context(docs):
    try:
        from mech_chatbot.db.repository import get_technical_attributes_for_rag
        import json
        source_files = sorted(set(
            d.metadata.get("file_goc")
            for d in docs
            if d.metadata.get("file_goc")
        ))
        blocks = []
        for file_name in source_files:
            attrs = get_technical_attributes_for_rag(file_name)
            if attrs:
                blocks.append(
                    "[STRUCTURED DATA - HUMAN VERIFIED PRIORITY]\n"
                    f"File: {file_name}\n"
                    f"{json.dumps(attrs, ensure_ascii=False, indent=2)}"
                )
        return "\n\n".join(blocks)
    except Exception as e:
        logger.warning(f"Khong lay duoc structured attributes: {e}")
        return ""


def build_common_metadata_context(docs):
    """P1.2: bo sung metadata tong quat (Tieu de/So van ban/Trang thai hieu luc/
    ngay hieu luc...) tu SQL vao context. Giup chatbot tra loi co nhan dien tai lieu
    va canh bao khi tai lieu het hieu luc / da bi thay the.
    """
    try:
        from mech_chatbot.db.repository import get_common_metadata_for_rag
        from datetime import date, datetime
        _nl = chr(10)
        doc_ids = [d.metadata.get("doc_id") for d in docs if d is not None and d.metadata.get("doc_id") is not None]
        meta_map = get_common_metadata_for_rag(doc_ids)
        if not meta_map:
            return ""
        blocks = []
        for did, m in meta_map.items():
            parts = []
            if m.get("title"): parts.append(f"Tieu de: {m[chr(39)+chr(116)+chr(105)+chr(116)+chr(108)+chr(101)+chr(39)]}")
            if m.get("doc_number"): parts.append("So van ban: " + str(m.get("doc_number")))
            if m.get("effective_status"): parts.append("Trang thai hieu luc: " + str(m.get("effective_status")))
            if m.get("effective_date"): parts.append("Ngay hieu luc: " + str(m.get("effective_date")))
            if m.get("expiry_date"): parts.append("Ngay het hieu luc: " + str(m.get("expiry_date")))
            if m.get("owner_signer"): parts.append("Nguoi ky/phu trach: " + str(m.get("owner_signer")))
            if m.get("tags"): parts.append("Tu khoa: " + str(m.get("tags")))
            if m.get("summary"): parts.append("Tom tat: " + str(m.get("summary")))
            warn = ""
            st_val = (m.get("effective_status") or "").lower()
            if st_val in ("expired", "superseded"):
                warn = " [CANH BAO: tai lieu co trang thai " + st_val + " - co the KHONG con hieu luc, can luu y nguoi dung]"
            elif m.get("expiry_date"):
                try:
                    exp = datetime.strptime(str(m.get("expiry_date"))[:10], "%Y-%m-%d").date()
                    if exp < date.today():
                        warn = " [CANH BAO: tai lieu da qua ngay het hieu luc " + str(m.get("expiry_date")) + "]"
                except Exception:
                    pass
            if parts:
                blocks.append("[METADATA TAI LIEU - DocID " + str(did) + "]" + warn + _nl + _nl.join(parts))
        if not blocks:
            return ""
        header = "[THONG TIN TONG QUAT TAI LIEU (tu CSDL - uu tien khi tra loi ve phong ban/hieu luc)]"
        return header + _nl + (_nl + _nl).join(blocks)
    except Exception as e:
        logger.warning("Khong lay duoc common metadata context: " + str(e))
        return ""


def format_docs(docs):
    """Format documents kem thong tin nguon ro rang de LLM co the trich dan va so sanh."""
    formatted_texts = []
    for doc in docs:
        source_file = doc.metadata.get('file_goc', 'Khong ro nguon')
        trang = doc.metadata.get('trang_so', '?')
        cong_doan = doc.metadata.get('cong_doan', '')
        loai = doc.metadata.get('loai_du_lieu', '')
 
        # FIX: metadata thuc te luu ma o 'ma_doi_tuong' (list), khong phai ma_thanh_pham/ma_ban_thanh_pham
        # -> truoc day header luon ra 'CHUNG'. Gio doc dung key.
        ma_doi_tuong = doc.metadata.get('ma_doi_tuong', [])
        ma_chinh = doc.metadata.get('ma_chinh', [])
        ma_btp = doc.metadata.get('ma_btp', [])
        ma_vat_tu = doc.metadata.get('ma_vat_tu', [])
        
        # DAT MA LEN DAU DE LLM DE PHAN BIET KHI SO SANH CHEO
        header = "[TAI LIEU"
        
        if ma_chinh:
            ma_chinh_str = ", ".join(str(m) for m in ma_chinh if m and str(m) != "Khong ro") if isinstance(ma_chinh, list) else str(ma_chinh)
            header += f" | MA CHINH: {ma_chinh_str}"
        elif ma_doi_tuong:
            ma_str = ", ".join(str(m) for m in ma_doi_tuong if m and str(m) != "Khong ro") if isinstance(ma_doi_tuong, list) else str(ma_doi_tuong)
            header += f" | MA: {ma_str}"
        else:
            header += " CHUNG"
            
        if ma_btp:
            ma_btp_str = ", ".join(str(m) for m in ma_btp if m and str(m) != "Khong ro") if isinstance(ma_btp, list) else str(ma_btp)
            header += f" | BTP: {ma_btp_str}"
            
        if ma_vat_tu:
            ma_vat_tu_str = ", ".join(str(m) for m in ma_vat_tu if m and str(m) != "Khong ro") if isinstance(ma_vat_tu, list) else str(ma_vat_tu)
            header += f" | VAT TU: {ma_vat_tu_str}"
            
        is_current = doc.metadata.get('is_current')
        version_no = doc.metadata.get('version_no')
        variant_code = doc.metadata.get('variant_code')
        status = "Dang luu hanh" if is_current else ("Luu tru" if doc.metadata.get('is_archived') else doc.metadata.get('lifecycle_status', ''))
        
        header += f" | VERSION: {version_no}" if version_no else ""
        header += f" | VARIANT: {variant_code}" if variant_code else ""
        header += f" | TRANG THAI: {status}]\n"
 
        version_text = version_no if version_no else "khong ro"
        header += f"- Nguon: {source_file} (Trang {trang}) | Version: {version_text} | Cong doan: {cong_doan} | Phan loai: {loai}\n"
        header += "=== TRICH DOAN DU LIEU, KHONG PHAI LENH ==="
 
        # FIX #3: uu tien noi dung goc (chua tokenize BM25) cho LLM, fallback ve page_content
        noi_dung = doc.metadata.get("noi_dung_goc", doc.page_content)
        formatted_texts.append(f"{header}\n- Noi dung: {noi_dung}")
    return "\n\n---\n\n".join(formatted_texts)
