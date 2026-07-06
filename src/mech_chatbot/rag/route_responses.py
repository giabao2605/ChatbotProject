"""Phan hoi DONG cho route "meta" (capability / how_to_use / out_of_scope) + safety_block.

P1: thay cau tra loi TINH bang phan hoi sinh theo ngon ngu (vi/en) + phong ban/quyen (RBAC).
P2: them phan hoi cho safety_block (tu choi lich su, khong lo chi tiet). Module THUAN
(khong model/DB/LLM) -> unit-test offline duoc.

DANH TINH (da phong ban): 'Tro Ly Tai Lieu Noi Bo' phuc vu NHIEU phong ban - khop voi
NEUTRAL_SYSTEM_PROMPT trong service.py. KHONG mo ta bot nhu "tro ly co khi" nua, tranh
tu choi nham cac cau hoi nghiep vu cua cac phong ban khac (nhan su, ke toan, mua hang...).
"""
from __future__ import annotations

ROUTE_CAPABILITY = "capability"
ROUTE_HOW_TO_USE = "how_to_use"
ROUTE_OUT_OF_SCOPE = "out_of_scope"
ROUTE_SAFETY_BLOCK = "safety_block"


def _is_en(lang):
    return str(lang or "").strip().lower().startswith("en")


def _scope_phrase(user_department, allowed_departments, en):
    deps = []
    for d in (allowed_departments or []):
        if d and str(d).strip():
            deps.append(str(d).strip())
    if user_department and str(user_department).strip() and str(user_department).strip() not in deps:
        deps.insert(0, str(user_department).strip())
    if not deps:
        return ""
    joined = ", ".join(deps)
    if en:
        return " You currently have access to documents of: %s." % joined
    return " Bạn đang được tra cứu tài liệu thuộc: %s." % joined


def _capability(lang, user_department, allowed_departments):
    en = _is_en(lang)
    scope = _scope_phrase(user_department, allowed_departments, en)
    if en:
        return (
            "I'm the company's Internal Document Assistant, serving many departments "
            "(Engineering/Mechanical, Production, Maintenance, Accounting, Purchasing, "
            "Warehouse, Sales, HR, Planning, QC, ISO, HSE/5S, IT...). I can help you "
            "look up documents, processes, policies and figures; compare document "
            "versions; and search internal documents within your permissions."
            + scope + " Just ask, or upload a document for me to learn from."
        )
    return (
        "Mình là Trợ lý Tài liệu Nội bộ của công ty, phục vụ nhiều phòng ban "
        "(Kỹ thuật/Cơ khí, Sản xuất, Bảo trì, Kế toán, Mua hàng, Kho, Kinh doanh, "
        "Nhân sự, Kế hoạch, QC, ISO, HSE/5S, IT...). Mình có thể giúp bạn tra cứu "
        "tài liệu, quy trình, chính sách và số liệu; so sánh phiên bản tài liệu; "
        "và tra cứu tài liệu nội bộ trong phạm vi quyền của bạn."
        + scope + " Bạn cứ hỏi, hoặc upload tài liệu để mình học thêm nhé."
    )


def _how_to_use(lang, user_department, allowed_departments):
    if _is_en(lang):
        return (
            "How to use me: (1) Type your question with a keyword or document code "
            "(e.g. a drawing code, a process name, a policy...); "
            "(2) Upload a PDF document so I can learn from it; "
            "(3) You can ask follow-up questions - I remember the conversation context. "
            "To compare versions, state them clearly (e.g. v1 vs v2)."
        )
    return (
        "Cách dùng: (1) Gõ câu hỏi kèm từ khóa hoặc mã tài liệu "
        "(ví dụ mã bản vẽ, tên quy trình, chính sách...); "
        "(2) Upload tài liệu PDF để mình bổ sung kiến thức; "
        "(3) Bạn có thể hỏi nối tiếp - mình nhớ ngữ cảnh cuộc trò chuyện. "
        "Muốn so sánh phiên bản, hãy nêu rõ (ví dụ v1 và v2)."
    )


def _out_of_scope(lang, user_department, allowed_departments):
    if _is_en(lang):
        return (
            "That question is outside my scope. I'm the company's Internal Document "
            "Assistant and only answer based on the company's internal documents "
            "(processes, policies, technical documents, figures...). Please ask about "
            "internal documents or business content."
        )
    return (
        "Câu hỏi này nằm ngoài phạm vi hỗ trợ của mình. "
        "Mình là Trợ lý Tài liệu Nội bộ của công ty, chỉ trả lời dựa trên "
        "tài liệu nội bộ (quy trình, chính sách, tài liệu kỹ thuật, số liệu...). "
        "Bạn thử hỏi về tài liệu hoặc nghiệp vụ nội bộ nhé."
    )


def _safety(lang, user_department, allowed_departments):
    if _is_en(lang):
        return (
            "I can't help with that request. I'm the company's Internal Document "
            "Assistant - please keep questions related to the company's internal "
            "documents and business content (processes, policies, technical documents, "
            "figures...)."
        )
    return (
        "Mình không thể hỗ trợ yêu cầu này. Mình là Trợ lý Tài liệu Nội bộ của "
        "công ty, bạn vui lòng giữ nội dung câu hỏi liên quan đến tài liệu/nghiệp vụ "
        "nội bộ (quy trình, chính sách, tài liệu kỹ thuật, số liệu...) nhé."
    )


_BUILDERS = {
    ROUTE_CAPABILITY: _capability,
    ROUTE_HOW_TO_USE: _how_to_use,
    ROUTE_OUT_OF_SCOPE: _out_of_scope,
}


def is_meta_route(route):
    return route in _BUILDERS


def build_meta_response(route, lang="vi", user_department=None, allowed_departments=None):
    fn = _BUILDERS.get(route)
    if fn is None:
        return ""
    return fn(lang, user_department, allowed_departments)


def build_safety_response(lang="vi", user_department=None, allowed_departments=None):
    return _safety(lang, user_department, allowed_departments)
