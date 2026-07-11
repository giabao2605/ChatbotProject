# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

import os
import re
from mech_chatbot.config.logging import logger, log_trace
from langchain_core.messages import HumanMessage
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, get_llm_model_name
from mech_chatbot.rag.answer_checks import (  # noqa: F401
    _safe_json_loads,
    _extract_numbers,
    extract_units_and_symbols,
    has_unsupported_units_symbols,
    KNOWN_MATERIALS,
    _known_materials,
    extract_known_materials,
    has_unsupported_materials,
    extract_codes,
    has_unsupported_codes,
    requires_source_citation,
    has_required_source_citation,
)

# cross-module (owned) refs
from mech_chatbot.rag.prompt import _normalize_lang
STRICT_ANSWER_MODE = os.getenv("STRICT_ANSWER_MODE", "true").strip().lower() in {
    "1", "true", "yes", "on"
}


RISKY_QUESTION_KEYWORDS = [
    "bao lau", "thoi gian", "may gio", "bao nhieu gio", "bao nhieu ngay",
    "mat bao lau", "mat may ngay", "mat may gio", "lead time", "cycle time",
    "chi phi", "gia", "bao nhieu tien", "don gia",
    "nang suat", "san luong", "dinh muc", "mot ngay", "1 ngay", "moi gio", "moi ca",
    "uoc tinh", "du kien", "du doan", "khoang bao nhieu",
    "co dat", "dat chuan", "tieu chuan", "kiem dinh",
    "thay duoc", "thay the", "vat lieu khac", "tuong duong",
]


TIME_EVIDENCE_PATTERNS = [
    r"thoi\s*gian\s*(?:gia\s*cong|san\s*xuat|che\s*tao|lap\s*rap|xu\s*ly)",
    r"(?:gia\s*cong|san\s*xuat|che\s*tao|lap\s*rap).{0,40}(?:gio|phut|ngay|ca)",
    r"(?:\d+(?:[\.,]\d+)?\s*)(?:gio|h|phut|p|ngay|ca)\b",
    r"nang\s*suat|dinh\s*muc|cycle\s*time|lead\s*time|takt\s*time",
]


COST_EVIDENCE_PATTERNS = [r"chi\s*phi|don\s*gia|gia\s*thanh|bao\s*gia|vnd|usd|dong"]


STANDARD_EVIDENCE_PATTERNS = [r"tieu\s*chuan|standard|iso|jis|astm|kiem\s*tra|nghiem\s*thu|qc|qa"]


MATERIAL_SUB_EVIDENCE_PATTERNS = [r"thay\s*the|tuong\s*duong|co\s*the\s*thay|vat\s*lieu\s*thay|alternative"]


def _norm(text):
    from mech_chatbot.rag.text_utils import remove_accents
    return remove_accents(str(text or "").lower())


def is_high_risk_question(question):
    q = _norm(question)
    return any(kw in q for kw in RISKY_QUESTION_KEYWORDS) or bool(re.search(r"\b\d{3,}\b", q))


def _has_any_pattern(text, patterns):
    t = _norm(text)
    return any(re.search(pat, t, flags=re.IGNORECASE | re.DOTALL) for pat in patterns)


def heuristic_missing_evidence_reason(question, context_text):
    """Chan nhanh cac cau hoi bay ma context ro rang khong co du kien can thiet."""
    q = _norm(question)
    ctx = _norm(context_text)
    if not ctx.strip():
        return "khong co du lieu tai lieu lien quan trong he thong"

    asks_time = any(kw in q for kw in [
        "bao lau", "thoi gian", "may gio", "bao nhieu gio", "bao nhieu ngay",
        "mat bao lau", "mat may ngay", "mat may gio", "lead time", "cycle time",
        "nang suat", "san luong", "dinh muc", "moi gio", "moi ca", "mot ngay", "1 ngay"
    ])
    if asks_time and not _has_any_pattern(ctx, TIME_EVIDENCE_PATTERNS):
        return "tai lieu khong ghi thoi gian gia cong/nang suat/dinh muc san xuat"

    asks_cost = any(kw in q for kw in ["chi phi", "gia", "bao nhieu tien", "don gia"])
    if asks_cost and not _has_any_pattern(ctx, COST_EVIDENCE_PATTERNS):
        return "tai lieu khong ghi chi phi/don gia/gia thanh"

    asks_standard = any(kw in q for kw in ["co dat", "dat chuan", "tieu chuan", "kiem dinh"])
    if asks_standard and not _has_any_pattern(ctx, STANDARD_EVIDENCE_PATTERNS):
        return "tai lieu khong ghi tieu chuan/ket qua kiem tra de ket luan dat hay khong dat"

    asks_material_sub = any(kw in q for kw in ["thay duoc", "thay the", "vat lieu khac", "tuong duong"])
    if asks_material_sub and not _has_any_pattern(ctx, MATERIAL_SUB_EVIDENCE_PATTERNS):
        return "tai lieu khong ghi thong tin vat lieu thay the/tuong duong"

    return None


