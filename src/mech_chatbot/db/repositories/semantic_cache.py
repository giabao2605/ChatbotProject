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
    'sc_get_exact',
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
def sc_put(question, embedding, answer, ref_text, ref_images, source_doc_ids, scope_sig, model, est_cost,
           question_hash=None, citation_snapshot=None, evidence_snapshot=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dbo.SemanticCache
                    (QuestionText, QuestionHash, Embedding, Answer, RefText, RefImages,
                     SourceDocIDs, ScopeSig, Model, EstCost, CitationSnapshotJson, EvidenceSnapshotJson)
                VALUES (:q, :qh, :emb, :a, :rt, :ri, :sd, :sc, :m, :ec, :citations, :evidence)
            """), {"q": _cap_len(question, 2000), "qh": _cap_len(question_hash, 64), "emb": embedding, "a": answer, "rt": ref_text,
                    "ri": ref_images, "sd": source_doc_ids, "sc": _cap_len(scope_sig, 400),
                    "m": _cap_len(model, 100), "ec": est_cost,
                    "citations": citation_snapshot, "evidence": evidence_snapshot})
    except Exception as e:
        logger.error(f"sc_put loi: {e}", exc_info=True)


def sc_get_candidates(scope_sig, ttl_hours, limit=300):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT TOP (:lim) CacheID, Embedding, Answer, RefText, RefImages, SourceDocIDs, EstCost,
                                  CitationSnapshotJson, EvidenceSnapshotJson
                FROM dbo.SemanticCache
                WHERE ScopeSig = :sc AND CreatedAt >= DATEADD(hour, -:ttl, GETDATE())
                ORDER BY CreatedAt DESC
            """), {"lim": int(limit), "sc": scope_sig, "ttl": int(ttl_hours)}).fetchall()
        return [{"cache_id": r[0], "embedding": r[1], "answer": r[2], "ref_text": r[3],
                 "ref_images": r[4], "source_doc_ids": r[5], "est_cost": r[6],
                 "citation_snapshot": r[7], "evidence_snapshot": r[8]} for r in rows]
    except Exception as e:
        logger.error(f"sc_get_candidates loi: {e}", exc_info=True)
        return []


def sc_get_exact(scope_sig, question_hash, normalized_question, ttl_hours):
    """Fast path by hash; legacy rows without hash get one exact-text fallback."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT TOP 1 CacheID, Answer, RefText, RefImages,
                                 SourceDocIDs, EstCost, QuestionHash,
                                 CitationSnapshotJson, EvidenceSnapshotJson
                    FROM dbo.SemanticCache
                    WHERE ScopeSig = :scope
                      AND CreatedAt >= DATEADD(hour, -:ttl, GETDATE())
                      AND (
                          QuestionHash = :question_hash
                          OR (
                              QuestionHash IS NULL
                              AND LOWER(LTRIM(RTRIM(QuestionText))) = :normalized_question
                          )
                      )
                    ORDER BY CASE WHEN QuestionHash = :question_hash THEN 0 ELSE 1 END,
                             CreatedAt DESC
                    """
                ),
                {
                    "scope": scope_sig,
                    "ttl": int(ttl_hours),
                    "question_hash": question_hash,
                    "normalized_question": normalized_question,
                },
            ).fetchone()
            if row and not row[6]:
                conn.execute(
                    text(
                        "UPDATE dbo.SemanticCache SET QuestionHash = :question_hash WHERE CacheID = :cache_id"
                    ),
                    {"question_hash": question_hash, "cache_id": row[0]},
                )
        if not row:
            return None
        return {
            "cache_id": row[0],
            "answer": row[1],
            "ref_text": row[2],
            "ref_images": row[3],
            "source_doc_ids": row[4],
            "est_cost": row[5],
            "citation_snapshot": row[7],
            "evidence_snapshot": row[8],
        }
    except Exception as e:
        logger.error(f"sc_get_exact loi: {e}", exc_info=True)
        return None


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
                "AND Servable = 1 AND PublicationState = 'published' "
                "AND IsCurrent = 1 AND LifecycleStatus = 'published' "
                "AND ReviewStatus = 'approved' "
                "AND LOWER(ISNULL(EffectiveStatus, 'effective')) NOT IN ('expired','superseded','draft') "
                "AND (EffectiveDate IS NULL OR EffectiveDate <= CAST(GETDATE() AS DATE)) "
                "AND (ExpiryDate IS NULL OR ExpiryDate >= CAST(GETDATE() AS DATE))"
            )).fetchone()
        return (row[0] or 0) == len(ids)
    except Exception as e:
        logger.error(f"sc_docs_all_current loi: {e}", exc_info=True)
        # Cache is only a shortcut. If source state cannot be revalidated,
        # fail closed and force normal retrieval instead of serving stale data.
        return False


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
