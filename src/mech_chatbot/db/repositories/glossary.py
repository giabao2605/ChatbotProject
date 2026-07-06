"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import json
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import _cap_len
from . import catalog as _r_catalog
from . import semantic_cache as _r_semantic_cache

__all__ = [
    'delete_glossary_term',
    'get_active_glossary',
    'list_domain_glossary',
    'set_glossary_active',
    'upsert_glossary_term',
]

# ==========================================================================
# P0-3: DOMAIN GLOSSARY / SYNONYM (tu dien dong nghia theo domain)
# ==========================================================================
def get_active_glossary(domains=None):
    """Cac muc glossary dang bat. domains=None -> tat ca; nguoc lai loc theo list domain."""
    _ck = "gloss_active:" + (",".join(sorted(domains)) if domains else "__all__")  # Perf (GD2)
    _c = _r_catalog._catalog_cache_get(_ck)
    if _c is not None:
        return list(_c)
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT GlossaryID, Domain, Term, Synonyms, Expansion FROM dbo.DomainGlossary WHERE IsActive = 1"
            )).fetchall()
        dset = set(domains) if domains else None
        out = []
        for gid, domain, term, syn, exp in rows:
            if dset is not None and domain not in dset:
                continue
            try:
                syn_list = json.loads(syn) if syn else []
                if not isinstance(syn_list, list):
                    syn_list = [str(syn_list)]
            except Exception:
                syn_list = [s.strip() for s in str(syn or "").split(",") if s.strip()]
            out.append({"glossary_id": gid, "domain": domain, "term": term,
                        "synonyms": syn_list, "expansion": exp})
        _r_catalog._catalog_cache_put(_ck, out)
        return list(out)
    except Exception as e:
        logger.error(f"get_active_glossary loi: {e}", exc_info=True)
        return []


def list_domain_glossary(domain=None, active_only=False):
    _ensure_engine()
    try:
        where = []
        params = {}
        if domain:
            where.append("Domain = :d"); params["d"] = domain
        if active_only:
            where.append("IsActive = 1")
        wc = ("WHERE " + " AND ".join(where)) if where else ""
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT GlossaryID, Domain, Term, Synonyms, Expansion, IsActive, CreatedAt "
                "FROM dbo.DomainGlossary " + wc + " ORDER BY Domain, Term"
            ), params).fetchall()
        out = []
        for gid, dm, term, syn, exp, active, created in rows:
            try:
                syn_list = json.loads(syn) if syn else []
                if not isinstance(syn_list, list):
                    syn_list = [str(syn_list)]
            except Exception:
                syn_list = [s.strip() for s in str(syn or "").split(",") if s.strip()]
            out.append({"glossary_id": gid, "domain": dm, "term": term, "synonyms": syn_list,
                        "expansion": exp, "is_active": bool(active), "created_at": created})
        return out
    except Exception as e:
        logger.error(f"list_domain_glossary loi: {e}", exc_info=True)
        return []


def upsert_glossary_term(term, domain, synonyms=None, expansion=None, is_active=True, glossary_id=None):
    _ensure_engine()
    if not term or not domain:
        return {"ok": False, "message": "thieu term hoac domain"}
    syn_json = json.dumps([s for s in (synonyms or []) if s], ensure_ascii=False)
    try:
        with engine.begin() as conn:
            if glossary_id:
                conn.execute(text("""
                    UPDATE dbo.DomainGlossary
                    SET Term = :t, Domain = :d, Synonyms = :s, Expansion = :e, IsActive = :a, UpdatedAt = GETDATE()
                    WHERE GlossaryID = :gid
                """), {"t": _cap_len(term, 255), "d": _cap_len(domain, 50), "s": syn_json,
                        "e": _cap_len(expansion, 1000), "a": 1 if is_active else 0, "gid": glossary_id})
                gid = glossary_id
            else:
                row = conn.execute(text("""
                    INSERT INTO dbo.DomainGlossary (Domain, Term, Synonyms, Expansion, IsActive)
                    OUTPUT INSERTED.GlossaryID
                    VALUES (:d, :t, :s, :e, :a)
                """), {"d": _cap_len(domain, 50), "t": _cap_len(term, 255), "s": syn_json,
                        "e": _cap_len(expansion, 1000), "a": 1 if is_active else 0}).fetchone()
                gid = row[0] if row else None
        _r_semantic_cache._invalidate_semantic_cache("glossary.upsert")
        _r_catalog._catalog_cache_invalidate("gloss_active:")  # Perf (GD2)
        return {"ok": True, "glossary_id": gid}
    except Exception as e:
        logger.error(f"upsert_glossary_term loi: {e}", exc_info=True)
        return {"ok": False, "message": str(e)}


def set_glossary_active(glossary_id, is_active):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE dbo.DomainGlossary SET IsActive = :a, UpdatedAt = GETDATE() WHERE GlossaryID = :gid"),
                         {"a": 1 if is_active else 0, "gid": glossary_id})
        _r_semantic_cache._invalidate_semantic_cache("glossary.active")
        _r_catalog._catalog_cache_invalidate("gloss_active:")  # Perf (GD2)
        return True
    except Exception as e:
        logger.error(f"set_glossary_active loi: {e}", exc_info=True)
        return False


def delete_glossary_term(glossary_id):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.DomainGlossary WHERE GlossaryID = :gid"), {"gid": glossary_id})
        _r_semantic_cache._invalidate_semantic_cache("glossary.delete")
        _r_catalog._catalog_cache_invalidate("gloss_active:")  # Perf (GD2)
        return True
    except Exception as e:
        logger.error(f"delete_glossary_term loi: {e}", exc_info=True)
        return False
