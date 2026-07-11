"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from . import audit as _r_audit
from . import qdrant as _r_qdrant
from . import semantic_cache as _r_semantic_cache

__all__ = [
    '_COMMON_META_COLS',
    '_apply_upload_meta_to_doc',
    '_clean_meta_value',
    'get_common_metadata_for_rag',
    'get_document_attributes',
    'get_document_metadata',
    'set_document_attributes',
    'update_document_common_metadata',
]

# ==========================================================================
# P0: METADATA TONG QUAT DA PHONG BAN (common fields + DocumentAttributes)
# ==========================================================================

# Map: key trong upload_meta/JSON  ->  ten cot tren TaiLieu
_COMMON_META_COLS = {
    "title": "Title",
    "summary": "Summary",
    "tags": "Tags",
    "doc_number": "DocNumber",
    "issued_date": "IssuedDate",
    "effective_date": "EffectiveDate",
    "expiry_date": "ExpiryDate",
    "review_date": "ReviewDate",
    "owner_signer": "OwnerSigner",
    "language": "DocLanguage",
    "effective_status": "EffectiveStatus",
    "site": "Site",
}

# Governance fields live on TaiLieu because they are part of the publication
# contract, not merely optional presentation metadata.
_GOVERNANCE_META_COLS = {
    "knowledge_owner_user_id": "KnowledgeOwnerUserID",
    "knowledge_approver_user_id": "KnowledgeApproverUserID",
    "taxonomy_version": "TaxonomyVersion",
    "parent_applicable": "ParentApplicable",
    "parent_section": "ParentSection",
    "parent_page": "ParentPage",
}


