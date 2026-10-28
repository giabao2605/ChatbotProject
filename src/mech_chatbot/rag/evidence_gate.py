# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

import re
from dataclasses import dataclass
from enum import Enum
from mech_chatbot.config.logging import logger, log_trace
from langchain_core.messages import HumanMessage
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, get_llm_model_name
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
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
from mech_chatbot.rag.number_normalization import (
    NUMBER_PATTERN as _NUMBER_PATTERN,
    normalize_number_token as _normalize_number_token,
    normalized_number_values,
)
from mech_chatbot.rag.execution import RequestBudgetExceeded
class EvidenceState(str, Enum):
    SUFFICIENT = "SUFFICIENT"
    AMBIGUOUS = "AMBIGUOUS"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class EvidenceDecision:
    state: EvidenceState
    reason: str = ""
    stage: str = "heuristic"
    evidence_quotes: tuple[str, ...] = ()
    telemetry_status: str = "heuristic_pass"

    @property
    def answerable(self) -> bool:
        return self.state is EvidenceState.SUFFICIENT


@dataclass(frozen=True)
class NumberViolation:
    raw: str
    normalized: str
    start: int
    end: int


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
    r"(?:thoi\s*gian|thoi\s*han|chu\s*ky)[^:\n]{0,100}:\s*[*_ ]*chua\s*xac\s*nhan",
    r"thoi\s*gian\s*(?:gia\s*cong|san\s*xuat|che\s*tao|lap\s*rap|xu\s*ly)",
    r"(?:gia\s*cong|san\s*xuat|che\s*tao|lap\s*rap).{0,40}(?:gio|phut|ngay|ca)",
    r"(?:\d+(?:[\.,]\d+)?\s*)(?:giay|gio|h|phut|p|ngay|ca|tuan|thang|quy|nam)\b",
    r"\b(?:moi|hang)\s+(?:ngay|tuan|thang|quy|nam)\b",
    r"\b(?:thu\s+(?:hai|ba|tu|nam|sau|bay)|chu\s+nhat)\b",
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


def _contains_phrase(text, phrase):
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text))


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

    # These phrases use "gia" as evaluation, value, processing or participation, not price.
    price_question = re.sub(r"\b(?:danh\s+gia|gia\s+(?:tri|cong)|tham\s+gia)\b", " ", q)
    asks_cost = any(
        _contains_phrase(q, keyword)
        for keyword in ["chi phi", "bao nhieu tien", "don gia"]
    ) or _contains_phrase(price_question, "gia")
    if asks_cost and not _has_any_pattern(ctx, COST_EVIDENCE_PATTERNS):
        return "tai lieu khong ghi chi phi/don gia/gia thanh"

    asks_standard = any(kw in q for kw in ["co dat", "dat chuan", "tieu chuan", "kiem dinh"])
    if asks_standard and not _has_any_pattern(ctx, STANDARD_EVIDENCE_PATTERNS):
        return "tai lieu khong ghi tieu chuan/ket qua kiem tra de ket luan dat hay khong dat"

    material_context = any(
        _contains_phrase(q, kw)
        for kw in [
            "vat lieu", "material", "thep", "steel", "aluminum",
            "inox", "copper", "cao su", "rubber", "nylon",
        ]
    )
    asks_material_sub = any(
        kw in q for kw in ["thay duoc", "vat lieu khac", "tuong duong"]
    ) or ("thay the" in q and material_context)
    if asks_material_sub and not _has_any_pattern(ctx, MATERIAL_SUB_EVIDENCE_PATTERNS):
        return "tai lieu khong ghi thong tin vat lieu thay the/tuong duong"

    return None


def make_insufficient_evidence_message(question, reason, lang="vi"):
    if _normalize_lang(lang) == "en":
        return "I could not find enough evidence in the documents you can access to answer this part."
    return "Mình chưa tìm thấy đủ bằng chứng trong tài liệu được phép xem để trả lời phần này."


def evaluate_answerability(
    question,
    context_text,
    docs=None,
    trace_id=None,
    *,
    strict_answer_mode=True,
    crag_enabled=False,
    verifier_enabled=False,
):
    """Return a structured evidence decision for generation or correction."""
    if not strict_answer_mode and not is_high_risk_question(question):
        return EvidenceDecision(EvidenceState.SUFFICIENT, telemetry_status="heuristic_pass")

    quick_reason = heuristic_missing_evidence_reason(question, context_text)
    if quick_reason:
        state = EvidenceState.AMBIGUOUS if crag_enabled and str(context_text).strip() else EvidenceState.INSUFFICIENT
        return EvidenceDecision(
            state,
            reason=quick_reason,
            stage="heuristic",
            telemetry_status="heuristic_block",
        )

    # Final prompt, source-citation gate and deterministic post-check already
    # validate the generated answer. A second GPT call here doubles latency and
    # can exhaust provider capacity; keep it as an explicit opt-in for audits.
    if not verifier_enabled:
        return EvidenceDecision(
            EvidenceState.SUFFICIENT,
            reason="deterministic_evidence_gate_passed",
            stage="verifier",
            telemetry_status="verifier_disabled",
        )

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

