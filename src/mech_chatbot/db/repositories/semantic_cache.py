"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import _cap_len

__all__ = [
    '_invalidate_semantic_cache',
    'sc_clear_all',
    'sc_delete',
    'sc_docs_all_current',
    'sc_get_candidates',
    'sc_put',
    'sc_record_hit',
    'sc_record_lookup',
    'sc_stats',
]

def _invalidate_semantic_cache(reason=""):
    """P0#3: best-effort xoa semantic cache sau mutation (quyen/tai lieu doi).
    KHONG raise ra ngoai de khong lam vo ham chinh."""
    try:
        deleted = sc_clear_all()
        logger.info(f"[semantic-cache] invalidated (reason={reason}, deleted={deleted})")
    except Exception:
        logger.warning(f"[semantic-cache] invalidate loi (reason={reason})", exc_info=True)


# ==========================================================================
# P2-9: SEMANTIC CACHE
# ==========================================================================
def sc_put(question, embedding, answer, ref_text, ref_images, source_doc_ids, scope_sig, model, est_cost):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dbo.SemanticCache
                    (QuestionText, Embedding, Answer, RefText, RefImages, SourceDocIDs, ScopeSig, Model, EstCost)
                VALUES (:q, :emb, :a, :rt, :ri, :sd, :sc, :m, :ec)
            """), {"q": _cap_len(question, 2000), "emb": embedding, "a": answer, "rt": ref_text,
                    "ri": ref_images, "sd": source_doc_ids, "sc": _cap_len(scope_sig, 400),
                    "m": _cap_len(model, 100), "ec": est_cost})
    except Exception as e:
        logger.error(f"sc_put loi: {e}", exc_info=True)


def sc_get_candidates(scope_sig, ttl_hours, limit=300):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT TOP (:lim) CacheID, Embedding, Answer, RefText, RefImages, SourceDocIDs, EstCost
                FROM dbo.SemanticCache
                WHERE ScopeSig = :sc AND CreatedAt >= DATEADD(hour, -:ttl, GETDATE())
                ORDER BY CreatedAt DESC
            """), {"lim": int(limit), "sc": scope_sig, "ttl": int(ttl_hours)}).fetchall()
        return [{"cache_id": r[0], "embedding": r[1], "answer": r[2], "ref_text": r[3],
                 "ref_images": r[4], "source_doc_ids": r[5], "est_cost": r[6]} for r in rows]
    except Exception as e:
        logger.error(f"sc_get_candidates loi: {e}", exc_info=True)
        return []


def sc_docs_all_current(doc_ids):
    _ensure_engine()
    ids = []
    for x in (doc_ids or []):
        try:
            ids.append(int(x))
        except Exception:
            pass
    ids = sorted(set(ids))
    if not ids:
        return True
    try:
        in_clause = ",".join(str(i) for i in ids)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) FROM dbo.TaiLieu WHERE DocID IN (" + in_clause + ") "
                "AND IsCurrent = 1 AND LifecycleStatus = 'published'"
            )).fetchone()
        return (row[0] or 0) == len(ids)
    except Exception as e:
        logger.error(f"sc_docs_all_current loi: {e}", exc_info=True)
        return True


def sc_delete(cache_id):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.SemanticCache WHERE CacheID = :id"), {"id": cache_id})
    except Exception as e:
        logger.error(f"sc_delete loi: {e}", exc_info=True)


def sc_record_hit(cache_id, cost):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE dbo.SemanticCache SET HitCount = HitCount + 1, LastHitAt = GETDATE() WHERE CacheID = :id"),
                         {"id": cache_id})
    except Exception as e:
        logger.error(f"sc_record_hit loi: {e}", exc_info=True)


def sc_record_lookup(hit, cost_saved):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                MERGE dbo.SemanticCacheStat AS tgt USING (SELECT 1 AS StatID) AS src ON tgt.StatID = src.StatID
                WHEN MATCHED THEN UPDATE SET Lookups = Lookups + 1, Hits = Hits + :h, CostSaved = CostSaved + :c
                WHEN NOT MATCHED THEN INSERT (StatID, Lookups, Hits, CostSaved) VALUES (1, 1, :h, :c);
            """), {"h": 1 if hit else 0, "c": float(cost_saved or 0.0)})
    except Exception as e:
        logger.error(f"sc_record_lookup loi: {e}", exc_info=True)


def sc_clear_all():
    _ensure_engine()
    try:
        with engine.begin() as conn:
            res = conn.execute(text("DELETE FROM dbo.SemanticCache"))
            conn.execute(text("UPDATE dbo.SemanticCacheStat SET Lookups = 0, Hits = 0, CostSaved = 0 WHERE StatID = 1"))
        return getattr(res, "rowcount", 0) or 0
    except Exception as e:
        logger.error(f"sc_clear_all loi: {e}", exc_info=True)
        return 0


def sc_stats():
    _ensure_engine()
    out = {"entries": 0, "lookups": 0, "hits": 0, "hit_rate": 0.0, "cost_saved": 0.0}
    try:
        with engine.connect() as conn:
            out["entries"] = conn.execute(text("SELECT COUNT(*) FROM dbo.SemanticCache")).scalar() or 0
            row = conn.execute(text("SELECT Lookups, Hits, CostSaved FROM dbo.SemanticCacheStat WHERE StatID = 1")).fetchone()
        if row:
            lk = row[0] or 0
            ht = row[1] or 0
            out["lookups"] = lk
            out["hits"] = ht
            out["cost_saved"] = round(float(row[2] or 0), 4)
            out["hit_rate"] = round(ht / lk * 100, 1) if lk else 0.0
        return out
    except Exception as e:
        logger.error(f"sc_stats loi: {e}", exc_info=True)
        return out