def _clean_meta_value(v):
    """Chuan hoa gia tri: '' -> None, strip chuoi."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _apply_upload_meta_to_doc(conn, doc_id, upload_meta_json, domain):
    """Ap metadata nhap luc upload (JSON tren IngestionJobs) xuong TaiLieu +
    DocumentAttributes. Chay TRONG cung transaction cua _get_or_create_doc.

    JSON dang: {"title":..., "summary":..., ..., "attributes": {key: value}}
    - Cac key common -> cot TaiLieu (chi ghi de khi co gia tri, tranh xoa du lieu).
    - attributes -> DocumentAttributes (ExtractedBy='manual'), thay the ban manual cu.
    """
    if not upload_meta_json:
        return
    try:
        import json as _json
        meta = _json.loads(upload_meta_json)
    except Exception:
        return
    if not isinstance(meta, dict):
        return
    try:
        sets, params = [], {"d": doc_id}
        for k, col in _COMMON_META_COLS.items():
            if k in meta:
                val = _clean_meta_value(meta.get(k))
                if val is not None:
                    sets.append(f"{col} = :{k}")
                    params[k] = val
        if sets:
            conn.execute(text("UPDATE TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :d"), params)

        governance_sets, governance_params = [], {"d": doc_id}
        for key, column in _GOVERNANCE_META_COLS.items():
            if key not in meta:
                continue
            value = meta.get(key)
            if key in {"knowledge_owner_user_id", "knowledge_approver_user_id", "parent_page"}:
                if value in (None, ""):
                    normalized = None
                else:
                    try:
                        normalized = int(value)
                    except (TypeError, ValueError):
                        continue
                    if normalized <= 0:
                        continue
            elif key == "parent_applicable":
                normalized = 1 if bool(value) else 0
            else:
                normalized = _clean_meta_value(value)
            governance_sets.append(f"{column} = :gov_{key}")
            governance_params[f"gov_{key}"] = normalized
        if ("parent_section" in meta or "parent_page" in meta) and "parent_applicable" not in meta:
            governance_sets.append("ParentApplicable = 1")
        if governance_sets:
            conn.execute(
                text("UPDATE TaiLieu SET " + ", ".join(governance_sets) + " WHERE DocID = :d"),
                governance_params,
            )

        attrs = meta.get("attributes") or {}
        if isinstance(attrs, dict) and attrs:
            dom = (domain or "generic")
            # Thay the cac attribute nhap tay truoc do cho doc nay (giu attribute do AI/regex boc tach)
            conn.execute(text("DELETE FROM DocumentAttributes WHERE DocID = :d AND ExtractedBy = 'manual'"), {"d": doc_id})
            for ak, av in attrs.items():
                av = _clean_meta_value(av)
                if av is None:
                    continue
                conn.execute(text("""
                    INSERT INTO DocumentAttributes (DocID, Domain, AttributeKey, AttributeValue, ExtractedBy)
                    VALUES (:d, :dom, :k, :v, 'manual')
                """), {"d": doc_id, "dom": dom, "k": str(ak)[:150], "v": str(av)})
    except Exception as e:
        logger.error(f"Loi _apply_upload_meta_to_doc doc_id={doc_id}: {e}", exc_info=True)


def get_document_attributes(doc_id, domain=None):
    """Tra ve dict {AttributeKey: AttributeValue} cho doc (uu tien ban manual)."""
    if doc_id is None:
        return {}
    _ensure_engine()
    try:
        with engine.connect() as conn:
            q = "SELECT AttributeKey, AttributeValue, ExtractedBy FROM DocumentAttributes WHERE DocID = :d"
            params = {"d": doc_id}
            if domain:
                q += " AND Domain = :dom"
                params["dom"] = domain
            q += " ORDER BY CASE WHEN ExtractedBy = 'manual' THEN 0 ELSE 1 END, AttrID DESC"
            rows = conn.execute(text(q), params).fetchall()
        out = {}
        for k, v, _by in rows:
            if k not in out:  # ban manual (sort truoc) thang the
                out[k] = v
        return out
    except Exception as e:
        logger.error(f"Loi get_document_attributes doc_id={doc_id}: {e}", exc_info=True)
        return {}


def get_document_metadata(doc_id):
    """Tra ve dict metadata tong quat (common fields) + 'attributes' cho 1 doc.
    Dung cho form chinh sua o Kho tai lieu / Duyet."""
    if doc_id is None:
        return {}
    _ensure_engine()
    out = {}
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT Title, Summary, Tags, DocNumber, IssuedDate, EffectiveDate,
                       ExpiryDate, ReviewDate, OwnerSigner, DocLanguage, EffectiveStatus, Site, Domain,
                       KnowledgeOwnerUserID, KnowledgeApproverUserID, TaxonomyVersion,
                       ParentApplicable, ParentSection, ParentPage
                FROM TaiLieu WHERE DocID = :d
            """), {"d": doc_id}).fetchone()
        if row:
            (out["title"], out["summary"], out["tags"], out["doc_number"], out["issued_date"],
             out["effective_date"], out["expiry_date"], out["review_date"], out["owner_signer"],
             out["language"], out["effective_status"], out["site"], out["domain"],
             out["knowledge_owner_user_id"], out["knowledge_approver_user_id"], out["taxonomy_version"],
             out["parent_applicable"], out["parent_section"], out["parent_page"]) = row
            out["parent_applicable"] = bool(out["parent_applicable"])
    except Exception as e:
        logger.error(f"Loi get_document_metadata doc_id={doc_id}: {e}", exc_info=True)
    out["attributes"] = get_document_attributes(doc_id)
    return out


def set_document_attributes(doc_id, domain, attrs, extracted_by="manual"):
    """Upsert cac attribute nhap tay cho 1 doc. attrs = dict {key: value}.
    Thay the toan bo ban 'manual' cu (key bo trong -> xoa)."""
    if doc_id is None:
        return False
    _ensure_engine()
    dom = (domain or "generic")
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM DocumentAttributes WHERE DocID = :d AND ExtractedBy = :by"),
                         {"d": doc_id, "by": extracted_by})
            for ak, av in (attrs or {}).items():
                av = _clean_meta_value(av)
                if av is None:
                    continue
                conn.execute(text("""
                    INSERT INTO DocumentAttributes (DocID, Domain, AttributeKey, AttributeValue, ExtractedBy)
                    VALUES (:d, :dom, :k, :v, :by)
                """), {"d": doc_id, "dom": dom, "k": str(ak)[:150], "v": str(av), "by": extracted_by})
        return True
    except Exception as e:
        logger.error(f"Loi set_document_attributes doc_id={doc_id}: {e}", exc_info=True)
        return False