Chi tra ve DUNG 1 JSON object theo schema sau, khong them text ngoai JSON. State la SUFFICIENT, AMBIGUOUS hoac INSUFFICIENT:

  "state": "SUFFICIENT",
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
            policies=[(getattr(doc, "metadata", {}) or {}).get("external_processing_policy") or "internal_only" for doc in evidence_docs],
        ).content
        data = _safe_json_loads(response)
        if not isinstance(data, dict):
            raise ValueError("Invalid verifier JSON")
        raw_state = data.get("state")
        if raw_state is not None:
            state = EvidenceState(str(raw_state).strip().upper())
        elif type(data.get("answerable")) is bool:
            state = EvidenceState.SUFFICIENT if data["answerable"] else EvidenceState.INSUFFICIENT
        else:
            raise ValueError("Invalid verifier decision")
        reason = str(data.get("reason") or "tai lieu khong co bang chung truc tiep")
        quotes = data.get("evidence_quotes", [])
        if not isinstance(quotes, list) or any(not isinstance(quote, str) for quote in quotes):
            raise ValueError("Invalid verifier evidence quotes")
        return EvidenceDecision(
            state,
            reason=reason,
            stage="verifier",
            evidence_quotes=tuple(quotes) if isinstance(quotes, list) else (),
            telemetry_status="verifier_pass" if state is EvidenceState.SUFFICIENT else "verifier_block",
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as e:
        logger.warning("Evidence verification unavailable: %s", type(e).__name__)
        return EvidenceDecision(
            EvidenceState.INSUFFICIENT,
            reason="verifier_unavailable",
            stage="verifier",
            telemetry_status="verifier_unavailable",
        )


def verify_answerability(question, context_text, docs=None, trace_id=None):
    """Backward-compatible tuple wrapper around :func:`evaluate_answerability`."""
    decision = evaluate_answerability(question, context_text, docs=docs, trace_id=trace_id)
    return decision.answerable, decision.reason, list(decision.evidence_quotes)


_CORRECTION_UNIT = r"[^\W\d_]+(?:\s+[^\W\d_]+){0,2}"
_REJECTED_NUMBER = (
    rf"[*_]{{0,2}}(?P<rejected>{_NUMBER_PATTERN.pattern})"
    rf"(?:\s+(?P<rejected_unit>{_CORRECTION_UNIT}))?[*_]{{0,2}}"
)
_CORRECTED_NUMBER = (
    rf"[*_]{{0,2}}(?P<corrected>{_NUMBER_PATTERN.pattern})"
    rf"(?:\s+(?P<corrected_unit>{_CORRECTION_UNIT}))?[*_]{{0,2}}"
)
_DIRECT_NUMBER_CORRECTIONS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"(?<!\w)(?:không\s+phải|khong\s+phai|not)\s+{_REJECTED_NUMBER}\s*[,;]?\s*"
        rf"(?:đúng\s+là|dung\s+la|mà\s+là|ma\s+la|but|the\s+correct\s+(?:number|value)\s+is)\s+{_CORRECTED_NUMBER}",
        rf"{_CORRECTED_NUMBER}\s*,\s*(?:không\s+phải|khong\s+phai|not)\s+{_REJECTED_NUMBER}",
    )
)


def find_unsupported_numbers(answer, context_text, question, strict_mode=False):
    """Return positioned number violations after formatting normalization."""
    if not strict_mode and not is_high_risk_question(question):
        return []
    allowed = normalized_number_values(context_text)
    answer_text = str(answer or "")
    question_numbers = normalized_number_values(question)
    # shortcut: only explicit paired corrections are recognized; ambiguous wording stays blocked.
    rejected_spans = {
        match.span("rejected")
        for pattern in _DIRECT_NUMBER_CORRECTIONS
        for match in pattern.finditer(answer_text)
        if _normalize_number_token(match.group("rejected")) in question_numbers
        and _normalize_number_token(match.group("corrected")) in allowed
        and (not match.group("rejected_unit") or not match.group("corrected_unit")
             or _norm(match.group("rejected_unit")) == _norm(match.group("corrected_unit")))
    }
    citation_spans = [
        match.span()
        for match in re.finditer(r"\[(?:Nguồn|Source):[^\]]+\]", answer_text, flags=re.IGNORECASE)
    ]
    violations = []
    for match in _NUMBER_PATTERN.finditer(answer_text):
        if any(start <= match.start() and match.end() <= end for start, end in citation_spans):
            continue
        normalized = _normalize_number_token(match.group(0))
        if normalized in allowed or match.span() in rejected_spans:
            continue
        violations.append(
            NumberViolation(match.group(0), normalized, match.start(), match.end())
        )
    return violations


def has_unsupported_numbers(answer, context_text, question, strict_mode=False):
    """
    Chặn số liệu mới do LLM tự tạo.

    Nếu strict_mode=True thì kiểm tra mọi câu trả lời kỹ thuật,
    không chỉ câu hỏi high-risk.
    """
    violations = find_unsupported_numbers(
        answer, context_text, question, strict_mode=strict_mode
    )
    if violations:
        logger.warning(
            "Post-check chan cau tra loi vi co so lieu khong co nguon: %s",
            [item.normalized for item in violations],
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
    'evaluate_answerability',
    'EvidenceDecision',
    'EvidenceState',
    'has_unsupported_numbers',
    'find_unsupported_numbers',
    'NumberViolation',
    'normalized_number_values',
]
