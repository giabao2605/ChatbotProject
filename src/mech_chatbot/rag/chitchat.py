"""Nhan dien cau xa giao (chitchat) - NGUON DUY NHAT cho toan he thong.

Truoc day logic chitchat bi lap o 3 noi trong service.py voi 3 danh sach tu
khac nhau va 1 ban dung khop CHUOI CON ('hi' in q) -> gay nham lan
(vd 'bao nhieu' chua 'hi'). Module nay gom lai 1 cho, khop theo TOKEN da
chuan hoa (bo dau), khong dung chuoi con.

Module THUAN (khong import model/DB) nen unit-test duoc offline.
"""
from __future__ import annotations

import re
import unicodedata

# --- Chuan hoa: ha thuong + bo dau + bo ky tu dac biet + gom khoang trang ---


def strip_accents(text: str) -> str:
    """Bo dau tieng Viet, xu ly ca chu 'd'."""
    s = str(text or "")
    nfkd = unicodedata.normalize("NFD", s)
    no_mark = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return no_mark.replace("\u0111", "d").replace("\u0110", "D")


def normalize(text: str) -> str:
    t = strip_accents(text).lower()
    t = re.sub(r"[^\w\s]", " ", t)   # bo dau cau
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Cac cau xa giao thuan tuy hay gap (da chuan hoa). Khop NGUYEN CA CAU.
CHITCHAT_PHRASES = {
    "xin chao", "chao", "chao ban", "xin chao ban", "chao ad", "chao shop",
    "hi", "hello", "helo", "hey", "alo", "hola",
    "cam on", "cam on ban", "cam on nhe", "thank you", "thankyou",
    "tam biet", "bye", "goodbye", "ok ban", "chao buoi sang",
}

# Token duoc coi la "tu xa giao". CHU Y: co tinh chon tu KHONG trung voi tu ky
# thuat pho bien (khong dua 'co','ve','so','ban','la','gi','di','thi'...).
GREETING_TOKENS = {
    "xin", "chao", "hi", "hello", "helo", "hey", "alo", "hola",
    "cam", "on", "thank", "thanks", "tks", "ty",
    "ok", "oke", "okie", "okay", "okla",
    "da", "vang", "um", "uh", "uhm", "hihi", "haha",
    "tam", "biet", "bye", "goodbye", "nhe", "nha",
}

# So token toi da de coi la 1 cau chao thuan tuy (tranh chuoi dai bat thuong).
_MAX_GREETING_TOKENS = 4


def is_chitchat(text: str) -> bool:
    """True neu CA cau chi la xa giao (khong kem cau hoi ky thuat).

    Quy tac (an toan, khong dung chuoi con):
      1) Sau chuan hoa, neu trung 1 cau chao quen thuoc -> chitchat.
      2) Hoac cau ngan (<=4 token) va MOI token deu la tu xa giao -> chitchat.
    Bat ky token ky thuat nao (dung sai, truc, ban ve, bao nhieu...) se pha vo
    dieu kien 2 -> KHONG bi nham la chitchat.
    """
    cleaned = normalize(text)
    if not cleaned:
        return False
    if cleaned in CHITCHAT_PHRASES:
        return True
    tokens = cleaned.split()
    if len(tokens) <= _MAX_GREETING_TOKENS and all(t in GREETING_TOKENS for t in tokens):
        return True
    return False


def requires_source_citation(question: str) -> bool:
    """Cau hoi thuc su (khong phai xa giao) thi BAT BUOC trich nguon."""
    return not is_chitchat(question)