def update_document_common_metadata(doc_id, reviewer="System", attributes=None, domain=None, **fields):
    """Cap nhat metadata tong quat (common fields) cho 1 doc da ton tai.

    fields nhan cac key trong _COMMON_META_COLS (title, summary, tags, doc_number,
    issued_date, effective_date, expiry_date, review_date, owner_signer, language,
    effective_status). Gia tri None -> BO QUA (khong ghi de); chuoi rong -> xoa (NULL).
    attributes (dict) -> ghi vao DocumentAttributes (ban manual).
    Dong bo mot phan xuong Qdrant payload (title/doc_number/tags/effective_status).
    """
    if doc_id is None:
        return False
    _ensure_engine()
    try:
        sets, params = [], {"d": doc_id}
        for k, col in _COMMON_META_COLS.items():
            if k in fields:
                v = fields.get(k)
                # phan biet: None = bo qua; '' = set NULL (xoa)
                if v is None:
                    continue
                if isinstance(v, str):
                    v = v.strip()
                    params[k] = v or None
                else:
                    params[k] = v
                sets.append(f"{col} = :{k}")
        for k, col in _GOVERNANCE_META_COLS.items():
            if k not in fields:
                continue
            value = fields.get(k)
            if value is None:
                continue
            if k in {"knowledge_owner_user_id", "knowledge_approver_user_id", "parent_page"}:
                if value == "":
                    params[k] = None
                else:
                    try:
                        params[k] = int(value)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"{k} phai la so nguyen hop le") from exc
                    if params[k] <= 0:
                        raise ValueError(f"{k} phai lon hon 0")
            elif k == "parent_applicable":
                params[k] = 1 if bool(value) else 0
            else:
                params[k] = _clean_meta_value(value)
                if k == "taxonomy_version" and not params[k]:
                    raise ValueError("taxonomy_version khong duoc de trong")
            sets.append(f"{col} = :{k}")
        if ("parent_section" in fields or "parent_page" in fields) and "parent_applicable" not in fields:
            sets.append("ParentApplicable = 1")
        if sets:
            with engine.begin() as conn:
                conn.execute(text("UPDATE TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :d"), params)

        if attributes is not None:
            set_document_attributes(doc_id, domain, attributes, extracted_by="manual")

        # Dong bo nhe xuong Qdrant (chi field huu ich cho loc/hien thi)
        qmeta = {}
        for qk in (
            "title", "doc_number", "tags", "effective_status", "site", "taxonomy_version",
            "parent_applicable", "parent_section", "parent_page",
        ):
            if qk in params:
                qmeta[qk] = params[qk]
        if qmeta:
            try:
                _r_qdrant.update_qdrant_metadata(doc_id, qmeta)
            except Exception as _qe:
                logger.warning(f"update_document_common_metadata: dong bo Qdrant loi doc {doc_id}: {_qe}")

        _r_audit.write_audit_log(reviewer, "update_common_metadata", "TaiLieu", doc_id,
                        {"fields": list(params.keys()), "has_attributes": attributes is not None})
        _r_semantic_cache._invalidate_semantic_cache("doc.metadata")
        return True
    except Exception as e:
        logger.error(f"Loi update_document_common_metadata doc_id={doc_id}: {e}", exc_info=True)
        return False


def get_common_metadata_for_rag(doc_ids):
    """Lay metadata tong quat (Title/Summary/Tags/DocNumber/cac moc ngay/EffectiveStatus...)
    cho danh sach DocID, phuc vu RAG. Tra ve {DocID(int): {..}}."""
    out = {}
    ids = []
    for d in (doc_ids or []):
        try:
            ids.append(int(d))
        except Exception:
            continue
    ids = list(dict.fromkeys(ids))
    if not ids or engine is None:
        return out
    try:
        keys, params = [], {}
        for i, did in enumerate(ids):
            kk = f"id{i}"
            params[kk] = did
            keys.append(f":{kk}")
        q = """
            SELECT DocID, Title, Summary, Tags, DocNumber, IssuedDate, EffectiveDate,
                   ExpiryDate, OwnerSigner, EffectiveStatus, DocLanguage
            FROM TaiLieu
            WHERE DocID IN (__IN_CLAUSE__)
        """.replace("__IN_CLAUSE__", ", ".join(keys))
        with engine.connect() as conn:
            rows = conn.execute(text(q), params).fetchall()
        for r in rows:
            out[r[0]] = {
                "title": r[1], "summary": r[2], "tags": r[3], "doc_number": r[4],
                "issued_date": r[5], "effective_date": r[6], "expiry_date": r[7],
                "owner_signer": r[8], "effective_status": r[9], "language": r[10],
            }
    except Exception as e:
        logger.warning(f"get_common_metadata_for_rag loi: {e}")
    return out
