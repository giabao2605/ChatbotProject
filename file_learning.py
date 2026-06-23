import os
from gemini_client import build_vision_model
from pdf_processor import (
    PDF_EXTENSIONS,
    SUPPORTED_LEARNING_EXTENSIONS,
    process_and_ingest_pdf,
    process_and_ingest_file,
)
from logger_config import logger

# Cau hinh Gemini API (Vision) - migrate sang google-genai qua gemini_client
vision_model = build_vision_model()


def learn_new_file(file_path, ten_file, thu_muc="Tu_Hoc", progress_callback=None):
    """
    Doc file moi, trich xuat metadata, goi Gemini Vision va nap vao Qdrant DB.
    """
    if not os.path.exists(file_path):
        logger.error(f"File vat ly khong ton tai: {file_path}")
        return False, "Khong tim thay file he thong de xu ly.", {}

    logger.info(f"Bat dau hoc file moi: {ten_file}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_LEARNING_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_LEARNING_EXTENSIONS))
        return False, f"Dinh dang {ext or '(khong co duoi file)'} chua duoc ho tro. Cac dinh dang dang ho tro: {supported}", {}

    if ext in PDF_EXTENSIONS:
        report = process_and_ingest_pdf(file_path, ten_file, thu_muc, vision_model, progress_callback)
    else:
        report = process_and_ingest_file(file_path, ten_file, thu_muc, vision_model, progress_callback)

    if report["status"] == "success":
        logger.info(f"Hoc file thanh cong: {report['message']}")
        return True, report["message"], report
    else:
        logger.error(f"Loi hoc file: {report['message']}")
        return False, report["message"], report
