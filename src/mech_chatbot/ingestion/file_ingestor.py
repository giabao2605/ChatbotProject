import os
from mech_chatbot.llm.vision_client import build_vision_model
from mech_chatbot.ingestion.pdf_processor import (
    PDF_EXTENSIONS,
    SUPPORTED_LEARNING_EXTENSIONS,
    process_and_ingest_pdf,
    process_and_ingest_file,
)
from mech_chatbot.config.logging import logger

# Cau hinh Gemini API (Vision) - migrate sang google-genai qua gemini_client
vision_model = build_vision_model()


def learn_new_file(file_path, ten_file, thu_muc="Tu_Hoc", progress_callback=None,
                   domain_override=None, security_override=None,
                   cong_doan_override=None, site_override=None,
                   scan_sensitive=False):
    """
    Doc file moi, trich xuat metadata, goi Gemini Vision va nap vao Qdrant DB.

    GD4: cac override (domain/security/cong_doan/site) den tu form upload;
    neu None thi ingest tu suy theo folder. scan_sensitive=True (duong nap hang
    loat tu fileserver) bat bo do noi dung nhay cam -> nang 'confidential' + review.
    """
    if not os.path.exists(file_path):
        logger.error(f"File vat ly khong ton tai: {file_path}")
        return False, "Khong tim thay file he thong de xu ly.", {}

    logger.info(f"Bat dau hoc file moi: {ten_file}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_LEARNING_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_LEARNING_EXTENSIONS))
        return False, f"Dinh dang {ext or '(khong co duoi file)'} chua duoc ho tro. Cac dinh dang dang ho tro: {supported}", {}

    _ov = dict(
        domain_override=domain_override,
        security_override=security_override,
        cong_doan_override=cong_doan_override,
        site_override=site_override,
        scan_sensitive=scan_sensitive,
    )
    if ext in PDF_EXTENSIONS:
        report = process_and_ingest_pdf(file_path, ten_file, thu_muc, vision_model, progress_callback, **_ov)
    else:
        report = process_and_ingest_file(file_path, ten_file, thu_muc, vision_model, progress_callback, **_ov)

    if report["status"] == "success":
        logger.info(f"Hoc file thanh cong: {report['message']}")
        return True, report["message"], report
    else:
        logger.error(f"Loi hoc file: {report['message']}")
        return False, report["message"], report
