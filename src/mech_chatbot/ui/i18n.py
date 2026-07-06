"""i18n module cho Mech Chatbot UI.

Cach dung:
    from mech_chatbot.ui.i18n import t, get_lang, set_lang, language_selector

    t("Xin chao")              -> "Hello" (khi lang=en) / "Xin chao" (khi lang=vi)
    t("Co {n} file", n=3)      -> "There are 3 files" / "Co 3 file"

P2.1: bang dich VI->EN da duoc tach ra file du lieu locales/<lang>.json
(truoc day nhung ~1150 dong dict inline trong chinh file nay). Logic va API
GIU NGUYEN; chi thay nguon du lieu tu inline sang JSON.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any

import streamlit as st

LANGUAGES: dict[str, str] = {"vi": "Tieng Viet", "en": "English"}
DEFAULT_LANG = "vi"

_SESSION_KEY = "_mech_lang"

_LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")


# ---------------------------------------------------------------------------
# Normalization helper
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Chuan hoa: NFD -> strip accents -> lower -> collapse whitespace."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", stripped).strip().lower()


# ---------------------------------------------------------------------------
# Translation dictionary  (VI -> EN) — nap tu locales/<lang>.json
# ---------------------------------------------------------------------------

def _load_locale(lang: str) -> dict[str, str]:
    """Nap bang dich cho mot ngon ngu tu file JSON. Loi/thieu file -> dict rong."""
    path = os.path.join(_LOCALES_DIR, f"{lang}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    # chi giu cac cap chuoi->chuoi (an toan)
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


_EN: dict[str, str] = _load_locale("en")


# ---------------------------------------------------------------------------
# Build lookup: normalized source text -> EN translation
# ---------------------------------------------------------------------------

_TRANSLATIONS: dict[str, dict[str, str]] = {"en": _EN}

_NORM_EN: dict[str, str] = {_norm(k): v for k, v in _EN.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_lang() -> str:
    """Tra ve lang hien tai (vi/en). Mac dinh vi."""
    return st.session_state.get(_SESSION_KEY, DEFAULT_LANG)


def set_lang(lang: str) -> None:
    """Dat lang trong session."""
    if lang in LANGUAGES:
        st.session_state[_SESSION_KEY] = lang


def t(text: str, **kwargs: Any) -> str:
    """Dich text sang ngon ngu hien tai.

    - text: chuoi tieng Viet goc.
    - kwargs: cac placeholder {key} trong chuoi.
    Tra ve chuoi da dich (EN) hoac goc (VI) voi placeholder da dien.
    """
    lang = get_lang()
    result = text
    if lang != DEFAULT_LANG:
        translations = _TRANSLATIONS.get(lang, {})
        # Thu exact match truoc
        translated = translations.get(text)
        if translated is None:
            # Thu normalized match
            translated = _NORM_EN.get(_norm(text))
        if translated is not None:
            result = translated
    if kwargs:
        try:
            result = result.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return result


def language_selector(
    label: str = "Language",
    key: str = "lang_selector",
    sidebar: bool = True,
) -> str:
    """Widget chon ngon ngu; tra ve lang hien tai."""
    container = st.sidebar if sidebar else st
    current = get_lang()
    options = list(LANGUAGES.keys())
    idx = options.index(current) if current in options else 0
    selected = container.selectbox(
        label,
        options,
        index=idx,
        format_func=lambda x: LANGUAGES[x],
        key=key,
    )
    if selected != current:
        set_lang(selected)
        st.rerun()
    return selected
