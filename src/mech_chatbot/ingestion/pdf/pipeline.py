"""Compatibility entrypoints for the PDF/file ingestion implementation.

The implementation has one owner in ``pipeline_implementation``. This module
keeps legacy imports stable while forwarding every call without duplicating
the pipeline lifecycle.
"""

from __future__ import annotations

from typing import Any

from mech_chatbot.domain.ingestion_progress import IngestionProgressEvent
from mech_chatbot.ingestion.pdf import pipeline_implementation as _implementation


def _legacy_callback(callback: Any) -> Any:
    if callback is None:
        return None

    def receive(event: IngestionProgressEvent) -> None:
        if event.phase == "embedding":
            callback("__STATUS__:embedding")
        else:
            callback(event.message)

    return receive


def process_and_ingest_pdf(
    pdf_path: str,
    ten_file: str,
    thu_muc: str,
    vision_model: Any = None,
    progress_callback: Any = None,
    domain_override: str | None = None,
    security_override: str | None = None,
    cong_doan_override: str | None = None,
    site_override: str | None = None,
    scan_sensitive: bool = False,
    phong_ban_override: Any = None,
) -> dict[str, Any]:
    return _implementation.process_and_ingest_pdf(
        pdf_path,
        ten_file,
        thu_muc,
        vision_model,
        _legacy_callback(progress_callback),
        domain_override,
        security_override,
        cong_doan_override,
        site_override,
        scan_sensitive,
        phong_ban_override,
    )


def process_and_ingest_file(
    file_path: str,
    ten_file: str,
    thu_muc: str,
    vision_model: Any = None,
    progress_callback: Any = None,
    domain_override: str | None = None,
    security_override: str | None = None,
    cong_doan_override: str | None = None,
    site_override: str | None = None,
    scan_sensitive: bool = False,
    phong_ban_override: Any = None,
) -> dict[str, Any]:
    return _implementation.process_and_ingest_file(
        file_path,
        ten_file,
        thu_muc,
        vision_model,
        _legacy_callback(progress_callback),
        domain_override,
        security_override,
        cong_doan_override,
        site_override,
        scan_sensitive,
        phong_ban_override,
    )


__all__ = ["process_and_ingest_file", "process_and_ingest_pdf"]