def make_insufficient_evidence_message(question, reason, lang="vi"):
    if _normalize_lang(lang) == "en":
        return (
            f"The current documents do not contain enough information to answer this question ({reason}).\n\n"
            "I will not estimate or fabricate data. To get an answer, please load documents with directly relevant data, "
            "such as machining time per product, hourly/shift productivity, production norms, costs or applicable inspection standards."
        )
    return (
        f"Tài liệu hiện tại không ghi thông tin đủ để trả lời câu hỏi này ({reason}).\n\n"
        "Mình sẽ không tự ước lượng hoặc tự bịa số liệu. Để trả lời được, bạn cần bổ sung tài liệu có dữ kiện trực tiếp liên quan, "
        "ví dụ thời gian gia công cho 1 sản phẩm, năng suất theo giờ/ca, định mức sản xuất, chi phí hoặc tiêu chuẩn kiểm tra tương ứng."
    )


def verify_answerability(question, context_text, docs=None, trace_id=None):
    """LLM evidence gate: kiem tra co du bang chung truc tiep truoc khi cho final answer."""
    if not STRICT_ANSWER_MODE and not is_high_risk_question(question):
        return True, "", []

    quick_reason = heuristic_missing_evidence_reason(question, context_text)
    if quick_reason:
        return False, quick_reason, []

    # Final prompt, source-citation gate and deterministic post-check already
    # validate the generated answer. A second GPT call here doubles latency and
    # can exhaust provider capacity; keep it as an explicit opt-in for audits.
    if os.getenv("LLM_EVIDENCE_VERIFIER_ENABLED", "false").strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        return True, "deterministic_evidence_gate_passed", []

    verifier_prompt = f"""
Ban la bo kiem dinh RAG cho chatbot ky thuat co khi. Nhiem vu: kiem tra CONTEXT co DU BANG CHUNG TRUC TIEP de tra loi QUESTION hay khong.

QUY TAC NGHIEM NGAT:
- Neu QUESTION yeu cau thoi gian, chi phi, nang suat, san luong, dat/khong dat, vat lieu thay the, hoac tinh toan, CONTEXT phai co day du du kien dau vao.
- Khong duoc xem viec tim dung ma ban ve la du bang chung neu thong tin duoc hoi khong xuat hien trong CONTEXT.
- Neu thieu du kien, answerable=false.
- Tra ve JSON hop le, khong markdown.

QUESTION:
{question}

CONTEXT:
{context_text[:12000]}

Chi tra ve DUNG 1 JSON object theo schema sau, khong them text ngoai JSON:

  "answerable": true,
  "reason": "ly do ngan gon",
  "evidence_quotes": ["trich dan ngan tu CONTEXT neu co"]


"""
    try:
        evidence_docs = list(docs or [])
        response = cohere_invoke(
            [HumanMessage(content=verifier_prompt)],
            surface="evidence_verification",
            trace_id=trace_id,
            doc_ids=[(getattr(doc, "metadata", {}) or {}).get("doc_id") for doc in evidence_docs],
            security_levels=[(getattr(doc, "metadata", {}) or {}).get("security_level") for doc in evidence_docs],
            policies=[(getattr(doc, "metadata", {}) or {}).get("external_processing_policy") or "all_external" for doc in evidence_docs],
        ).content
        data = _safe_json_loads(response)
        if not isinstance(data, dict):
            logger.warning("Evidence gate khong parse duoc JSON, fallback cho phep final answer nhung van dung prompt nghiem ngat.")
            return True, "", []
        answerable = bool(data.get("answerable"))
        reason = str(data.get("reason") or "tai lieu khong co bang chung truc tiep")
        quotes = data.get("evidence_quotes") or []
        # if answerable and not quotes:
        #     # Cau hoi rui ro ma verifier khong dua duoc quote -> khong cho qua.
        #     return False, "khong tim thay trich dan bang chung truc tiep trong tai lieu", []
        return answerable, reason, quotes if isinstance(quotes, list) else []
    except Exception as e:
        logger.warning(f"Evidence gate loi ({e}). Fallback sang heuristic/prompt nghiem ngat.")
        return True, "", []


def has_unsupported_numbers(answer, context_text, question, strict_mode=False):
    """
    Chặn số liệu mới do LLM tự tạo.

    Nếu strict_mode=True thì kiểm tra mọi câu trả lời kỹ thuật,
    không chỉ câu hỏi high-risk.
    """
    if not strict_mode and not is_high_risk_question(question):
        return False

    answer_nums = _extract_numbers(answer)
    if not answer_nums:
        return False

    allowed_nums = _extract_numbers(context_text) | _extract_numbers(question)

    # Bỏ qua số thứ tự / heading markdown phổ biến
    harmless = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"}

    unsupported = {
        n for n in answer_nums
        if n not in allowed_nums and n not in harmless
    }

    if unsupported:
        logger.warning(
            f"Post-check chan cau tra loi vi co so lieu khong co nguon: {sorted(unsupported)}"
        )
        return True

    return False

__all__ = [
    'RISKY_QUESTION_KEYWORDS',
    'TIME_EVIDENCE_PATTERNS',
    'COST_EVIDENCE_PATTERNS',
    'STANDARD_EVIDENCE_PATTERNS',
    'MATERIAL_SUB_EVIDENCE_PATTERNS',
    '_norm',
    'is_high_risk_question',
    '_has_any_pattern',
    'heuristic_missing_evidence_reason',
    'make_insufficient_evidence_message',
    'verify_answerability',
    'has_unsupported_numbers',
]
