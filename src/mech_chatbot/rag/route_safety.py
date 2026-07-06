"""Route safety_block - phat hien noi dung KHONG an toan, chay TRUOC pipeline [P2].

Muc tieu: chan prompt injection (chiem quyen / lo system prompt) va lam dung (abuse)
NGAY tu dau, truoc L0/L1/L2 va truoc RAG. Module THUAN (chi stdlib + chitchat.normalize)
-> unit-test offline duoc.

Nguyen tac FAIL-SAFE nguoc: chi chan khi khop RO RANG (cum nhieu tu, chuan hoa bo dau,
ranh gioi tu) de TRANH chan nham cau ky thuat hop le. Cac cum co the mo rong qua ENV
(SAFETY_EXTRA_INJECTION / SAFETY_EXTRA_ABUSE, ngan cach bang dau phay).
"""
from __future__ import annotations

import os

from mech_chatbot.rag import chitchat

REASON_PROMPT_INJECTION = "prompt_injection"
REASON_ABUSE = "abuse"

# Cac cum (DA chuan hoa: bo dau, thuong) dac trung cho prompt injection.
_INJECTION_PHRASES = (
    "bo qua huong dan", "bo qua chi dan", "bo qua moi chi dan", "bo qua cac chi dan",
    "quen het huong dan", "quen cac chi dan", "khong tuan theo huong dan",
    "ignore previous instructions", "ignore all previous", "ignore the above",
    "ignore your instructions", "disregard previous", "disregard all previous",
    "system prompt", "he thong prompt", "in ra prompt", "in ra system prompt",
    "lo prompt", "reveal your prompt", "reveal your system", "show me your prompt",
    "cho toi xem prompt", "tiet lo prompt", "tiet lo system",
    "you are now", "ban bay gio la", "tu gio ban la", "gio ban dong vai",
    "developer mode", "che do nha phat trien", "jailbreak", "dan mode",
    "bypass security", "vuot qua bao mat", "bypass restrictions", "bypass all",
    "pretend to be", "gia vo la", "dong vai mot", "sudo mode",
    "khong con gioi han", "khong bi gioi han nao",
)

# Cum lam dung / quay roi ro rang (khop theo RANH GIOI TU, khong substring lung tung).
_ABUSE_PHRASES = (
    "do ngu", "thang ngu", "con ngu", "do ngoc", "cut di", "im mom", "im di",
    "do cho", "khon nan", "mat day", "stupid bot", "idiot bot", "fuck", "fuck you",
    "shit", "dumb bot",
)


def _norm_list(env_name):
    out = []
    for part in str(os.getenv(env_name, "")).split(","):
        p = chitchat.normalize(part)
        if p:
            out.append(p)
    return tuple(out)


def _contains_phrase(norm, phrase):
    """Khop theo ranh gioi tu (co khoang trang hai ben) de tranh false positive."""
    if not phrase:
        return False
    return (" " + norm + " ").find(" " + phrase + " ") >= 0


def enabled():
    raw = os.getenv("SAFETY_BLOCK_ENABLED")
    if raw is None:
        return True
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def detect(text):
    """Tra ve ly do ('prompt_injection' | 'abuse') neu KHONG an toan, nguoc lai None."""
    if not text or not str(text).strip():
        return None
    norm = chitchat.normalize(text)
    if not norm:
        return None
    # Injection: cum dac trung, khop substring (cum du dai nen it false positive).
    for p in _INJECTION_PHRASES + _norm_list("SAFETY_EXTRA_INJECTION"):
        if p and p in norm:
            return REASON_PROMPT_INJECTION
    # Abuse: khop theo ranh gioi tu.
    for p in _ABUSE_PHRASES + _norm_list("SAFETY_EXTRA_ABUSE"):
        if _contains_phrase(norm, p):
            return REASON_ABUSE
    return None


def is_unsafe(text):
    return detect(text) is not None
