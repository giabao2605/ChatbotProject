"""Refactor (GD4 - lat cat 2): cum GLOSSARY EXPANSION tach khoi rag/service.py.

NGUYEN TAC: trich NGUYEN VAN (byte-for-byte, bang ast) tu service.py -> KHONG doi logic.
Chi phu thuoc stdlib (re, time) + logger, va cac lazy import (domain_registry, repository,
text_utils) BEN TRONG ham -> KHONG the gay circular import voi service.py.
service.py re-import cac ten nay nen moi cho goi cu + tests van chay.
"""
import os
import re
import time

from mech_chatbot.config.logging import logger


_GLOSSARY_TTL = float(os.getenv("GLOSSARY_CACHE_TTL", "60"))


_GLOSSARY_CACHE = {"ts": 0.0, "key": None, "data": []}


def _glossary_domains_for_department(user_department):
    """P0-3: domain glossary ap dung = generic + domain cua phong ban user."""
    domains = ["generic"]
    try:
        if user_department:
            from mech_chatbot.ingestion.domain_registry import resolve_domain_by_department
            d = resolve_domain_by_department(user_department)
            if d and d not in domains:
                domains.append(d)
    except Exception:
        pass
    return domains


def _load_glossary_cached(domains):
    key = tuple(sorted(domains or []))
    now = time.time()
    if _GLOSSARY_CACHE["key"] == key and (now - _GLOSSARY_CACHE["ts"]) < _GLOSSARY_TTL:
        return _GLOSSARY_CACHE["data"]
    try:
        from mech_chatbot.db.repository import get_active_glossary
        data = get_active_glossary(list(key) if key else None)
    except Exception as e:
        logger.warning(f"load glossary loi: {e}")
        data = []
    _GLOSSARY_CACHE["ts"] = now
    _GLOSSARY_CACHE["key"] = key
    _GLOSSARY_CACHE["data"] = data
    return data


def glossary_expansion_terms(text_in, user_department=None):
    """P0-3: tra ve chuoi tu dong nghia/mo rong cho cac term glossary xuat hien trong text_in,
    gioi han theo domain cua phong ban user (+ generic). Khop theo ranh gioi tu."""
    if not text_in:
        return ""
    try:
        from mech_chatbot.rag.text_utils import remove_accents
        domains = _glossary_domains_for_department(user_department)
        entries = _load_glossary_cached(domains)
        if not entries:
            return ""
        norm_q = remove_accents(str(text_in).lower())
        _wb = chr(92) + "b"

        def _has(vn):
            if not vn:
                return False
            try:
                return re.search(_wb + re.escape(vn) + _wb, norm_q) is not None
            except Exception:
                return vn in norm_q

        adds = []
        seen = set()
        for e in entries:
            variants = [e.get("term", "")] + list(e.get("synonyms") or [])
            matched = any(_has(remove_accents(str(v).lower())) for v in variants if v)
            if not matched:
                continue
            extra = list(variants)
            if e.get("expansion"):
                extra.append(e["expansion"])
            for v in extra:
                if not v:
                    continue
                vn = remove_accents(str(v).lower())
                if vn and not _has(vn) and vn not in seen:
                    seen.add(vn)
                    adds.append(str(v))
        return " ".join(adds)
    except Exception as e:
        logger.warning(f"glossary_expansion_terms loi: {e}")
        return ""
