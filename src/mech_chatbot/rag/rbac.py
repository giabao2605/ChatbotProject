"""RBAC filter logic — TACH RIENG khoi rag/service.py.

Muc dich: cac ham phan quyen o day THUAN (chi phu thuoc qdrant_client.models),
KHONG keo theo viec khoi tao RAG (Qdrant client, embedding model, LLM...).
Nho vay unit test import duoc ma KHONG bi tai model / goi mang / crash.

Logic giu NGUYEN BAN tu service.py (khong doi hanh vi).
"""
from qdrant_client import models

from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT

LEVEL_ORDER = {"public": 0, "internal": 1, "confidential": 2}

# Cac key metadata dung de match ma chi tiet (part id) trong Qdrant.
# strict = chi match ma chinh; broad = mo rong sang ma lien quan/btp/vat tu.
PART_ID_KEYS_STRICT = [
    "metadata.base_code",
    "metadata.ma_chinh",
    "metadata.ma_doi_tuong",
]
PART_ID_KEYS_BROAD = [
    "metadata.base_code",
    "metadata.ma_chinh",
    "metadata.ma_btp",
    "metadata.ma_vat_tu",
    "metadata.ma_lien_quan",
    "metadata.ma_doi_tuong",
]


def _allowed_levels(max_security_level):
    order = LEVEL_ORDER.get((max_security_level or "public"), 0)
    return [lvl for lvl, o in LEVEL_ORDER.items() if o <= order]


def _security_filter(max_security_level):
    levels = _allowed_levels(max_security_level)
    # GD5 muc 5: tai lieu THIEU metadata.security_level coi nhu MAT (confidential).
    # Chi user co clearance 'confidential' moi duoc thay tai lieu chua gan muc mat (empty)
    # -> mac dinh an toan.
    allow_empty = "confidential" in levels
    should = []
    if allow_empty:
        should.append(models.IsEmptyCondition(is_empty=models.PayloadField(key="metadata.security_level")))
    should.append(models.FieldCondition(key="metadata.security_level", match=models.MatchAny(any=levels)))
    try:
        return models.Filter(should=should)
    except Exception:
        return models.FieldCondition(key="metadata.security_level", match=models.MatchAny(any=levels))


def _strict_site_enabled():
    """P0#2: doc LAZY co AppSettings 'rbac_strict_site_filter'.
    Import repository TRONG ham de rbac.py van THUAN (unit test import duoc, khong keo DB/RAG).
    Moi loi -> coi nhu strict=False (tuong thich nguoc)."""
    try:
        from mech_chatbot.db.repository import get_app_setting
        raw = get_app_setting("rbac_strict_site_filter", "false")
        return str(raw).strip().lower() in ("true", "1", "yes", "on")
    except Exception:
        return False


def _site_filter(allowed_sites):
    """P1.2 + P0#2: gioi han theo site. List rong/None -> KHONG loc theo site (tuong thich nguoc).
    - STRICT OFF (mac dinh): tai lieu thieu site (metadata.site rong) VAN hien (tranh an du lieu cu).
    - STRICT ON: KHONG cho qua tai lieu thieu site -> chan leak cross-site."""
    sites = [s for s in (allowed_sites or []) if s]
    if not sites:
        return None
    match_cond = models.FieldCondition(key="metadata.site", match=models.MatchAny(any=sites))
    # STRICT ON: chi khop dung site duoc phep, khong noi long cho doc thieu site.
    if _strict_site_enabled():
        return match_cond
    # STRICT OFF: giu hanh vi cu (cho qua doc thieu site).
    try:
        return models.Filter(should=[
            models.IsEmptyCondition(is_empty=models.PayloadField(key="metadata.site")),
            match_cond,
        ])
    except Exception:
        return match_cond


def create_rbac_filter(user_department, user_roles, allowed_departments=None, max_security_level=None, allowed_sites=None):
    # Chi admin moi duoc bo filter (None = khong gioi han)
    if user_roles and "admin" in user_roles:
        return None

    # Khong co role nao -> DENY tat ca
    if not user_roles:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.phong_ban_quyen",
                    match=models.MatchValue(value="__DENY__")
                )
            ]
        )

    allowed = list(allowed_departments) if allowed_departments else []
    if user_department and user_department not in allowed:
        allowed.append(user_department)
    if SHARE_ALL_DEPARTMENT not in allowed:
        allowed.append(SHARE_ALL_DEPARTMENT)

    if not allowed:
        allowed = [SHARE_ALL_DEPARTMENT]

    must = [
        models.FieldCondition(key="metadata.phong_ban_quyen", match=models.MatchAny(any=allowed)),
        _security_filter(max_security_level),
    ]
    site_cond = _site_filter(allowed_sites)
    if site_cond is not None:
        must.append(site_cond)

    return models.Filter(must=must)


def _part_id_should_filter(new_part_ids, broad=False):
    """Dieu kien should match ma chi tiet (part id) tren cac key metadata."""
    keys = PART_ID_KEYS_BROAD if broad else PART_ID_KEYS_STRICT
    return models.Filter(
        should=[
            models.FieldCondition(key=k, match=models.MatchAny(any=new_part_ids))
            for k in keys
        ]
    )


def compose_retrieval_filters(must_conditions, new_part_ids):
    """Ghep (strict_filter, broad_filter) tu must_conditions chung.

    BAT BIEN AN TOAN QUAN TRONG:
    Ca strict va broad deu KE THUA TOAN BO `must_conditions` (trong do co
    rbac_filter). Nhanh broad CHI duoc mo rong dieu kien `should` ve part id,
    TUYET DOI khong duoc bo bot dieu kien `must` -> khong bao gio noi quyen.

    Vi the production PHAI dung ham nay thay vi tu dung lai broad_musts tu dau.
    """
    strict_musts = list(must_conditions)
    # P1: routing da tach khoi trich ma -> new_part_ids KHONG con chua sentinel
    # "CHITCHAT" (chitchat da bi chan tu truoc boi Interaction Router).
    if new_part_ids:
        strict_musts.append(_part_id_should_filter(new_part_ids, broad=False))
    strict_filter = models.Filter(must=strict_musts)

    broad_musts = list(must_conditions)
    broad_musts.append(_part_id_should_filter(new_part_ids, broad=True))
    broad_filter = models.Filter(must=broad_musts)

    return strict_filter, broad_filter
