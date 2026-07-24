# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import."""

import os
import fitz
import re
import time
import json
import inspect
from dataclasses import dataclass, replace
from functools import partial
from typing import Mapping

from PIL import Image
import pdfplumber
from langchain_core.documents import Document
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from qdrant_client import models
from mech_chatbot.config.logging import logger
from mech_chatbot.db.repositories.bom import save_bom_records
from mech_chatbot.db.repositories.document import (
    get_document_info,
    mark_document_ingest_failed,
    update_document_classification,
)
from mech_chatbot.db.repositories.document_pages import (
    clear_reingest_snapshot,
    reset_document_metadata,
    restore_document_children,
    save_document_attributes,
    save_document_metadata,
    save_document_page,
    save_page_metadata,
    save_technical_attributes,
)
from mech_chatbot.llm.vision_client import describe_vision_error, is_retryable_error
from mech_chatbot.llm.external_ai import external_document_context

# cross-module (owned) imports
from mech_chatbot.ingestion.pdf.config import (
    IMAGE_DIR,
    IMAGE_EXTENSIONS,
    MARKDOWN_EXTENSIONS,
    PdfIngestionConfig,
    ROLLBACK_ON_INGEST_ERROR,
    STRICT_INGEST_REQUIRE_VISION,
)
from mech_chatbot.ingestion.vision_cache import VisionCacheConfig
from mech_chatbot.ingestion.pdf.chunking import (
    _build_chunk_context_prefix,
    _contextual_chunk_enabled,
    get_token_splitter,
    token_splitter,
    tokenize_cached,
)
from mech_chatbot.ingestion.pdf.vision import _prewarm_vision_cache, call_vision_model, format_vision_data, parse_vision_json
from mech_chatbot.ingestion.pdf.quality import _normalize_phong_ban_quyen, calculate_quality_status
from mech_chatbot.ingestion.pdf.bom import (
    extract_bom_records,
    extract_bom_records_from_markdown,
)
from mech_chatbot.ingestion.pdf.readers import extract_text_from_supported_file
from mech_chatbot.ingestion.pdf.metadata import extract_metadata_smart
from mech_chatbot.domain.ingestion_progress import IngestionProgressEvent
from mech_chatbot.application.vector_ingestion import (
    IngestionPersistence,
    IngestionPipelineDependencies,
    require_pipeline_dependencies,
)


@dataclass(frozen=True, slots=True)
class _SourceContext:
    domain: str
    scan_sensitive: bool
    security_level: str
    site: str | None
    site_missing: bool


@dataclass(frozen=True, slots=True)
class _QualityReportContext:
    source: _SourceContext
    sensitive_scan: Mapping[str, object]
    started_at: float
    source_kind: str
    file_extension: str | None = None


@dataclass(frozen=True, slots=True)
class _GovernedDocument:
    doc_id: int
    info: Mapping[str, object]
    external_policy: str


def _legacy_ingestion_persistence() -> IngestionPersistence:
    """Keep direct test/legacy calls working without hiding production wiring."""

    reingest_snapshots = {}

    def bind_optional(callback, **kwargs):
        signature = inspect.signature(callback)
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        supported = {
            key: value
            for key, value in kwargs.items()
            if accepts_kwargs or key in signature.parameters
        }
        return partial(callback, **supported) if supported else callback

    return IngestionPersistence(
        reset_document_metadata=bind_optional(
            reset_document_metadata,
            snapshot_store=reingest_snapshots,
        ),
        get_document_info=get_document_info,
        update_document_classification=update_document_classification,
        clear_reingest_snapshot=bind_optional(
            clear_reingest_snapshot,
            snapshot_store=reingest_snapshots,
        ),
        mark_document_ingest_failed=mark_document_ingest_failed,
        restore_document_children=bind_optional(
            restore_document_children,
            snapshot_store=reingest_snapshots,
        ),
        save_bom_records=save_bom_records,
        save_technical_attributes=save_technical_attributes,
        save_document_attributes=save_document_attributes,
        save_document_page=save_document_page,
        save_page_metadata=save_page_metadata,
    )


def _load_governed_document(ten_file, thu_muc, *, persistence):
    doc_id = persistence.reset_document_metadata(ten_file, thu_muc)
    if not doc_id:
        raise RuntimeError(
            "Khong tao duoc document identity; dung ingest truoc khi goi dich vu ngoai."
        )

    raw_info = persistence.get_document_info(doc_id)
    if not isinstance(raw_info, Mapping) or not raw_info:
        raise RuntimeError(
            "Khong tai duoc governance cua tai lieu; dung ingest truoc khi goi dich vu ngoai."
        )

    info = dict(raw_info)
    external_policy = str(info.get("external_processing_policy") or "").strip()
    if not external_policy:
        raise RuntimeError(
            "Khong tai duoc external processing policy; dung ingest truoc khi goi dich vu ngoai."
        )
    return _GovernedDocument(
        doc_id=int(doc_id),
        info=info,
        external_policy=external_policy,
    )


def _initialize_source_context(
    thu_muc,
    *,
    domain_override,
    security_override,
    site_override,
    scan_sensitive,
    phong_ban_override,
):
    from mech_chatbot.ingestion.domain_registry import (
        resolve_domain_by_department,
        resolve_security_by_department,
    )
    from mech_chatbot.ingestion.sensitive_scanner import requires_mandatory_scan
    from mech_chatbot.ingestion.site_registry import resolve_site_by_department

    domain = domain_override or resolve_domain_by_department(thu_muc)
    resolved_scan_sensitive = bool(
        scan_sensitive or requires_mandatory_scan(thu_muc, phong_ban_override)
    )
    security_level = security_override or resolve_security_by_department(thu_muc)
    site = site_override or resolve_site_by_department(thu_muc)
    return _SourceContext(
        domain=domain,
        scan_sensitive=resolved_scan_sensitive,
        security_level=security_level,
        site=site,
        site_missing=not str(site or "").strip(),
    )


def _initialize_report(
    ten_file,
    *,
    total_pages,
    site_missing,
    track_vision_cache_hits,
):
    report = {
        "status": "success",
        "ten_file": ten_file,
        "total_pages": total_pages,
        "total_chunks": 0,
        "pages_text_extracted": [],
        "pages_table_extracted": [],
        "pages_vision_success": [],
        "failed_pages": [],
        "vision_warnings": [],
        **({"pages_vision_cache_hit": []} if track_vision_cache_hits else {}),
        "metadata_llm_failed_pages": [],
        "bom_rows_count": 0,
        "technical_attributes_count": 0,
        "quality_score": 0,
        "quality_status": "unknown",
        "warnings": [],
        "time_taken": 0,
        "message": "",
    }
    if site_missing:
        report["warnings"].append(
            "Thieu site: tai lieu se o trang thai needs_review va khong duoc publish cho den khi backfill."
        )
        report["missing_metadata"] = ["site"]
    return report


