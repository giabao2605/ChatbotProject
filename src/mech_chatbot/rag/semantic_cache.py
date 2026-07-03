"""P2-9: Semantic cache cho cau hoi lap lai (giam cost GPT).

- Ton trong RBAC: chi tai su dung entry co CUNG chu ky pham vi (scope_signature).
- TTL theo env; invalidation: kiem tra tai lieu nguon con hien hanh (IsCurrent + published) luc doc.
- Do luong: hit-rate + tien tiet kiem (bang SemanticCacheStat).
"""
import os
import json
import math


def enabled():
    return os.getenv("SEMANTIC_CACHE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "y", "on"}


def sim_threshold():
    try:
        return float(os.getenv("SEMANTIC_CACHE_SIM_THRESHOLD", "0.93"))
    except Exception:
        return 0.93


def ttl_hours():
    try:
        return float(os.getenv("SEMANTIC_CACHE_TTL_HOURS", "24"))
    except Exception:
        return 24.0


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def scope_signature(user_department, allowed_departments, max_security_level, allowed_sites, user_roles):
    roles = sorted([str(r).lower() for r in (user_roles or [])])
    if "admin" in roles:
        return "admin"
    deps = sorted(set([str(d) for d in (allowed_departments or []) if d] +
                      ([str(user_department)] if user_department else [])))
    sites = sorted(set([str(s) for s in (allowed_sites or []) if s]))
    lvl = str(max_security_level or "public")
    return "d=" + ",".join(deps) + "|lvl=" + lvl + "|s=" + ",".join(sites)


def select_best(candidates, embedding, threshold):
    best = None
    best_s = 0.0
    for c in candidates:
        s = cosine(embedding, c.get("embedding"))
        if s > best_s:
            best_s = s
            best = c
    if best is not None and best_s >= threshold:
        return best, best_s
    return None, best_s


def lookup(question, embedding, scope_sig):
    from mech_chatbot.db.repository import (
        sc_get_candidates, sc_docs_all_current, sc_record_lookup, sc_record_hit, sc_delete,
    )
    try:
        cands = sc_get_candidates(scope_sig, ttl_hours())
    except Exception:
        cands = []
    parsed = []
    for c in cands:
        try:
            emb = c.get("embedding")
            c["embedding"] = json.loads(emb) if isinstance(emb, str) else emb
            if c["embedding"]:
                parsed.append(c)
        except Exception:
            continue
    best, score = select_best(parsed, embedding, sim_threshold())
    if not best:
        try:
            sc_record_lookup(False, 0.0)
        except Exception:
            pass
        return None
    try:
        doc_ids = json.loads(best.get("source_doc_ids") or "[]")
    except Exception:
        doc_ids = []
    if doc_ids:
        try:
            still_ok = sc_docs_all_current(doc_ids)
        except Exception:
            still_ok = True
        if not still_ok:
            try:
                sc_delete(best["cache_id"])
            except Exception:
                pass
            try:
                sc_record_lookup(False, 0.0)
            except Exception:
                pass
            return None
    saved = 0.0
    try:
        saved = float(best.get("est_cost") or 0.0)
    except Exception:
        saved = 0.0
    try:
        sc_record_hit(best["cache_id"], saved)
    except Exception:
        pass
    try:
        sc_record_lookup(True, saved)
    except Exception:
        pass
    try:
        ref_images = json.loads(best.get("ref_images") or "[]")
    except Exception:
        ref_images = []
    return {"answer": best.get("answer") or "", "ref_text": best.get("ref_text") or "",
            "ref_images": ref_images, "score": score}


_REFUSAL_MARKERS = ["khong ghi thong tin", "khong tu uoc luong", "tai lieu hien tai",
                    "khong du", "insufficient", "do not contain", "cannot answer"]


def _looks_like_refusal(answer):
    try:
        from unicodedata import normalize, category
        a = "".join(ch for ch in normalize("NFD", str(answer or "").lower()) if category(ch) != "Mn")
        return any(m in a for m in _REFUSAL_MARKERS)
    except Exception:
        return False


def store(question, embedding, answer, ref_text, ref_images, source_doc_ids, scope_sig, model, est_cost):
    if not enabled() or not answer or not str(answer).strip():
        return
    if _looks_like_refusal(answer):
        return
    try:
        from mech_chatbot.db.repository import sc_put
        sc_put(
            question=question,
            embedding=json.dumps([round(float(x), 6) for x in (embedding or [])]),
            answer=answer, ref_text=ref_text or "",
            ref_images=json.dumps(ref_images or []),
            source_doc_ids=json.dumps([int(x) for x in (source_doc_ids or []) if x is not None]),
            scope_sig=scope_sig, model=model, est_cost=float(est_cost or 0.0),
        )
    except Exception:
        pass


def teeing_store_stream(inner, question, embedding, scope_sig, ref_text, ref_images,
                        source_doc_ids, model, input_char_len=0):
    chunks = []
    try:
        for ch in inner:
            chunks.append(ch)
            yield ch
    finally:
        try:
            answer = "".join(str(c) for c in chunks)
            out_tok = len(answer) // 4
            in_tok = int(input_char_len) // 4
            est = (in_tok * 2.5 + out_tok * 15) / 1000000.0
            store(question, embedding, answer, ref_text, ref_images, source_doc_ids, scope_sig, model, est)
        except Exception:
            pass
