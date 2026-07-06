"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from . import document as _r_document

__all__ = [
    '_REINGEST_SNAPSHOT_TABLES',
    '_reingest_snapshots',
    '_snapshot_document_children',
    'clear_reingest_snapshot',
    'get_technical_attributes_for_rag',
    'reset_document_metadata',
    'restore_document_children',
    'save_document_attributes',
    'save_document_metadata',
    'save_document_page',
    'save_page_metadata',
    'save_technical_attributes',
    'verify_technical_attribute',
]

_REINGEST_SNAPSHOT_TABLES = {
    "TaiLieuKyThuat": "ID",
    "BangKeVatTu": "ID",
    "DocumentPages": "PageID",
    "TechnicalAttributes": "AttributeID",
}
# Snapshot tam thoi (trong bo nho) de khoi phuc du lieu con cu neu re-ingest that bai giua chung.
_reingest_snapshots = {}


def _snapshot_document_children(conn, doc_id):
    """Chup lai cac dong con truoc khi xoa de co the restore neu ingest moi loi."""
    snap = {}
    for tbl in _REINGEST_SNAPSHOT_TABLES:
        try:
            rows = conn.execute(text(f"SELECT * FROM {tbl} WHERE DocID = :d"), {"d": doc_id}).mappings().all()
            snap[tbl] = [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[reingest] snapshot {tbl} loi doc_id={doc_id}: {e}")
            snap[tbl] = []
    return snap


def reset_document_metadata(file_name, thu_muc, keep_snapshot=True):
    """Fix #1: GOI MOT LAN truoc khi nap file. Xoa metadata cu, tra ve DocID dung chung.

    keep_snapshot=True: chup lai du lieu con cu (trong bo nho) TRUOC khi xoa, de
    restore_document_children() co the khoi phuc neu ingest moi that bai giua chung
    (tranh mat du lieu cu khi Vision/embedding loi). Goi clear_reingest_snapshot()
    khi ingest thanh cong.
    """
    _ensure_engine()
    try:
        with engine.begin() as conn:
            doc_id = _r_document._get_or_create_doc(conn, file_name, thu_muc)
            if doc_id is not None:
                if keep_snapshot:
                    _reingest_snapshots[doc_id] = _snapshot_document_children(conn, doc_id)
                conn.execute(text("DELETE FROM TaiLieuKyThuat WHERE DocID = :d"), {"d": doc_id})
                conn.execute(text("DELETE FROM BangKeVatTu WHERE DocID = :d"), {"d": doc_id})
                conn.execute(text("DELETE FROM DocumentPages WHERE DocID = :d"), {"d": doc_id})
                conn.execute(text("DELETE FROM TechnicalAttributes WHERE DocID = :d"), {"d": doc_id})
            return doc_id
    except Exception as e:
        logger.error(f"Loi reset metadata {file_name}: {e}", exc_info=True)
        if isinstance(e, ValueError) and "published" in str(e):
            raise e
        return None


def clear_reingest_snapshot(doc_id):
    """Ingest thanh cong -> bo snapshot con cu (giai phong bo nho)."""
    if doc_id is not None:
        _reingest_snapshots.pop(doc_id, None)


def restore_document_children(doc_id):
    """Khoi phuc du lieu con cu tu snapshot khi re-ingest that bai (tranh mat data cu).

    Chi restore cho tung bang khi bang do hien dang RONG cho doc_id (ingest moi chua
    ghi duoc gi) -> tranh nhan doi voi du lieu ingest moi da ghi mot phan.
    """
    if doc_id is None:
        return False
    snap = _reingest_snapshots.pop(doc_id, None)
    if not snap:
        return False
    _ensure_engine()
    restored = 0
    try:
        with engine.begin() as conn:
            for tbl, id_col in _REINGEST_SNAPSHOT_TABLES.items():
                rows = snap.get(tbl) or []
                if not rows:
                    continue
                cur = conn.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE DocID = :d"), {"d": doc_id}).scalar() or 0
                if cur > 0:
                    continue
                for r in rows:
                    cols = [c for c in r.keys() if c != id_col]
                    if not cols:
                        continue
                    col_sql = ", ".join(f"[{c}]" for c in cols)
                    par_sql = ", ".join(f":{c}" for c in cols)
                    conn.execute(text(f"INSERT INTO {tbl} ({col_sql}) VALUES ({par_sql})"),
                                 {c: r[c] for c in cols})
                    restored += 1
        logger.info(f"[reingest] restore_document_children doc_id={doc_id}: {restored} dong.")
        return restored > 0
    except Exception as e:
        logger.error(f"[reingest] restore_document_children loi doc_id={doc_id}: {e}", exc_info=True)
        return False
 
def save_document_page(doc_id, file_name, page_no, text_extract, local_ocr_text, vision_summary, local_ocr_confidence, extraction_status, image_path):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO dbo.DocumentPages (
                        DocID, FileName, PageNo, TextExtract, LocalOCRText, 
                        VisionSummary, LocalOCRConfidence, ExtractionStatus, ImagePath
                    ) VALUES (
                        :d, :f, :p, :t, :o, :v, :c, :s, :i
                    )
                """),
                {
                    "d": doc_id,
                    "f": file_name,
                    "p": page_no,
                    "t": text_extract,
                    "o": local_ocr_text,
                    "v": vision_summary,
                    "c": local_ocr_confidence,
                    "s": extraction_status,
                    "i": image_path
                }
            )
    except Exception as e:
        logger.error(f"Loi luu DocumentPages cho {file_name} trang {page_no}: {e}", exc_info=True)

def save_technical_attributes(doc_id, file_name, page_no, attributes):
    if not attributes:
        return
    _ensure_engine()
    try:
        with engine.begin() as conn:
            for attr in attributes:
                conn.execute(
                    text("""
                        INSERT INTO dbo.TechnicalAttributes (
                            DocID, FileName, PageNo, AttributeType, AttributeName, 
                            AttributeValue, Unit, SourceText, Confidence, ExtractedBy
                        ) VALUES (
                            :d, :f, :p, :at, :an, :av, :u, :st, :c, :eb
                        )
                    """),
                    {
                        "d": doc_id,
                        "f": file_name,
                        "p": page_no,
                        "at": attr.get("AttributeType", ""),
                        "an": attr.get("AttributeName", ""),
                        "av": str(attr.get("AttributeValue", ""))[:500],
                        "u": attr.get("Unit"),
                        "st": attr.get("SourceText"),
                        "c": attr.get("Confidence"),
                        "eb": attr.get("ExtractedBy")
                    }
                )
    except Exception as e:
        logger.error(f"Loi luu TechnicalAttributes cho {file_name}: {e}", exc_info=True)

def save_document_attributes(doc_id, domain, attributes):
    """Luu metadata domain phi co khi vao DocumentAttributes (ke_toan, nhan_su, chung...)."""
    if not attributes:
        return
    _ensure_engine()
    try:
        with engine.begin() as conn:
            for attr in attributes:
                conn.execute(
                    text("""
                        INSERT INTO dbo.DocumentAttributes (DocID, Domain, AttributeKey, AttributeValue, Confidence, ExtractedBy)
                        VALUES (:d, :dom, :k, :v, :c, :eb)
                    """),
                    {
                        "d": doc_id,
                        "dom": domain,
                        "k": str(attr.get("key", ""))[:150],
                        "v": str(attr.get("value", "")),
                        "c": attr.get("confidence"),
                        "eb": attr.get("extracted_by", "regex"),
                    },
                )
    except Exception as e:
        logger.error(f"Loi luu DocumentAttributes cho doc {doc_id}: {e}", exc_info=True)


def verify_technical_attribute(attr_id, verified_by, correct_value=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            if correct_value is not None:
                conn.execute(
                    text("""
                        UPDATE dbo.TechnicalAttributes
                        SET HumanVerified = 1,
                            VerifiedBy = :v,
                            VerifiedAt = GETDATE(),
                            AttributeValue = :val
                        WHERE AttributeID = :id
                    """),
                    {"id": attr_id, "v": verified_by, "val": str(correct_value)[:500]}
                )
            else:
                conn.execute(
                    text("""
                        UPDATE dbo.TechnicalAttributes
                        SET HumanVerified = 1,
                            VerifiedBy = :v,
                            VerifiedAt = GETDATE()
                        WHERE AttributeID = :id
                    """),
                    {"id": attr_id, "v": verified_by}
                )
    except Exception as e:
        logger.error(f"Loi verify_technical_attribute: {e}", exc_info=True)

def get_technical_attributes_for_rag(file_name):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            # Ưu tiên lấy dòng được human verified, sau đó mới đến confidence cao
            rows = conn.execute(
                text("""
                    SELECT AttributeType, AttributeValue, Unit, HumanVerified, ExtractedBy
                    FROM (
                        SELECT *,
                               ROW_NUMBER() OVER(
                                   PARTITION BY AttributeType 
                                   ORDER BY HumanVerified DESC, Confidence DESC
                               ) as rn
                        FROM dbo.TechnicalAttributes
                        WHERE FileName = :f
                    ) t
                    WHERE rn = 1
                """),
                {"f": file_name}
            ).fetchall()
            
            result = {}
            for r in rows:
                result[r[0]] = {
                    "value": r[1],
                    "unit": r[2],
                    "human_verified": bool(r[3]),
                    "extracted_by": r[4]
                }
            return result
    except Exception as e:
        logger.error(f"Loi get_technical_attributes_for_rag {file_name}: {e}", exc_info=True)
        return {}

def save_page_metadata(file_name, thu_muc, info, doc_id=None):
    """Fix #1: Chi INSERT 1 dong cho 1 trang. KHONG xoa du lieu trang khac."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            if doc_id is None:
                doc_id = _r_document._get_or_create_doc(conn, file_name, thu_muc)
            p = _r_document._prepare_metadata_params(info)
            p["doc_id"] = doc_id
            conn.execute(
                text(
                    """
                    INSERT INTO TaiLieuKyThuat (
                        DocID, TrangSo, LoaiTaiLieu, MaDoiTuong, TenSanPham, CongDoan,
                        VatLieu, SoLuong, NguoiLap, NgayVe, DungSaiDay, DungSaiKhac,
                        KichThuocTongThe, HDCV, YCKT
                    ) VALUES (
                        :doc_id, :trang_so, :loai_tai_lieu, :ma_doi_tuong, :ten_sp, :cong_doan,
                        :vat_lieu, :so_luong, :nguoi_lap, :ngay_ve, :dung_sai_day, :dung_sai_khac,
                        :kich_thuoc, :hdcv, :yckt
                    )
                    """
                ),
                p,
            )
            return doc_id
    except Exception as e:
        logger.error(
            f"Loi save_page_metadata {file_name} trang {info.get('trang_so')}: {e}",
            exc_info=True,
        )
        return None
 
def save_document_metadata(file_name, thu_muc, info):
    """Tuong thich nguoc cho file 1 trang (non-PDF): reset + insert mot lan."""
    doc_id = reset_document_metadata(file_name, thu_muc)
    return save_page_metadata(file_name, thu_muc, info, doc_id=doc_id)