def _finalize_successful_persistence(
    report,
    *,
    dependencies,
    doc_id,
    ten_file,
    thu_muc,
    phong_ban_override,
    source_context,
    persistence,
):
    if report["status"] != "success" or not doc_id:
        return

    phong_ban = _normalize_phong_ban_quyen(thu_muc, phong_ban_override)
    classification_updated = persistence.update_document_classification(
        doc_id,
        domain=source_context.domain,
        security_level=source_context.security_level,
        phong_ban=phong_ban,
    )
    if classification_updated is False:
        raise RuntimeError("Khong dong bo duoc classification/RBAC vao SQL.")
    try:
        dependencies.qdrant_client.set_payload(
            collection_name=dependencies.collection_name,
            payload={
                "security_level": source_context.security_level,
                "domain": source_context.domain,
            },
            points=models.Filter(
                must=[
                    models.FieldCondition(
                        key="metadata.doc_id",
                        match=models.MatchValue(value=doc_id),
                    )
                ]
            ),
            key="metadata",
        )
    except Exception as error:
        raise RuntimeError(
            f"Khong dong bo duoc payload Qdrant cho doc_id={doc_id}: {error}"
        ) from error
    persistence.clear_reingest_snapshot(doc_id)


def _rollback_failed_ingestion(
    report,
    *,
    dependencies,
    doc_id,
    ten_file,
    thu_muc,
    rollback_on_error,
    persistence,
):
    if report["status"] != "error" or not rollback_on_error:
        return

    try:
        _delete_vectors_for_file(
            ten_file,
            thu_muc,
            doc_id=doc_id,
            dependencies=dependencies,
        )
        persistence.mark_document_ingest_failed(
            ten_file,
            thu_muc,
            report.get("message"),
        )
        persistence.restore_document_children(doc_id)
        report["total_chunks"] = 0
        report["warnings"].append(
            "Da rollback vector/metadata cua file nay vi ingest khong dat quality gate."
        )
    except Exception as error:
        report["warnings"].append(f"Rollback vector/metadata that bai: {error}")
        logger.warning(f"Rollback vector/metadata that bai cho {ten_file}: {error}")


def _build_final_report_message(report, context):
    message_parts = []
    if report["message"]:
        message_parts.append(report["message"])
    if context.source_kind == "pdf":
        if report["failed_pages"]:
            message_parts.append(f"Cac trang loi/bo qua: {report['failed_pages']}")
        if report["vision_warnings"]:
            pages = [
                item.get("page")
                for item in report["vision_warnings"]
                if isinstance(item, dict)
            ]
            message_parts.append(f"Trang cảnh báo GPT-5.4 Vision: {pages}")
    if report["metadata_llm_failed_pages"]:
        label = (
            "Trang lỗi LLM metadata fallback"
            if context.source_kind == "pdf"
            else "Trang loi LLM metadata fallback"
        )
        message_parts.append(f"{label}: {report['metadata_llm_failed_pages']}")
    if report["warnings"]:
        message_parts.append("Canh bao chat luong: " + " | ".join(report["warnings"][:5]))
    if not message_parts:
        if context.source_kind == "pdf":
            message_parts.append(
                f"Da nap {report['total_chunks']} chunks tu {report['total_pages']} trang."
            )
        else:
            message_parts.append(
                f"Da nap {report['total_chunks']} chunks tu file {context.file_extension}."
            )
    return " ".join(message_parts)


def _finalize_quality_report(report, context):
    score, status = calculate_quality_status(report, context.source.domain)
    report["quality_score"] = score
    report["quality_status"] = (
        "needs_review"
        if context.source.site_missing and report["status"] == "success"
        else status
    )
    if context.sensitive_scan.get("is_sensitive") and report["status"] == "success":
        from mech_chatbot.ingestion.sensitive_scanner import apply_sensitive_quality_policy

        apply_sensitive_quality_policy(report, context.sensitive_scan)
    if context.source.site_missing and report["status"] == "success":
        report["quality_label"] = "Cần rà soát thủ công"
        if "missing_site" not in report["quality_reason_codes"]:
            report["quality_reason_codes"].append("missing_site")
            report["quality_reasons"].append(
                {
                    "code": "missing_site",
                    "component": "metadata_classification",
                    "message": "Thiếu site bắt buộc trước khi publish.",
                }
            )

    report["time_taken"] = round(time.time() - context.started_at, 2)
    report["message"] = _build_final_report_message(report, context)
    return report


def _finalize_pipeline_transaction(
    report,
    *,
    dependencies,
    doc_id,
    ten_file,
    thu_muc,
    phong_ban_override,
    context,
    rollback_on_error,
    persistence,
):
    _finalize_quality_report(report, context)
    if report.get("quality_status") == "blocked":
        report["status"] = "error"

    if report["status"] == "success":
        try:
            _finalize_successful_persistence(
                report,
                dependencies=dependencies,
                doc_id=doc_id,
                ten_file=ten_file,
                thu_muc=thu_muc,
                phong_ban_override=phong_ban_override,
                source_context=context.source,
                persistence=persistence,
            )
        except Exception as error:
            report["status"] = "error"
            report["message"] = str(error)

    _rollback_failed_ingestion(
        report,
        dependencies=dependencies,
        doc_id=doc_id,
        ten_file=ten_file,
        thu_muc=thu_muc,
        rollback_on_error=rollback_on_error,
        persistence=persistence,
    )
    report["message"] = _build_final_report_message(report, context)
    return report


def _emit_progress(progress_callback, phase, message):
    if progress_callback:
        progress_callback(IngestionProgressEvent(phase=phase, message=message))


def _vision_cache_get(cache_module, key, *, config, explicit_config):
    if explicit_config:
        return cache_module.get(key, config=config)
    return cache_module.get(key)


def _vision_cache_put(cache_module, key, value, *, config, explicit_config):
    if explicit_config:
        return cache_module.put(key, value, config=config)
    return cache_module.put(key, value)


@retry(
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True
)
def _add_docs_with_retry(chunks, *, dependencies):
    dependencies.vector_store.add_documents(chunks)


def _delete_vectors_for_file(
    ten_file,
    thu_muc,
    doc_id=None,
    *,
    dependencies,
):
    # Uu tien xoa theo doc_id (chinh xac nhat, bat duoc ca khi doi ten file/thu muc
    # -> tranh sot vector cu gay trung lap/nhieu khi re-ingest).
    if doc_id is not None:
        try:
            dependencies.qdrant_client.delete(
                collection_name=dependencies.collection_name,
                points_selector=models.Filter(
                    must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id))]
                ),
            )
        except Exception as e:
            logger.warning(f"Xoa vector theo doc_id={doc_id} loi (se thu tiep theo file): {e}")
    # Bug#4: khop theo metadata.thu_muc (thu muc goc, gia tri don) thay vi
    # metadata.phong_ban_quyen (danh sach quyen). phong_ban_quyen co the chua nhieu
    # phong chia se, dung MatchValue tren list de lai vector khi doi ten phong.
    # Van giu should de tuong thich nguoc voi vector cu (chua co metadata.thu_muc).
    dependencies.qdrant_client.delete(
        collection_name=dependencies.collection_name,
        points_selector=models.Filter(
            must=[
                models.FieldCondition(key="metadata.file_goc", match=models.MatchValue(value=ten_file)),
            ],
            should=[
                models.FieldCondition(key="metadata.thu_muc", match=models.MatchValue(value=thu_muc)),
                models.FieldCondition(key="metadata.phong_ban_quyen", match=models.MatchValue(value=thu_muc)),
            ],
        )
    )


