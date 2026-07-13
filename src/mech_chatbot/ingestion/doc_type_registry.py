"""P2 - Chuan hoa NHAN LOAI TAI LIEU (document type) + da ngon ngu.

Van de truoc day: LoaiTaiLieu luu free-text tieng Viet KHONG dau tu LLM
("Ban ve gia cong", "So tay ISO"...) tron lan voi ma tieng Anh tu classifier
("technical_drawing", "invoice"...). Khong nhat quan -> kho loc/thong ke.

Giai phap: 1 bo MA CHUAN (canonical code) co nhan hien thi da ngon ngu (vi/en),
kem danh sach synonym (ke ca tieng Viet co/khong dau, tieng Anh) de chuan hoa
bat ky chuoi dau vao nao ve dung 1 ma.

Dung boi:
  - repository.save_page_metadata / update_document_full_metadata (chuan hoa khi luu)
  - ui/pages/documents.py, admin.py (hien thi nhat quan, ke ca du lieu cu)
"""
import re
import unicodedata

# code -> {"vi": <nhan tieng Viet co dau>, "en": <nhan tieng Anh>, "synonyms": [...]}
# synonyms nen viet thuong, khong dau (se duoc so khop sau khi bo dau).
DOC_TYPES = {
    "technical_drawing": {
        "vi": "Bản vẽ kỹ thuật", "en": "Technical drawing",
        "synonyms": ["technical drawing", "drawing", "ban ve", "ban ve ky thuat",
                     "ban ve gia cong", "ban ve co khi", "bản vẽ"],
    },
    "bom": {
        "vi": "Bảng kê vật tư (BOM)", "en": "Bill of materials",
        "synonyms": ["bom", "bill of materials", "bang ke", "bang ke vat tu",
                     "danh muc vat tu", "bảng kê vật tư"],
    },
    "process": {
        "vi": "Quy trình / Hướng dẫn", "en": "Process / Instruction",
        "synonyms": ["process", "procedure", "sop", "instruction", "quy trinh",
                     "huong dan", "so tay", "so tay iso", "work instruction"],
    },
    "catalog": {
        "vi": "Catalog / Tài liệu kỹ thuật", "en": "Catalog / Datasheet",
        "synonyms": ["catalog", "catalogue", "datasheet", "data sheet",
                     "tai lieu ky thuat", "thong so ky thuat"],
    },
    "invoice": {
        "vi": "Hóa đơn", "en": "Invoice",
        "synonyms": ["invoice", "hoa don", "hóa đơn"],
    },
    "contract": {
        "vi": "Hợp đồng", "en": "Contract",
        "synonyms": ["contract", "hop dong", "hợp đồng", "agreement"],
    },
    "payroll": {
        "vi": "Bảng lương", "en": "Payroll",
        "synonyms": ["payroll", "bang luong", "bảng lương", "luong", "salary"],
    },
    "decision": {
        "vi": "Quyết định", "en": "Decision",
        "synonyms": ["decision", "quyet dinh", "quyết định"],
    },
    "report": {
        "vi": "Báo cáo", "en": "Report",
        "synonyms": ["report", "bao cao", "báo cáo"],
    },
    "form": {
        "vi": "Biểu mẫu", "en": "Form",
        "synonyms": ["form", "bieu mau", "biểu mẫu", "mau don", "template"],
    },
    "generic": {
        "vi": "Tài liệu tổng hợp", "en": "General document",
        "synonyms": ["generic", "other", "khac", "tai lieu tong hop",
                     "tai lieu chung", "khong ro", "unknown"],
    },
    # Wave 2: kho, ke toan, kinh doanh, ke hoach.
    "spreadsheet": {"vi": "Bảng tính", "en": "Spreadsheet", "synonyms": ["bang tinh", "excel"]},
    "inventory_report": {"vi": "Báo cáo tồn kho", "en": "Inventory report", "synonyms": ["bao cao ton kho"]},
    "goods_receipt": {"vi": "Phiếu nhập kho", "en": "Goods receipt", "synonyms": ["phieu nhap kho"]},
    "goods_issue": {"vi": "Phiếu xuất kho", "en": "Goods issue", "synonyms": ["phieu xuat kho"]},
    "stock_card": {"vi": "Thẻ kho", "en": "Stock card", "synonyms": ["the kho"]},
    "transfer_form": {"vi": "Phiếu chuyển kho", "en": "Transfer form", "synonyms": ["phieu chuyen kho"]},
    "payment_request": {"vi": "Đề nghị thanh toán", "en": "Payment request", "synonyms": ["de nghi thanh toan"]},
    "ledger": {"vi": "Sổ cái", "en": "Ledger", "synonyms": ["so cai"]},
    "financial_report": {"vi": "Báo cáo tài chính", "en": "Financial report", "synonyms": ["bao cao tai chinh"]},
    "tax_document": {"vi": "Tài liệu thuế", "en": "Tax document", "synonyms": ["tai lieu thue"]},
    "quotation": {"vi": "Báo giá", "en": "Quotation", "synonyms": ["bao gia", "quote"]},
    "purchase_order": {"vi": "Đơn mua hàng", "en": "Purchase order", "synonyms": ["don mua hang", "purchase order", "po"]},
    "supplier_report": {"vi": "Báo cáo nhà cung cấp", "en": "Supplier report", "synonyms": ["bao cao nha cung cap"]},
    "sales_order": {"vi": "Đơn hàng bán", "en": "Sales order", "synonyms": ["don hang ban"]},
    "customer_report": {"vi": "Báo cáo khách hàng", "en": "Customer report", "synonyms": ["bao cao khach hang"]},
    "revenue_report": {"vi": "Báo cáo doanh thu", "en": "Revenue report", "synonyms": ["bao cao doanh thu"]},
    "production_plan": {"vi": "Kế hoạch sản xuất", "en": "Production plan", "synonyms": ["ke hoach san xuat"]},
    "demand_plan": {"vi": "Kế hoạch nhu cầu", "en": "Demand plan", "synonyms": ["ke hoach nhu cau"]},
    "schedule": {"vi": "Lịch tiến độ", "en": "Schedule", "synonyms": ["lich tien do"]},
    "material_plan": {"vi": "Kế hoạch nguyên vật liệu", "en": "Material plan", "synonyms": ["ke hoach nguyen vat lieu"]},
    # Wave 3: san xuat, bao tri, chat luong, ISO.
    "work_instruction": {"vi": "Hướng dẫn công việc", "en": "Work instruction", "synonyms": ["huong dan cong viec"]},
    "production_order": {"vi": "Lệnh sản xuất", "en": "Production order", "synonyms": ["lenh san xuat"]},
    "process_sheet": {"vi": "Phiếu quy trình", "en": "Process sheet", "synonyms": ["phieu quy trinh"]},
    "routing_sheet": {"vi": "Phiếu công đoạn", "en": "Routing sheet", "synonyms": ["phieu cong doan"]},
    "setup_sheet": {"vi": "Phiếu thiết lập", "en": "Setup sheet", "synonyms": ["phieu thiet lap"]},
    "quality_record": {"vi": "Hồ sơ chất lượng", "en": "Quality record", "synonyms": ["ho so chat luong"]},
    "maintenance_plan": {"vi": "Kế hoạch bảo trì", "en": "Maintenance plan", "synonyms": ["ke hoach bao tri"]},
    "maintenance_schedule": {"vi": "Lịch bảo trì", "en": "Maintenance schedule", "synonyms": ["lich bao tri"]},
    "maintenance_record": {"vi": "Hồ sơ bảo trì", "en": "Maintenance record", "synonyms": ["ho so bao tri"]},
    "equipment_manual": {"vi": "Hướng dẫn thiết bị", "en": "Equipment manual", "synonyms": ["huong dan thiet bi"]},
    "inspection_checklist": {"vi": "Danh sách kiểm tra", "en": "Inspection checklist", "synonyms": ["danh sach kiem tra"]},
    "spare_parts_list": {"vi": "Danh sách phụ tùng", "en": "Spare parts list", "synonyms": ["danh sach phu tung"]},
    "technical_instruction": {"vi": "Hướng dẫn kỹ thuật", "en": "Technical instruction", "synonyms": ["huong dan ky thuat"]},
    "procedure": {"vi": "Quy trình", "en": "Procedure", "synonyms": ["quy trinh"]},
    "quality_standard": {"vi": "Tiêu chuẩn chất lượng", "en": "Quality standard", "synonyms": ["tieu chuan chat luong"]},
    "inspection_plan": {"vi": "Kế hoạch kiểm tra", "en": "Inspection plan", "synonyms": ["ke hoach kiem tra"]},
    "inspection_record": {"vi": "Hồ sơ kiểm tra", "en": "Inspection record", "synonyms": ["ho so kiem tra"]},
    "nonconformance_report": {"vi": "Báo cáo không phù hợp", "en": "Nonconformance report", "synonyms": ["bao cao khong phu hop", "ncr"]},
    "corrective_action": {"vi": "Hành động khắc phục", "en": "Corrective action", "synonyms": ["hanh dong khac phuc", "capa"]},
    "certificate": {"vi": "Chứng nhận", "en": "Certificate", "synonyms": ["chung nhan"]},
    "policy": {"vi": "Chính sách", "en": "Policy", "synonyms": ["chinh sach"]},
    "record": {"vi": "Hồ sơ", "en": "Record", "synonyms": ["ho so"]},
    "audit_report": {"vi": "Báo cáo đánh giá", "en": "Audit report", "synonyms": ["bao cao danh gia"]},
    "nonconformity": {"vi": "Điểm không phù hợp", "en": "Nonconformity", "synonyms": ["diem khong phu hop"]},
    "management_review": {"vi": "Xem xét của lãnh đạo", "en": "Management review", "synonyms": ["xem xet cua lanh dao"]},
    "manual": {"vi": "Sổ tay", "en": "Manual", "synonyms": ["so tay"]},
    # Wave 4: khuon, HSE/5S va cong nghe thong tin.
    "mold_drawing": {"vi": "Bản vẽ khuôn", "en": "Mold drawing", "synonyms": ["ban ve khuon", "mould drawing"]},
    "mold_specification": {"vi": "Thông số khuôn", "en": "Mold specification", "synonyms": ["thong so khuon", "mould specification"]},
    "material_specification": {"vi": "Đặc tính vật liệu", "en": "Material specification", "synonyms": ["dac tinh vat lieu", "thong so vat lieu"]},
    "safety_rule": {"vi": "Quy định an toàn", "en": "Safety rule", "synonyms": ["quy dinh an toan", "noi quy an toan"]},
    "risk_assessment": {"vi": "Đánh giá rủi ro", "en": "Risk assessment", "synonyms": ["danh gia rui ro"]},
    "work_permit": {"vi": "Giấy phép làm việc", "en": "Work permit", "synonyms": ["giay phep lam viec"]},
    "incident_report": {"vi": "Báo cáo sự cố", "en": "Incident report", "synonyms": ["bao cao su co"]},
    "emergency_plan": {"vi": "Kế hoạch ứng phó khẩn cấp", "en": "Emergency plan", "synonyms": ["ke hoach ung pho khan cap", "phuong an khan cap"]},
    "training_record": {"vi": "Hồ sơ đào tạo", "en": "Training record", "synonyms": ["ho so dao tao"]},
    "5s_audit": {"vi": "Đánh giá 5S", "en": "5S audit", "synonyms": ["danh gia 5s", "audit 5s"]},
    "system_guide": {"vi": "Hướng dẫn hệ thống", "en": "System guide", "synonyms": ["huong dan he thong"]},
    "network_diagram": {"vi": "Sơ đồ mạng", "en": "Network diagram", "synonyms": ["so do mang"]},
    "asset_inventory": {"vi": "Danh mục tài sản IT", "en": "IT asset inventory", "synonyms": ["danh muc tai san it", "kiem ke tai san it"]},
    "access_request": {"vi": "Yêu cầu cấp quyền", "en": "Access request", "synonyms": ["yeu cau cap quyen"]},
    "change_record": {"vi": "Hồ sơ thay đổi", "en": "Change record", "synonyms": ["ho so thay doi", "ghi nhan thay doi"]},
    "backup_restore": {"vi": "Sao lưu và phục hồi", "en": "Backup and restore", "synonyms": ["sao luu phuc hoi", "backup restore"]},
    "security_standard": {"vi": "Tiêu chuẩn bảo mật", "en": "Security standard", "synonyms": ["tieu chuan bao mat", "tieu chuan an toan thong tin"]},
    "other": {"vi": "Tài liệu khác", "en": "Other", "synonyms": ["khac"]},
}

