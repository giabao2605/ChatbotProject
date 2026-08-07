# -*- coding: utf-8 -*-
"""Pure ingestion configuration and file-type policy.

Environment parsing belongs to the process composition root.  This module is
safe to import: it neither reads process environment nor creates directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unicodedata

from mech_chatbot.domain.document_types import (
    HTML_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MARKDOWN_EXTENSIONS,
    PDF_EXTENSIONS,
    PRESENTATION_EXTENSIONS,
    SUPPORTED_LEARNING_EXTENSIONS,
    TABLE_EXTENSIONS,
    TEXT_EXTENSIONS,
    WORD_EXTENSIONS,
)


BASE_DIR = str(Path(__file__).resolve().parents[3])
IMAGE_DIR = str(Path(BASE_DIR) / "data" / "processed")
DEFAULT_VISION_CACHE_DIR = str(Path(BASE_DIR) / "data" / "cache" / "vision")

EMBEDDING_CHUNK_SIZE = 220
EMBEDDING_CHUNK_OVERLAP = 40
STRICT_INGEST_REQUIRE_VISION = False
ROLLBACK_ON_INGEST_ERROR = True
LLM_METADATA_MODE = "missing_only"
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"


@dataclass(frozen=True, slots=True)
class PdfIngestionConfig:
    """Narrow immutable configuration consumed by the PDF ingestion adapter."""

    image_dir: Path = Path(IMAGE_DIR)
    embedding_chunk_size: int = EMBEDDING_CHUNK_SIZE
    embedding_chunk_overlap: int = EMBEDDING_CHUNK_OVERLAP
    embedding_model_name: str = EMBEDDING_MODEL_NAME
    llm_metadata_mode: str = LLM_METADATA_MODE
    contextual_chunk_enabled: bool = False
    strict_require_vision: bool = STRICT_INGEST_REQUIRE_VISION
    rollback_on_error: bool = ROLLBACK_ON_INGEST_ERROR
    pdf_render_dpi: int = 300
    metadata_text_limit: int = 20_000
    vision_prewarm_workers: int = 1
    vision_cache_enabled: bool = True
    vision_cache_dir: Path = Path(DEFAULT_VISION_CACHE_DIR)

    def __post_init__(self) -> None:
        object.__setattr__(self, "image_dir", Path(self.image_dir))
        object.__setattr__(self, "vision_cache_dir", Path(self.vision_cache_dir))
        object.__setattr__(
            self,
            "embedding_model_name",
            str(self.embedding_model_name).strip(),
        )
        object.__setattr__(
            self,
            "llm_metadata_mode",
            str(self.llm_metadata_mode).strip().lower(),
        )
        if self.embedding_chunk_size <= 0:
            raise ValueError("EMBEDDING_CHUNK_SIZE must be greater than zero.")
        if self.embedding_chunk_overlap < 0:
            raise ValueError(
                "EMBEDDING_CHUNK_OVERLAP must be zero or greater."
            )
        if self.embedding_chunk_overlap >= self.embedding_chunk_size:
            raise ValueError(
                "EMBEDDING_CHUNK_OVERLAP must be smaller than chunk size."
            )
        if not self.embedding_model_name:
            raise ValueError("EMBEDDING_MODEL must not be empty.")
        if self.pdf_render_dpi <= 0:
            raise ValueError("PDF_RENDER_DPI must be greater than zero.")
        if self.metadata_text_limit <= 0:
            raise ValueError("METADATA_TEXT_LIMIT must be greater than zero.")
        if self.vision_prewarm_workers < 0:
            raise ValueError(
                "INGEST_VISION_PREWARM_WORKERS must be zero or greater."
            )

    @classmethod
    def from_settings(cls, settings: object) -> "PdfIngestionConfig":
        """Project the canonical startup snapshot into this narrow config."""

        cache_dir = getattr(settings, "VISION_CACHE_DIR", None)
        return cls(
            embedding_chunk_size=int(
                getattr(settings, "EMBEDDING_CHUNK_SIZE")
            ),
            embedding_chunk_overlap=int(
                getattr(settings, "EMBEDDING_CHUNK_OVERLAP")
            ),
            embedding_model_name=str(getattr(settings, "EMBEDDING_MODEL")),
            llm_metadata_mode=str(getattr(settings, "LLM_METADATA_MODE")),
            contextual_chunk_enabled=bool(
                getattr(settings, "ENABLE_CONTEXTUAL_CHUNK")
            ),
            strict_require_vision=bool(
                getattr(settings, "STRICT_INGEST_REQUIRE_VISION")
            ),
            rollback_on_error=bool(
                getattr(settings, "ROLLBACK_ON_INGEST_ERROR")
            ),
            pdf_render_dpi=int(getattr(settings, "PDF_RENDER_DPI")),
            metadata_text_limit=int(getattr(settings, "METADATA_TEXT_LIMIT")),
            vision_prewarm_workers=int(
                getattr(settings, "INGEST_VISION_PREWARM_WORKERS")
            ),
            vision_cache_enabled=bool(
                getattr(settings, "VISION_CACHE_ENABLED")
            ),
            vision_cache_dir=(
                Path(cache_dir)
                if cache_dir
                else Path(DEFAULT_VISION_CACHE_DIR)
            ),
        )


def default_pdf_ingestion_config() -> PdfIngestionConfig:
    """Return legacy defaults without consulting mutable process state."""

    return PdfIngestionConfig(
        image_dir=Path(IMAGE_DIR),
        embedding_chunk_size=int(EMBEDDING_CHUNK_SIZE),
        embedding_chunk_overlap=int(EMBEDDING_CHUNK_OVERLAP),
        embedding_model_name=str(EMBEDDING_MODEL_NAME),
        llm_metadata_mode=str(LLM_METADATA_MODE),
        strict_require_vision=bool(STRICT_INGEST_REQUIRE_VISION),
        rollback_on_error=bool(ROLLBACK_ON_INGEST_ERROR),
    )


def remove_accents(text: str) -> str:
    if text is None:
        return ""
    normalized_text = str(text).replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", normalized_text)
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )


__all__ = [
    "BASE_DIR",
    "DEFAULT_VISION_CACHE_DIR",
    "EMBEDDING_CHUNK_OVERLAP",
    "EMBEDDING_CHUNK_SIZE",
    "EMBEDDING_MODEL_NAME",
    "HTML_EXTENSIONS",
    "IMAGE_DIR",
    "IMAGE_EXTENSIONS",
    "LLM_METADATA_MODE",
    "MARKDOWN_EXTENSIONS",
    "PDF_EXTENSIONS",
    "PRESENTATION_EXTENSIONS",
    "PdfIngestionConfig",
    "ROLLBACK_ON_INGEST_ERROR",
    "STRICT_INGEST_REQUIRE_VISION",
    "SUPPORTED_LEARNING_EXTENSIONS",
    "TABLE_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "WORD_EXTENSIONS",
    "default_pdf_ingestion_config",
    "remove_accents",
]