def process_and_ingest_pdf(
    pdf_path,
    ten_file,
    thu_muc,
    vision_model=None,
    progress_callback=None,
    domain_override=None,
    security_override=None,
    cong_doan_override=None,
    site_override=None,
    scan_sensitive=False,
    phong_ban_override=None,
    *,
    dependencies: IngestionPipelineDependencies | None = None,
    persistence: IngestionPersistence | None = None,
    config: PdfIngestionConfig | None = None,
):
    dependencies = require_pipeline_dependencies(dependencies)
    persistence = persistence or _legacy_ingestion_persistence()
    explicit_config = config is not None
    runtime_config = config or PdfIngestionConfig(
        image_dir=IMAGE_DIR,
        strict_require_vision=STRICT_INGEST_REQUIRE_VISION,
        rollback_on_error=ROLLBACK_ON_INGEST_ERROR,
    )
    runtime_config.image_dir.mkdir(parents=True, exist_ok=True)
    cache_config = VisionCacheConfig(
        enabled=runtime_config.vision_cache_enabled,
        directory=runtime_config.vision_cache_dir,
    )
    splitter = (
        get_token_splitter(runtime_config)
        if explicit_config
        else token_splitter
    )
    contextual_chunks = (
        runtime_config.contextual_chunk_enabled
        if explicit_config
        else _contextual_chunk_enabled()
    )
    source_context = _initialize_source_context(
        thu_muc,
        domain_override=domain_override,
        security_override=security_override,
        site_override=site_override,
        scan_sensitive=scan_sensitive,
        phong_ban_override=phong_ban_override,
    )
    domain = source_context.domain
    scan_sensitive = source_context.scan_sensitive
    security_level = source_context.security_level
    site = source_context.site
    start_time = time.time()
    report = _initialize_report(
        ten_file,
        total_pages=0,
        site_missing=source_context.site_missing,
        track_vision_cache_hits=True,
    )
    sensitive_scan = {"is_sensitive": False, "categories": [], "match_counts": {}}
    doc = None
    pdf_table_reader = None
    doc_id = None
    try:
        doc = fitz.open(pdf_path)
        report["total_pages"] = len(doc)

        # FIX #1: Reset metadata MOT LAN cho ca file, lay doc_id dung chung cho moi trang
        governed_document = _load_governed_document(
            ten_file,
            thu_muc,
            persistence=persistence,
        )
        doc_id = governed_document.doc_id
        doc_info = governed_document.info
        external_policy = governed_document.external_policy
        report["classification_failed"] = bool(doc_info.get("classification_failed"))

        # FIX hieu nang: mo pdfplumber MOT LAN ngoai vong lap (truoc day mo lai moi trang)
        try:
            pdf_table_reader = pdfplumber.open(pdf_path)
        except Exception as e:
            logger.warning(f"Khong mo duoc pdfplumber cho {ten_file}: {e}")
            pdf_table_reader = None

        base_name = os.path.splitext(ten_file)[0]  # FIX: thay ten_file.replace('.pdf', '')

        # Perf (GD3, opt-in, mac dinh TAT): lam nong Vision cache song song truoc khi vao vong lap.
        # Khi INGEST_VISION_PREWARM_WORKERS<=1 -> no-op, vong lap chay serial y het cu.
        with external_document_context(
            doc_ids=[doc_id],
            security_levels=[security_level],
            policies=[external_policy],
        ):
            prewarm_args = (
                doc,
                ten_file,
                thu_muc,
                domain,
                vision_model,
                (
                    lambda message: _emit_progress(
                        progress_callback,
                        "extracting",
                        message,
                    )
                ),
            )
            if explicit_config:
                _prewarm_vision_cache(
                    *prewarm_args,
                    config=runtime_config,
                )
            else:
                _prewarm_vision_cache(*prewarm_args)

        for page_num in range(len(doc)):
            _emit_progress(
                progress_callback,
                "extracting",
                f"Dang xu ly trang {page_num+1}/{len(doc)}",
            )
            try:
                page = doc.load_page(page_num)
                text = page.get_text("text")

                # Render image truoc de kip phan tich
                pix = page.get_pixmap(dpi=runtime_config.pdf_render_dpi)
                safe_thu_muc = re.sub(r'[\\/*?:"<>|]', "", thu_muc) if thu_muc else ""
                if safe_thu_muc:
                    img_name = f"{safe_thu_muc}_{base_name}_page{page_num+1}.png"
                else:
                    img_name = f"{base_name}_page{page_num+1}.png"
                img_path = os.path.join(runtime_config.image_dir, img_name)
                pix.save(img_path)

                image_summary = ""
                vision_metadata = {}
                is_text_heavy = len(text.strip()) > 1500  # Chi bo qua trang van ban thuan
                if len(text.strip()) > 100:
                    report["pages_text_extracted"].append(page_num + 1)

                # Phan tich anh qua GPT-5.4 Vision khi can.
                from mech_chatbot.ingestion.domain_handlers import get_handler as _gh_vision
                _is_mech_domain_vision = _gh_vision(domain).vision_always
                # P0: chi day anh/scan qua GPT Vision. Trang co lop text day (van ban thuan)
                # thuoc domain phi co khi thi doc truc tiep tu text layer, tiet kiem quota Vision.
                vision_required = os.path.exists(img_path) and (_is_mech_domain_vision or not is_text_heavy)
                vision_failed = False
                vision_data = {}

                if vision_required:
                    _emit_progress(
                        progress_callback,
                        "extracting",
                        f"Trang {page_num+1}: dùng GPT-5.4 Vision để phân tích ảnh.",
                    )

                if vision_model and vision_required:
                    _emit_progress(
                        progress_callback,
                        "extracting",
                        f"Đang dùng GPT-5.4 Vision phân tích ảnh trang {page_num+1}...",
                    )
                    try:
                        img_to_analyze = Image.open(img_path)
                        prompt = (
                            f"Day la trang so {page_num+1} cua file {ten_file}. "
                            "Hay OCR va tra ve ket qua DUOI DANG JSON voi schema sau:\n"
                            "{\n"
                            '  "document_codes": [],\n'
                            '  "part_names": [],\n'
                            '  "materials": [],\n'
                            '  "dimensions": [],\n'
                            '  "tolerances": [],\n'
                            '  "technical_notes": [],\n'
                            '  "bom_rows": [],\n'
                            '  "uncertain_fields": []\n'
                            "}\n"
                            "Luon tra ve dung dinh dang JSON (khong kem text mo dau/ket thuc ngoai block ```json). "
                            "Dien vao cac mang cac thong tin ky thuat tuong ung ban nhin thay trong hinh."
                        )
                        # P2-5: cache ket qua Vision theo hash anh trang (tiet kiem chi phi)
                        from mech_chatbot.ingestion import vision_cache as _vc
                        _page_key = _vc.hash_image_file(img_path)
                        _cached = _vision_cache_get(
                            _vc,
                            _page_key,
                            config=cache_config,
                            explicit_config=explicit_config,
                        )
                        if _cached is not None:
                            vision_data = _cached
                            report.setdefault("pages_vision_cache_hit", []).append(page_num + 1)
                            _emit_progress(
                                progress_callback,
                                "extracting",
                                f"Trang {page_num+1}: dung lai ket qua Vision tu cache (tiet kiem chi phi).",
                            )
                        else:
                            with external_document_context(
                                doc_ids=[doc_id],
                                security_levels=[security_level],
                                policies=[external_policy],
                            ):
                                response = call_vision_model(vision_model, prompt, img_to_analyze)
                            vision_data = parse_vision_json(response.text)
                            if vision_data:
                                _vision_cache_put(
                                    _vc,
                                    _page_key,
                                    vision_data,
                                    config=cache_config,
                                    explicit_config=explicit_config,
                                )

                        if vision_data:
                            image_summary = format_vision_data(vision_data)
                            report["pages_vision_success"].append(page_num + 1)

                            # Add to vision_metadata
                            if vision_data.get("document_codes"): vision_metadata["vision_document_codes"] = ", ".join([str(x) for x in vision_data["document_codes"]])
                            if vision_data.get("part_names"): vision_metadata["vision_part_names"] = ", ".join([str(x) for x in vision_data["part_names"]])
                            if vision_data.get("materials"): vision_metadata["vision_materials"] = ", ".join([str(x) for x in vision_data["materials"]])
                            if vision_data.get("dimensions"): vision_metadata["vision_dimensions"] = ", ".join([str(x) for x in vision_data["dimensions"]])
                            if vision_data.get("tolerances"): vision_metadata["vision_tolerances"] = ", ".join([str(x) for x in vision_data["tolerances"]])
                            if vision_data.get("technical_notes"): vision_metadata["vision_technical_notes"] = ", ".join([str(x) for x in vision_data["technical_notes"]])
                            if vision_data.get("uncertain_fields"): vision_metadata["vision_uncertain_fields"] = ", ".join([str(x) for x in vision_data["uncertain_fields"]])

                            # Convert BOM rows if present
                            if vision_data.get("bom_rows"):
                                structured_bom = []
                                for idx, row in enumerate(vision_data["bom_rows"]):
                                    if isinstance(row, dict):
                                        structured_bom.append({
                                            "ma_hang": str(row.get("ma", row.get("code", row.get("ma_hang", "")))),
                                            "ten_vat_tu": str(row.get("ten", row.get("name", row.get("ten_vat_tu", "")))),
                                            "vat_lieu": str(row.get("vat_lieu", row.get("material", ""))),
                                            "so_luong": row.get("sl", row.get("qty", row.get("so_luong", None))),
                                            "ghi_chu": str(row.get("ghi_chu", row.get("note", ""))),
                                            "don_vi": str(row.get("don_vi", row.get("unit", ""))),
                                            "confidence": row.get("confidence", 0.85),
                                            "raw_row_json": json.dumps(row, ensure_ascii=False),
                                            "source_table_index": 0
                                        })
                                if structured_bom:
                                    persistence.save_bom_records(
                                        doc_id,
                                        page_num + 1,
                                        structured_bom,
                                    )
                        else:
                            image_summary = response.text
                    except Exception as e:
                        vision_failed = True
                        detail = describe_vision_error(e)
                        warn = f"Trang {page_num+1}: GPT-5.4 Vision/OCR lỗi cho {img_name}: {detail}"
                        report["vision_warnings"].append({"page": page_num + 1, "detail": detail})
                        report["warnings"].append(warn)
                        logger.error(warn)
                elif vision_required and not vision_model:
                    vision_failed = True
                    warn = f"Trang {page_num+1}: cần GPT-5.4 Vision/OCR nhưng chưa cấu hình PROXYLLM_API_KEY hợp lệ."
                    report["vision_warnings"].append({"page": page_num + 1, "detail": "no_vision_model"})
                    report["warnings"].append(warn)
                    logger.error(warn)

                if vision_failed and runtime_config.strict_require_vision:
                    report["failed_pages"].append(page_num+1)
                    logger.error(
                        f"Bo qua nap trang {page_num+1} cua {ten_file} de tranh nap thieu du lieu hinh anh/OCR."
                    )
                    continue

                combined_text_for_metadata = text + "\n\n" + image_summary
                warning_count_before_metadata = len(report["warnings"])
                with external_document_context(
                    doc_ids=[doc_id],
                    security_levels=[security_level],
                    policies=[external_policy],
                ):
                    metadata_kwargs = {
                        "quality_warnings": report["warnings"],
                    }
                    if explicit_config:
                        metadata_kwargs["metadata_mode"] = (
                            runtime_config.llm_metadata_mode
                        )
                    info = extract_metadata_smart(
                        combined_text_for_metadata,
                        ten_file,
                        thu_muc,
                        vision_model,
                        **metadata_kwargs,
                    )
                if len(report["warnings"]) > warning_count_before_metadata:
                    report["metadata_llm_failed_pages"].append(page_num+1)

                # Count technical attributes (fields that are not empty and not "Khong ro")
                for k, v in info.items():
                    if v and v != "Khong ro" and v != [] and k not in ["yckt", "hdcv", "ngay_ve", "nguoi_lap"]:
                        report["technical_attributes_count"] += 1

                tech_attrs = []
                def add_attr(atype, avalue, source_text, extractor, confidence=1.0, unit=None):
                    if avalue and avalue != "Khong ro" and avalue != []:
                        if isinstance(avalue, list):
                            for v in avalue:
                                if v and v != "Khong ro":
                                    tech_attrs.append({
                                        "AttributeType": atype, "AttributeValue": str(v), "Unit": unit,
                                        "SourceText": str(source_text)[:1000], "Confidence": confidence, "ExtractedBy": extractor
                                    })
                        else:
                            tech_attrs.append({
                                "AttributeType": atype, "AttributeValue": str(avalue), "Unit": unit,
                                "SourceText": str(source_text)[:1000], "Confidence": confidence, "ExtractedBy": extractor
                            })

                add_attr("material", info.get("vat_lieu"), text[:200], "llm_text")
                add_attr("thickness", info.get("dung_sai_day"), text[:200], "llm_text")
                add_attr("dimension", info.get("kich_thuoc"), text[:200], "llm_text")
                add_attr("tolerance", info.get("dung_sai_khac"), text[:200], "llm_text")
                add_attr("part_name", info.get("ten_tai_lieu"), text[:200], "llm_text")
                add_attr("drawing_code", info.get("ma_doi_tuong"), text[:200], "llm_text")
                add_attr("quantity", info.get("so_luong"), text[:200], "llm_text")
                add_attr("note", info.get("yckt"), text[:200], "llm_text")

                if vision_data:
                    add_attr("material", vision_data.get("materials"), image_summary[:500], "vision", 0.9)
                    add_attr("dimension", vision_data.get("dimensions"), image_summary[:500], "vision", 0.9)
                    add_attr("tolerance", vision_data.get("tolerances"), image_summary[:500], "vision", 0.9)
                    add_attr("part_name", vision_data.get("part_names"), image_summary[:500], "vision", 0.9)
                    add_attr("drawing_code", vision_data.get("document_codes"), image_summary[:500], "vision", 0.9)
                    add_attr("note", vision_data.get("technical_notes"), image_summary[:500], "vision", 0.9)

                from mech_chatbot.ingestion.mechanical_extractors import extract_mechanical_attributes
                from mech_chatbot.ingestion.domain_handlers import get_handler as _gh_attr
                _handler_attr = _gh_attr(domain)
                _attr_kind = _handler_attr.extractor_kind
                regex_attrs = extract_mechanical_attributes(combined_text_for_metadata) if _handler_attr.attribute_strategy == 'technical' else []
                for attr in regex_attrs:
                    tech_attrs.append({
                        "AttributeType": attr["type"],
                        "AttributeValue": attr["value"],
                        "Unit": None,
                        "SourceText": attr["source_text"][:1000],
                        "Confidence": attr["confidence"],
                        "ExtractedBy": attr["extracted_by"]
                    })

                if _handler_attr.attribute_strategy == 'technical':
                    persistence.save_technical_attributes(
                        doc_id,
                        ten_file,
                        page_num + 1,
                        tech_attrs,
                    )
                else:
                    from mech_chatbot.ingestion.generic_extractors import extract_generic_attributes
                    persistence.save_document_attributes(
                        doc_id,
                        domain,
                        extract_generic_attributes(
                            combined_text_for_metadata,
                            domain,
                        ),
                    )

                # GD4: duong nap hang loat khong tin folder tuyet doi -> quet noi dung nhay cam
                if scan_sensitive:
                    from mech_chatbot.ingestion.sensitive_scanner import scan_sensitive_content, escalate_security, merge_scan_results
                    _scan = scan_sensitive_content(combined_text_for_metadata)
                    sensitive_scan = merge_scan_results(sensitive_scan, _scan)
                    if _scan.get("is_sensitive"):
                        security_level = escalate_security(security_level, _scan)
                metadata = {
                    "file_goc": ten_file,
                    "thu_muc": thu_muc,
                    "phong_ban_quyen": _normalize_phong_ban_quyen(thu_muc, phong_ban_override),
                    "ma_doi_tuong": info["ma_doi_tuong"],
                    "ma_chinh": info.get("ma_chinh", []),
                    "ma_btp": info.get("ma_btp", []),
                    "ma_vat_tu": info.get("ma_vat_tu", []),
                    "ma_lien_quan": info.get("ma_lien_quan", []),
                    "loai_tai_lieu": info["loai_tai_lieu"],
                    "ten_san_pham": info["ten_tai_lieu"],
                    "cong_doan": cong_doan_override or info["cong_doan"],
                    "so_luong": info["so_luong"],
                    "vat_lieu": info["vat_lieu"],
                    "nguoi_lap": info["nguoi_lap"],
                    "ngay_ve": info["ngay_ve"],
                    "dung_sai_do_day": info["dung_sai_day"],
                    "dung_sai_kich_thuoc": info["dung_sai_khac"],
                    "kich_thuoc_tong_the": info["kich_thuoc"],
                    # --- No-code resolver enrichment (Muc 3) ---
                    "product_name": info.get("ten_tai_lieu", "") or "",
                    "model": doc_info.get("variant_code", "default"),
                    "doc_type": info.get("loai_tai_lieu", "") or "",
                    "document_type": doc_info.get("document_type") or info.get("loai_tai_lieu", "") or "",
                    "document_type_family": doc_info.get("document_type") or "",
                    "title": doc_info.get("title", "") or "",
                    "doc_number": doc_info.get("doc_number", "") or "",
                    "material_list": ([info["vat_lieu"]] if info.get("vat_lieu") and str(info.get("vat_lieu")).strip() not in ("", "Khong ro", "Không rõ") else []),
                    "dimensions_list": ([info["kich_thuoc"]] if info.get("kich_thuoc") else []),
                    "ten_san_pham_norm": (info.get("ten_tai_lieu", "") or "").strip().lower(),
                    "trang_so": page_num + 1,
                    "parent_page": page_num + 1,
                    "parent_section": (
                        doc_info.get("parent_section")
                        or
                        info.get("cong_doan")
                        or info.get("ten_tai_lieu")
                        or info.get("loai_tai_lieu")
                        or ""
                    ),
                    "doc_status": "pending_review",
                    "doc_id": doc_id,
                    "family_id": doc_info.get("family_id"),
                    "base_code": doc_info.get("base_code", ""),
                    "version_no": doc_info.get("version_no", 1),
                    "version_label": doc_info.get("version_label", ""),
                    "variant_code": doc_info.get("variant_code", "default"),
                    "variant_group": doc_info.get("variant_group", ""),
                    "lifecycle_status": doc_info.get("lifecycle_status", "draft"),
                    "review_status": doc_info.get("review_status", "pending_review"),
                    "is_current": doc_info.get("is_current", False),
                    "is_archived": doc_info.get("is_archived", False),
                    "supersedes_doc_id": doc_info.get("supersedes_doc_id"),
                    "publication_state": doc_info.get("publication_state", "draft"),
                    "publication_version": doc_info.get("publication_version", 1),
                    "serving_epoch": doc_info.get("serving_epoch", 0),
                    "servable": bool(doc_info.get("servable", False)),
                    "owner_department": doc_info.get("owner_department") or thu_muc,
                    "knowledge_owner_user_id": doc_info.get("knowledge_owner_user_id"),
                    "knowledge_approver_user_id": doc_info.get("knowledge_approver_user_id"),
                    "taxonomy_version": doc_info.get("taxonomy_version", "v1"),
                    "parent_applicable": bool(doc_info.get("parent_applicable", True)),
                    "parent_context_enabled": bool(doc_info.get("parent_context_enabled", True)),
                    "source_system": doc_info.get("source_system", "upload"),
                    "external_processing_policy": external_policy,
                    # Source-of-truth for chat presentation: show the rendered
                    # page only when Vision/OCR produced a non-empty summary.
                    "vision_used": bool(image_summary.strip()),
                    # Multi-domain (P0)
                    "domain": domain,
                    "security_level": security_level,
                    # Multi-site (P1.2)
                    "site": site,
                    # Trang thai hieu luc canonical cho ingest moi.
                    # refresh_expired_status() se dong bo 'expired' xuong payload sau nay)
                    "effective_status": "effective",
                }
                info['trang_so'] = page_num + 1

                persistence.save_document_page(
                    doc_id=doc_id,
                    file_name=ten_file,
                    page_no=page_num + 1,
                    text_extract=text,
                    vision_summary=image_summary,
                    extraction_status="success",
                    image_path=img_path
                )

                # FIX #1: CHI insert metadata trang nay (khong xoa metadata cac trang khac)
                persistence.save_page_metadata(
                    ten_file,
                    thu_muc,
                    info,
                    doc_id=doc_id,
                )

                all_chunks = []
                title_block = (
                    f"Thong tin tai lieu {ten_file}:\n"
                    f"- Ma chinh: {info.get('ma_chinh', [])}\n"
                    f"- Ma BTP: {info.get('ma_btp', [])}\n"
                    f"- Ma vat tu: {info.get('ma_vat_tu', [])}\n"
                    f"- Ma lien quan: {info.get('ma_lien_quan', [])}\n"
                    f"- Ma doi tuong tong hop: {info['ma_doi_tuong']}\n"
                    f"- Loai tai lieu: {info['loai_tai_lieu']}\n"
                    f"- Ten tai lieu/san pham: {info['ten_tai_lieu']}\n"
                    f"- Cong doan: {info['cong_doan']}\n"
                    f"- Vat lieu: {info['vat_lieu']}\n"
                    f"- So luong: {info['so_luong']}\n"
                    f"- Nguoi lap: {info['nguoi_lap']}\n"
                    f"- Ngay phat hanh: {info['ngay_ve']}\n"
                    f"- Dung sai do day vat lieu: {info['dung_sai_day']}\n"
                    f"- Dung sai cac kich thuoc khac: {info['dung_sai_khac']}\n"
                )
                if info['kich_thuoc']:
                    title_block += f"- Kich thuoc tong the: {info['kich_thuoc']}\n"
                all_chunks.append(Document(page_content=title_block, metadata={**metadata, "loai_du_lieu": "title_block"}))

                # FIX hieu nang: dung pdf_table_reader DA mo san thay vi mo lai moi trang
                markdown_tables = ""
                if pdf_table_reader is not None:
                    try:
                        page_plumber = pdf_table_reader.pages[page_num]
                        tables = page_plumber.extract_tables()
                        if tables:
                            report["pages_table_extracted"].append(page_num + 1)
                        for table_idx, table in enumerate(tables):
                            # Parse BOM records and save to SQL
                            bom_records = extract_bom_records(table, table_idx=table_idx)
                            if bom_records:
                                persisted = persistence.save_bom_records(
                                    doc_id,
                                    page_num + 1,
                                    bom_records,
                                )
                                report["bom_rows_count"] += persisted
                                if not persisted:
                                    report["warnings"].append(
                                        f"structured_bom_persistence_failed:page:{page_num + 1}"
                                    )

                            for row in table:
                                cleaned_row = [str(cell).replace("\n", " ").strip() if cell else "" for cell in row]
                                markdown_tables += "| " + " | ".join(cleaned_row) + " |\n"
                            markdown_tables += "\n"
                    except Exception as e:
                        logger.warning(f"Khong the boc bang bieu {img_name}: {e}")

                if markdown_tables.strip():
                    table_content = f"Bang bieu tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{markdown_tables}"
                    table_chunks = splitter.split_text(table_content)
                    for i, c in enumerate(table_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "bang_ke_vat_tu", "chunk_index": i}))

                if info['yckt']:
                    yckt_content = f"Yeu cau ky thuat tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{info['yckt']}"
                    yckt_chunks = splitter.split_text(yckt_content)
                    for i, c in enumerate(yckt_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "yckt", "chunk_index": i}))

                if info['hdcv']:
                    hdcv_content = f"Huong dan cong viec tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{info['hdcv']}"
                    hdcv_chunks = splitter.split_text(hdcv_content)
                    for i, c in enumerate(hdcv_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "hdcv", "chunk_index": i}))

                # Luoi an toan: luu raw text de giu moi thong tin chi tiet
                # (kich thuoc, goc, ban kinh, ghi chu) ma regex khong cover duoc
                if text.strip():
                    raw_content = f"Noi dung chi tiet trang {page_num+1} tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{text.strip()}"
                    raw_chunks = splitter.split_text(raw_content)
                    for i, c in enumerate(raw_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "text", "chunk_index": i}))

                if image_summary.strip():
                    img_summary_content = f"Phan tich hinh anh tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{image_summary}"
                    all_chunks.append(Document(page_content=img_summary_content, metadata={**metadata, "loai_du_lieu": "image_summary", **vision_metadata}))

                # FIX #3: GIU text goc cho LLM (noi_dung_goc) TRUOC khi tokenize ban dung cho BM25
                for chunk in all_chunks:
                    chunk.metadata["noi_dung_goc"] = chunk.page_content
                    _embed_src = chunk.page_content
                    if contextual_chunks:
                        _cpref = _build_chunk_context_prefix(chunk.metadata)
                        if _cpref:
                            _embed_src = _cpref + "\n" + chunk.page_content
                    chunk.page_content = tokenize_cached(_embed_src)

                # Document Versioning: Xoa vector cu cua file nay truoc khi add (chi xoa 1 lan o trang 1)
                if page_num == 0:
                    try:
                        _delete_vectors_for_file(
                            ten_file,
                            thu_muc,
                            doc_id=doc_id,
                            dependencies=dependencies,
                        )
                    except Exception as e:
                        logger.warning(f"Khong xoa duoc vector cu (bo qua, tiep tuc): {ten_file}: {e}")

                _emit_progress(
                    progress_callback,
                    "embedding",
                    "Đang tạo embedding...",
                )
                _add_docs_with_retry(all_chunks, dependencies=dependencies)
                report["total_chunks"] += len(all_chunks)

            except Exception as e:
                logger.error(f"Loi khi xu ly trang {page_num+1} cua {ten_file}: {e}", exc_info=True)
                report["failed_pages"].append(page_num+1)
                # FIX #2: KHONG dong doc o day nua (truoc day doc.close() trong except
                # khien cac trang sau cua cung file deu loi day chuyen).

    except Exception as e:
        logger.error(f"Loi doc file PDF {ten_file}: {e}", exc_info=True)
        report["status"] = "error"
        report["message"] = str(e)
    finally:
        # FIX #2: dong tai nguyen DUNG MOT LAN, ke ca khi thanh cong
        # (truoc day thieu doc.close() o nhanh thanh cong -> ro ri file handle).
        if pdf_table_reader is not None:
            try:
                pdf_table_reader.close()
            except Exception:
                pass
        if doc is not None:
            doc.close()

    # Partial page failures reduce extraction coverage; only an empty result is
    # a hard pipeline failure.
    if report["status"] == "success" and report["total_chunks"] == 0:
        report["status"] = "error"

    finalized_source = replace(source_context, security_level=security_level)
    return _finalize_pipeline_transaction(
        report,
        dependencies=dependencies,
        doc_id=doc_id,
        ten_file=ten_file,
        thu_muc=thu_muc,
        phong_ban_override=phong_ban_override,
        context=_QualityReportContext(
            source=finalized_source,
            sensitive_scan=sensitive_scan,
            started_at=start_time,
            source_kind="pdf",
        ),
        rollback_on_error=runtime_config.rollback_on_error,
        persistence=persistence,
    )


