"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger

__all__ = [
    '_refresh_material_cache',
    'add_material_synonym',
    'delete_material',
    'delete_material_synonym',
    'list_materials',
    'upsert_material',
]

# ============================ P2: MATERIAL DICTIONARY ============================
# CRUD cho tu dien ma vat tu / dong nghia (quan tri qua UI trang 'materials').
# Sau moi thay doi -> refresh cache cua material_registry de co hieu luc ngay.

def _refresh_material_cache():
    try:
        from mech_chatbot.db.registry_ports import refresh_cache
        refresh_cache()
    except Exception:
        pass


def list_materials():
    """Tra ve list vat lieu kem dong nghia: [{material_id, code, display, category, is_active, synonyms:[...]}]."""
    _ensure_engine()
    with engine.connect() as conn:
        mats = conn.execute(text(
            "SELECT MaterialID, CanonicalCode, DisplayName, Category, IsActive "
            "FROM dbo.MaterialDictionary ORDER BY CanonicalCode"
        )).fetchall()
        syns = conn.execute(text(
            "SELECT SynonymID, MaterialID, Synonym, IsActive FROM dbo.MaterialSynonym ORDER BY Synonym"
        )).fetchall()
    syn_by_mat = {}
    for sid, mid, syn, act in syns:
        syn_by_mat.setdefault(mid, []).append(
            {"synonym_id": sid, "synonym": syn, "is_active": bool(act)}
        )
    return [
        {
            "material_id": m[0], "code": m[1], "display": m[2], "category": m[3],
            "is_active": bool(m[4]), "synonyms": syn_by_mat.get(m[0], []),
        }
        for m in mats
    ]


def upsert_material(code, display=None, category=None, is_active=True, material_id=None):
    """Them moi hoac cap nhat 1 vat lieu chuan. Match theo material_id (sua) hoac CanonicalCode (them)."""
    _ensure_engine()
    code = (code or "").strip()
    if not code:
        return False
    display = (display or code).strip()
    act = 1 if is_active else 0
    try:
        with engine.begin() as conn:
            if material_id:
                conn.execute(text(
                    "UPDATE dbo.MaterialDictionary SET CanonicalCode=:c, DisplayName=:d, "
                    "Category=:cat, IsActive=:a, UpdatedAt=GETDATE() WHERE MaterialID=:id"
                ), {"c": code, "d": display, "cat": category, "a": act, "id": material_id})
            else:
                exists = conn.execute(text(
                    "SELECT MaterialID FROM dbo.MaterialDictionary WHERE CanonicalCode=:c"
                ), {"c": code}).fetchone()
                if exists:
                    conn.execute(text(
                        "UPDATE dbo.MaterialDictionary SET DisplayName=:d, Category=:cat, "
                        "IsActive=:a, UpdatedAt=GETDATE() WHERE MaterialID=:id"
                    ), {"d": display, "cat": category, "a": act, "id": exists[0]})
                else:
                    conn.execute(text(
                        "INSERT INTO dbo.MaterialDictionary (CanonicalCode, DisplayName, Category, IsActive) "
                        "VALUES (:c, :d, :cat, :a)"
                    ), {"c": code, "d": display, "cat": category, "a": act})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"upsert_material loi cho '{code}': {e}", exc_info=True)
        return False


def delete_material(material_id):
    """Xoa 1 vat lieu (dong nghia tu xoa theo CASCADE)."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.MaterialDictionary WHERE MaterialID=:id"), {"id": material_id})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"delete_material loi cho id {material_id}: {e}", exc_info=True)
        return False


def add_material_synonym(material_id, synonym):
    """Them 1 tu dong nghia cho vat lieu (bo qua neu trung)."""
    _ensure_engine()
    synonym = (synonym or "").strip()
    if not material_id or not synonym:
        return False
    try:
        with engine.begin() as conn:
            exists = conn.execute(text(
                "SELECT SynonymID FROM dbo.MaterialSynonym WHERE MaterialID=:m AND Synonym=:s"
            ), {"m": material_id, "s": synonym}).fetchone()
            if not exists:
                conn.execute(text(
                    "INSERT INTO dbo.MaterialSynonym (MaterialID, Synonym, IsActive) VALUES (:m, :s, 1)"
                ), {"m": material_id, "s": synonym})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"add_material_synonym loi: {e}", exc_info=True)
        return False


def delete_material_synonym(synonym_id):
    """Xoa 1 tu dong nghia."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.MaterialSynonym WHERE SynonymID=:id"), {"id": synonym_id})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"delete_material_synonym loi cho id {synonym_id}: {e}", exc_info=True)
        return False
