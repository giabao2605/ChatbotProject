# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import."""

import os
import unicodedata


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


EMBEDDING_CHUNK_SIZE = int(os.getenv("EMBEDDING_CHUNK_SIZE", "220"))


EMBEDDING_CHUNK_OVERLAP = int(os.getenv("EMBEDDING_CHUNK_OVERLAP", "40"))


STRICT_INGEST_REQUIRE_VISION = _env_bool("STRICT_INGEST_REQUIRE_VISION", True)


ROLLBACK_ON_INGEST_ERROR = _env_bool("ROLLBACK_ON_INGEST_ERROR", True)


GEMINI_METADATA_MODE = os.getenv("GEMINI_METADATA_MODE", "missing_only").strip().lower()


def remove_accents(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(
        ch for ch in normalized
        if unicodedata.category(ch) != "Mn"
    )


EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")


BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


IMAGE_DIR = os.path.join(BASE_DIR, "data", "processed")


os.makedirs(IMAGE_DIR, exist_ok=True)


PDF_EXTENSIONS = {".pdf"}


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".sql",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".json", ".xml", ".yaml",
    ".yml", ".ini", ".cfg",
}


HTML_EXTENSIONS = {".html", ".htm"}


TABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls"}


WORD_EXTENSIONS = {".docx"}


PRESENTATION_EXTENSIONS = {".pptx"}


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


SUPPORTED_LEARNING_EXTENSIONS = (
    PDF_EXTENSIONS
    | TEXT_EXTENSIONS
    | HTML_EXTENSIONS
    | TABLE_EXTENSIONS
    | WORD_EXTENSIONS
    | PRESENTATION_EXTENSIONS
    | IMAGE_EXTENSIONS
)

__all__ = [
    '_env_bool',
    'EMBEDDING_CHUNK_SIZE',
    'EMBEDDING_CHUNK_OVERLAP',
    'STRICT_INGEST_REQUIRE_VISION',
    'ROLLBACK_ON_INGEST_ERROR',
    'GEMINI_METADATA_MODE',
    'remove_accents',
    'EMBEDDING_MODEL_NAME',
    'BASE_DIR',
    'IMAGE_DIR',
    'PDF_EXTENSIONS',
    'TEXT_EXTENSIONS',
    'HTML_EXTENSIONS',
    'TABLE_EXTENSIONS',
    'WORD_EXTENSIONS',
    'PRESENTATION_EXTENSIONS',
    'IMAGE_EXTENSIONS',
    'SUPPORTED_LEARNING_EXTENSIONS',
]
