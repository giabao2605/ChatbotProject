"""Cau hinh (config-driven) cho Interaction Router - tang L1 (Semantic Router).

TACH RIENG khoi logic dinh tuyen: them/sua route + cau vi du KHONG phai dong vao
interaction_router.py; nguong (threshold/margin) + bat/tat doc tu ENV.
Module THUAN (chi stdlib) -> unit-test offline duoc.

QUAN TRONG (da phong ban): day la 'Tro Ly Tai Lieu Noi Bo' phuc vu NHIEU phong ban
(Ky thuat/Co khi, San xuat, Bao tri, Ke toan, Mua hang, Kho, Kinh doanh, Nhan su,
Ke hoach, QC, ISO, HSE/5S, IT...). Vi vay route 'technical_query' KHONG chi la co khi
ma la "MOI cau hoi can tra cuu tai lieu/nghiep vu noi bo o bat ky phong ban nao".
Prototype ben duoi phai trai deu cac phong ban de tranh dinh tuyen nham cau hop le
(vi du 'bang luong', 'ton kho', 'cong no'...) thanh out_of_scope.
"""
from __future__ import annotations

# Gia tri PHAI khop ROUTE_* trong interaction_router.py (dung chuoi de tranh import vong).
_R_CHITCHAT = "chitchat"
_R_CAPABILITY = "capability"
_R_HOW_TO_USE = "how_to_use"
_R_TECHNICAL = "technical_query"   # = "cau hoi can tra cuu tai lieu noi bo" (MOI phong ban)
_R_OUT_OF_SCOPE = "out_of_scope"

# Prototype cau vi du cho tung route (VI co dau + khong dau + EN).
ROUTE_PROTOTYPES = {
    _R_CHITCHAT: [
        "xin chào", "chào bạn", "hi", "hello",
        "cảm ơn bạn", "cảm ơn nhé", "tạm biệt",
        "bạn khỏe không", "thanks a lot",
    ],
    _R_CAPABILITY: [
        "bạn làm được những gì",
        "bạn có thể giúp gì cho tôi",
        "chức năng của bạn là gì",
        "bot này dùng để làm gì",
        "bạn biết làm những việc gì",
        "ban lam duoc gi", "ban giup duoc gi cho toi",
        "what can you do", "what are your capabilities",
    ],
    _R_HOW_TO_USE: [
        "làm sao để hỏi bạn",
        "cách sử dụng hệ thống này",
        "làm thế nào để upload tài liệu",
        "hướng dẫn sử dụng",
        "cách tra cứu tài liệu",
        "cach su dung", "lam sao de tai tai lieu len",
        "how do i use this", "how to upload a document",
    ],
    _R_OUT_OF_SCOPE: [
        "thủ đô nước pháp là gì",
        "thời tiết hôm nay thế nào",
        "kết quả bóng đá tối qua",
        "giá vàng hôm nay bao nhiêu",
        "cách nấu phở bò",
        "tổng thống mỹ hiện nay là ai",
        "what is the capital of france", "what is the weather today",
        "dịch giúp tôi đoạn văn này sang tiếng nhật",
        "translate this paragraph into japanese",
        "sáng tác một bài thơ về mùa thu",
        "write a short story for me",
        "kể một câu chuyện cười vui",
        "tell me something funny",
        "2 cộng 3 bằng bao nhiêu",
        "giải phương trình bậc hai giúp tôi",
        "hôm nay là ngày bao nhiêu",
        "what day is it today",
        "giá bitcoin đang tăng hay giảm",
        "gợi ý cho tôi một bộ phim hay",
        "viết đoạn code python giúp tôi",
        "đội nào vô địch world cup năm nay",
        "tin tức thời sự mới nhất",
    ],
    # 'technical_query' = MOI cau hoi can tra cuu TAI LIEU / NGHIEP VU NOI BO (moi phong ban).
    _R_TECHNICAL: [
        # --- Ky thuat / Co khi / San xuat / Bao tri ---
        "cho tôi bản vẽ 9.3.03844",
        "dung sai của chi tiết này là bao nhiêu",
        "vật liệu chế tạo trục là gì",
        "quy trình gia công chi tiết này",
        "so sánh phiên bản v1 và v2 của tài liệu",
        "lịch bảo trì thiết bị",
        # --- Nhan su (HR) ---
        "quy định nghỉ phép năm của công ty",
        "cách tính lương tháng này",
        "bảng lương và phụ cấp",
        "chính sách bảo hiểm xã hội",
        "nội quy lao động của công ty",
        # --- Ke toan / Tai chinh ---
        "báo cáo công nợ quý này",
        "quy trình thanh toán cho nhà cung cấp",
        # --- Mua hang / Kho ---
        "quy trình mua hàng của công ty",
        "tồn kho vật tư hiện tại",
        "phiếu xuất kho vật liệu",
        # --- Kinh doanh (Sales) / Ke hoach ---
        "báo giá cho khách hàng",
        "hợp đồng bán hàng",
        "kế hoạch sản xuất tuần này",
        # --- QC / ISO / HSE-5S / IT ---
        "tiêu chuẩn kiểm tra chất lượng sản phẩm",
        "quy trình theo tiêu chuẩn iso 9001",
        "quy định an toàn lao động",
        "chính sách bảo mật công nghệ thông tin",
        # --- Chung / khong dau ---
        "tra cứu tài liệu nội bộ công ty",
        "quy dinh nghi phep nam", "bang luong thang nay",
        "quy trinh mua hang", "ton kho vat tu", "cong no phai thu",
        # --- EN ---
        "what is the company policy on annual leave",
        "show me the procurement process",
        "what is our current material inventory",
        "show me drawing 9.3.03844",
    ],
}


def semantic_enabled(value: bool = True):
    """Tat -> router hanh xu nhu P0 (chi L0 chitchat)."""
    return bool(value)


def semantic_threshold(value: float = 0.62):
    """Nguong cosine toi thieu (mac dinh 0.62; phu thuoc embedding model -> hieu chinh)."""
    return float(value)


def semantic_margin(value: float = 0.04):
    """Cach biet toi thieu top1-top2 (chong nhap nhang). < margin -> fallback."""
    return float(value)