DEFAULT_CODE = "generic"
SUPPORTED_LANGS = ("vi", "en")


def strip_accents(s):
    """Bo dau tieng Viet + ha thuong + gom khoang trang -> de so khop synonym."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("đ", "d").replace("Đ", "D")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


# Map synonym (da bo dau) -> code, build 1 lan.
_SYN_INDEX = None


def _syn_index():
    global _SYN_INDEX
    if _SYN_INDEX is None:
        idx = {}
        for code, meta in DOC_TYPES.items():
            idx[strip_accents(code)] = code
            idx[strip_accents(meta["vi"])] = code
            idx[strip_accents(meta["en"])] = code
            for syn in meta.get("synonyms", []):
                idx[strip_accents(syn)] = code
        _SYN_INDEX = idx
    return _SYN_INDEX


def normalize_doc_type(raw):
    """Tra ve canonical code, hoac None neu khong nhan dien duoc."""
    key = strip_accents(raw)
    if not key:
        return None
    idx = _syn_index()
    if key in idx:
        return idx[key]
    # So khop chua (substring) - uu tien synonym dai truoc
    for syn in sorted(idx.keys(), key=len, reverse=True):
        if syn and syn in key:
            return idx[syn]
    return None


def doc_type_label(code, lang="vi"):
    """Nhan hien thi cho 1 canonical code."""
    meta = DOC_TYPES.get(code)
    if not meta:
        return None
    return meta.get(lang) or meta.get("vi") or code


def canonical_label(raw, lang="vi"):
    """Chuan hoa 1 chuoi bat ky ve nhan hien thi chuan.
    Neu khong nhan dien -> giu nguyen raw (title-case nhe) de khong mat thong tin.
    """
    code = normalize_doc_type(raw)
    if code:
        return doc_type_label(code, lang)
    raw = (str(raw).strip() if raw else "")
    return raw or doc_type_label(DEFAULT_CODE, lang)


def list_doc_types(lang="vi"):
    """[{code, label}] cho UI (vd dropdown loc/sua)."""
    return [{"code": c, "label": doc_type_label(c, lang)} for c in DOC_TYPES]