def process_and_ingest_file(
    file_path,
    ten_file,
    thu_muc,
    vision_model=None,
    progress_callback=None,
    domain_override=None,
    security_override=None,
    cong_doan_override=None,
    site_override=None,
    scan_sensitive=False,
    phong_ban_override=None,
    *,
    dependencies: IngestionPipelineDependencies | None = None,
    persistence: IngestionPersistence | None = None,
    config: PdfIngestionConfig | None = None,
):
    dependencies = require_pipeline_dependencies(dependencies)
    persistence = persistence or _legacy_ingestion_persistence()
    runtime_config = config or PdfIngestionConfig(
        image_dir=IMAGE_DIR,
        strict_require_vision=STRICT_INGEST_REQUIRE_VISION,
        rollback_on_error=ROLLBACK_ON_INGEST_ERROR,
    )
    splitter = get_token_splitter(runtime_config) if config is not None else token_splitter
    contextual_chunks = (
        runtime_config.contextual_chunk_enabled
        if config is not None
        else _contextual_chunk_enabled()
    )
    source_context = _initialize_source_context(
        thu_muc,
        domain_override=domain_override,
        security_override=security_override,
        site_override=site_override,
        scan_sensitive=scan_sensitive,
        phong_ban_override=phong_ban_override,
    )
    domain = source_context.domain
    scan_sensitive = source_context.scan_sensitive
    security_level = source_context.security_level
    site = source_context.site
    ext = os.path.splitext(file_path)[1].lower()
    start_time = time.time()
    report = _initialize_report(
        ten_file,
        total_pages=1,
        site_missing=source_context.site_missing,
        track_vision_cache_hits=False,
    )
    sensitive_scan = {"is_sensitive": False, "categories": [], "match_counts": {}}
    doc_id = None
    rendered_image_path = None
    try:
        # Create/load the governed document before any reader or metadata path
        # can call an external model. This gives the policy context a stable
        # document identity and ensures an early failure can restore snapshot.
        governed_document = _load_governed_document(
            ten_file,
            thu_muc,
            persistence=persistence,
        )
        doc_id = governed_document.doc_id
        doc_info = governed_document.info
        report["classification_failed"] = bool(
            doc_info.get("classification_failed")
        )
        external_policy = governed_document.external_policy
        _emit_progress(
            progress_callback,
            "extracting",
            f"Dang doc noi dung file {ext}...",
        )
        with external_document_context(
            doc_ids=[doc_id],
            security_levels=[security_level],
            policies=[external_policy],
        ):
            text_content, data_type = extract_text_from_supported_file(
                file_path,
                ten_file,
                vision_model,
            )
        text_content = text_content.strip()
        if not text_content:
            raise ValueError("Khong trich xuat duoc noi dung co the tim kiem tu file nay.")

        warning_count_before_metadata = len(report["warnings"])
        _meta_limit = runtime_config.metadata_text_limit
        with external_document_context(
            doc_ids=[doc_id],
            security_levels=[security_level],
            policies=[external_policy],
        ):
            metadata_kwargs = {"quality_warnings": report["warnings"]}
            if config is not None:
                metadata_kwargs["metadata_mode"] = (
                    runtime_config.llm_metadata_mode
                )
            info = extract_metadata_smart(
                text_content[:_meta_limit],
                ten_file,
                thu_muc,
                vision_model,
                **metadata_kwargs,
            )
        if len(report["warnings"]) > warning_count_before_metadata:
            report["metadata_llm_failed_pages"].append(1)
        if info.get("ten_tai_lieu") == "Khong ro":
            info["ten_tai_lieu"] = os.path.splitext(ten_file)[0]

        # Neu file tu hoc la hinh anh, copy no sang Data_Anh_Da_Tach de sau nay lam Ban ve can cu
        if ext in IMAGE_EXTENSIONS:
            import shutil
            safe_thu_muc = re.sub(r'[\\/*?:"<>|]', "", thu_muc) if thu_muc else ""
            base_name = os.path.splitext(ten_file)[0]
            if safe_thu_muc:
                img_name = f"{safe_thu_muc}_{base_name}_page1.png"
            else:
                img_name = f"{base_name}_page1.png"
            runtime_config.image_dir.mkdir(parents=True, exist_ok=True)
            img_path = os.path.join(runtime_config.image_dir, img_name)
            rendered_image_path = img_path
            # Convert sang PNG va luu
            try:
                img = Image.open(file_path)
                img.save(img_path, format="PNG")
            except Exception as e:
                logger.warning(f"Loi khi copy anh tu hoc vao Data_Anh_Da_Tach: {e}")

        if ext != ".pdf" and info.get("loai_tai_lieu") == "Ban ve gia cong":
            type_map = {
                "bang_du_lieu": "Bang du lieu",
                "van_ban_word": "Tai lieu Word",
                "slide": "Tai lieu trinh chieu",
                "image_summary": "Tai lieu anh/OCR",
                "van_ban_html": "Tai lieu HTML",
                "du_lieu_json": "Du lieu JSON",
                "du_lieu_xml": "Du lieu XML",
                "van_ban": "Tai lieu van ban",
            }
            info["loai_tai_lieu"] = type_map.get(data_type, "Tai lieu tong hop")

        # File 1 trang: persist metadata against the governed snapshot.
        persistence.save_page_metadata(
            ten_file,
            thu_muc,
            info,
            doc_id=doc_id,
        )
        vision_used = bool(data_type == "image_summary" and text_content.strip())
        if vision_used:
            report["pages_vision_success"].append(1)
            persistence.save_document_page(
                doc_id=doc_id,
                file_name=ten_file,
                page_no=1,
                text_extract="",
                vision_summary=text_content,
                extraction_status="success",
                image_path=rendered_image_path,
            )
        elif ext in MARKDOWN_EXTENSIONS:
            report["pages_text_extracted"].append(1)
            persistence.save_document_page(
                doc_id=doc_id,
                file_name=ten_file,
                page_no=1,
                text_extract=text_content,
                vision_summary="",
                extraction_status="success",
                image_path=None,
            )
            markdown_bom_records = extract_bom_records_from_markdown(text_content)
            if markdown_bom_records:
                persisted = persistence.save_bom_records(
                    doc_id,
                    1,
                    markdown_bom_records,
                )
                report["bom_rows_count"] += persisted
                report["pages_table_extracted"].append(1)
                if not persisted:
                    report["warnings"].append(
                        "structured_bom_persistence_failed:page:1"
                    )

        # GD4: duong nap hang loat khong tin folder tuyet doi -> quet noi dung nhay cam
        if scan_sensitive:
            from mech_chatbot.ingestion.sensitive_scanner import scan_sensitive_content, escalate_security
            sensitive_scan = scan_sensitive_content(text_content)
            if sensitive_scan.get("is_sensitive"):
                security_level = escalate_security(security_level, sensitive_scan)

        metadata = {
            "file_goc": ten_file,
            "thu_muc": thu_muc,
            "phong_ban_quyen": _normalize_phong_ban_quyen(thu_muc, phong_ban_override),
            "ma_doi_tuong": info["ma_doi_tuong"],
            "ma_chinh": info.get("ma_chinh", []),
            "ma_btp": info.get("ma_btp", []),
            "ma_vat_tu": info.get("ma_vat_tu", []),
            "ma_lien_quan": info.get("ma_lien_quan", []),
            "loai_tai_lieu": info["loai_tai_lieu"],
            "ten_san_pham": info["ten_tai_lieu"],
            "cong_doan": cong_doan_override or info["cong_doan"],
            "so_luong": info["so_luong"],
            "vat_lieu": info["vat_lieu"],
            "nguoi_lap": info["nguoi_lap"],
            "ngay_ve": info["ngay_ve"],
            "dung_sai_do_day": info["dung_sai_day"],
            "dung_sai_kich_thuoc": info["dung_sai_khac"],
            "kich_thuoc_tong_the": info["kich_thuoc"],
            # --- No-code resolver enrichment (Muc 3) ---
            "product_name": info.get("ten_tai_lieu", "") or "",
            "model": doc_info.get("variant_code", "default"),
            "doc_type": info.get("loai_tai_lieu", "") or "",
            "document_type": doc_info.get("document_type") or info.get("loai_tai_lieu", "") or "",
            "document_type_family": doc_info.get("document_type") or "",
            "title": doc_info.get("title", "") or "",
            "doc_number": doc_info.get("doc_number", "") or "",
            "material_list": ([info["vat_lieu"]] if info.get("vat_lieu") and str(info.get("vat_lieu")).strip() not in ("", "Khong ro", "Không rõ") else []),
            "dimensions_list": ([info["kich_thuoc"]] if info.get("kich_thuoc") else []),
            "ten_san_pham_norm": (info.get("ten_tai_lieu", "") or "").strip().lower(),
            "trang_so": 1,
            "parent_page": 1,
            "parent_section": (
                doc_info.get("parent_section")
                or
                info.get("cong_doan")
                or info.get("ten_tai_lieu")
                or info.get("loai_tai_lieu")
                or ""
            ),
            "dinh_dang_file": ext,
            "doc_status": "pending_review",
            "doc_id": doc_id,
            "family_id": doc_info.get("family_id"),
            "base_code": doc_info.get("base_code", ""),
            "version_no": doc_info.get("version_no", 1),
            "version_label": doc_info.get("version_label", ""),
            "variant_code": doc_info.get("variant_code", "default"),
            "variant_group": doc_info.get("variant_group", ""),
            "lifecycle_status": doc_info.get("lifecycle_status", "draft"),
            "review_status": doc_info.get("review_status", "pending_review"),
            "is_current": doc_info.get("is_current", False),
            "is_archived": doc_info.get("is_archived", False),
            "supersedes_doc_id": doc_info.get("supersedes_doc_id"),
            "publication_state": doc_info.get("publication_state", "draft"),
            "publication_version": doc_info.get("publication_version", 1),
            "serving_epoch": doc_info.get("serving_epoch", 0),
            "servable": bool(doc_info.get("servable", False)),
            "owner_department": doc_info.get("owner_department") or thu_muc,
            "knowledge_owner_user_id": doc_info.get("knowledge_owner_user_id"),
            "knowledge_approver_user_id": doc_info.get("knowledge_approver_user_id"),
            "taxonomy_version": doc_info.get("taxonomy_version", "v1"),
            "parent_applicable": bool(doc_info.get("parent_applicable", True)),
            "parent_context_enabled": bool(doc_info.get("parent_context_enabled", True)),
            "source_system": doc_info.get("source_system", "upload"),
            "external_processing_policy": external_policy,
            "vision_used": vision_used,
            # Multi-domain (P0)
            "domain": domain,
            "security_level": security_level,
            # Multi-site (P1.2)
            "site": site,
            # Trang thai hieu luc canonical cho ingest moi.
            "effective_status": "effective",
        }
        info["trang_so"] = 1

        all_chunks = []
        title_block = (
            f"Thong tin tai lieu {ten_file}:\n"
            f"- Ma chinh: {info.get('ma_chinh', [])}\n"
            f"- Ma BTP: {info.get('ma_btp', [])}\n"
            f"- Ma vat tu: {info.get('ma_vat_tu', [])}\n"
            f"- Ma lien quan: {info.get('ma_lien_quan', [])}\n"
            f"- Ma doi tuong tong hop: {info['ma_doi_tuong']}\n"
            f"- Loai tai lieu: {info['loai_tai_lieu']}\n"
            f"- Ten tai lieu/san pham: {info['ten_tai_lieu']}\n"
            f"- Cong doan/thu muc: {info['cong_doan']}\n"
            f"- Vat lieu: {info['vat_lieu']}\n"
            f"- Dinh dang file: {ext}\n"
        )
        all_chunks.append(Document(page_content=title_block, metadata={**metadata, "loai_du_lieu": "title_block"}))

        # Dung chung token_splitter da dinh nghia theo gioi han embedding.
        chunks = splitter.split_text(text_content)
        for i, chunk in enumerate(chunks):
            if chunk.strip():
                all_chunks.append(Document(
                    page_content=chunk,
                    metadata={**metadata, "loai_du_lieu": data_type, "chunk_index": i + 1}
                ))

        # FIX #3: GIU text goc cho LLM truoc khi tokenize cho BM25 (ap dung TOAN BO chunks)
        for chunk in all_chunks:
            chunk.metadata["noi_dung_goc"] = chunk.page_content
            _embed_src = chunk.page_content
            if contextual_chunks:
                _cpref = _build_chunk_context_prefix(chunk.metadata)
                if _cpref:
                    _embed_src = _cpref + "\n" + chunk.page_content
            chunk.page_content = tokenize_cached(_embed_src)

        if all_chunks:
            # Document Versioning: Xoa vector cu
            try:
                _delete_vectors_for_file(
                    ten_file,
                    thu_muc,
                    doc_id=doc_id,
                    dependencies=dependencies,
                )
            except Exception as e:
                logger.warning(f"Khong xoa duoc vector cu (bo qua, tiep tuc): {ten_file}: {e}")

            _emit_progress(
                progress_callback,
                "embedding",
                "Đang tạo embedding...",
            )
            _add_docs_with_retry(all_chunks, dependencies=dependencies)
            report["total_chunks"] += len(all_chunks)

    except Exception as e:
        logger.error(f"Loi doc file {ten_file}: {e}", exc_info=True)
        report["status"] = "error"
        report["message"] = str(e)

    if report["status"] == "success" and report["total_chunks"] == 0:
        report["status"] = "error"

    finalized_source = replace(source_context, security_level=security_level)
    return _finalize_pipeline_transaction(
        report,
        dependencies=dependencies,
        doc_id=doc_id,
        ten_file=ten_file,
        thu_muc=thu_muc,
        phong_ban_override=phong_ban_override,
        context=_QualityReportContext(
            source=finalized_source,
            sensitive_scan=sensitive_scan,
            started_at=start_time,
            source_kind="file",
            file_extension=ext,
        ),
        rollback_on_error=runtime_config.rollback_on_error,
        persistence=persistence,
    )

__all__ = [
    '_add_docs_with_retry',
    '_delete_vectors_for_file',
    'process_and_ingest_pdf',
    'process_and_ingest_file',
]
