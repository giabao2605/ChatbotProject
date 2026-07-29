"""Evidence-focused citation selection and source rendering."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def _normalize_question(question: str) -> str:
    try:
        from mech_chatbot.rag.text_utils import remove_accents

        return remove_accents(str(question or "").lower())
    except Exception:
        return str(question or "").lower()


def _metadata_values(metadata: dict[str, Any], *keys: str) -> set[str]:
    found: list[Any] = []
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, (list, tuple, set)):
            found.extend(value)
        elif value is not None:
            found.append(value)
    return {
        str(value).strip().lower()
        for value in found
        if str(value).strip()
    }


def _is_bom_evidence(doc: Any, wanted_codes: set[str]) -> bool:
    metadata = getattr(doc, "metadata", {}) or {}
    kind = str(metadata.get("loai_du_lieu") or "").lower()
    source = str(metadata.get("file_goc") or "").lower()
    if kind in {"sql_bom", "bang_ke_vat_tu", "bom"} or "bom" in source:
        return True
    if not wanted_codes:
        return False
    codes = _metadata_values(
        metadata,
        "base_code",
        "ma_chinh",
        "ma_doi_tuong",
        "ma_btp",
        "ma_vat_tu",
    )
    return bool(codes & wanted_codes)


def _deduplicate_sources(pool: list[Any], max_sources: int) -> list[Any]:
    selected: list[Any] = []
    seen: set[tuple[Any, Any]] = set()
    for doc in pool:
        metadata = getattr(doc, "metadata", {}) or {}
        key = (
            metadata.get("doc_id") or metadata.get("file_goc"),
            metadata.get("trang_so") or metadata.get("parent_page"),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(doc)
        if len(selected) >= max_sources:
            break
    return selected


def select_citation_docs(
    docs: Any,
    question: str = "",
    is_bom_query: bool = False,
    part_ids: Any = None,
    limit: int | None = None,
    bom_limit: int = 3,
) -> list[Any]:
    """Return a small citation set without changing generation candidates."""

    candidates = list(docs or [])
    if not candidates:
        return []
    normalized_question = _normalize_question(question)
    bom_mode = bool(is_bom_query) or any(
        token in normalized_question
        for token in ("bom", "bang ke vat tu", "vat tu", "bill of materials")
    )
    wanted_codes = {
        str(value).strip().lower() for value in (part_ids or []) if value
    }

    pool = (
        [doc for doc in candidates if _is_bom_evidence(doc, wanted_codes)]
        if bom_mode
        else candidates
    )
    if not pool:
        pool = candidates
    max_sources = int(limit or 5)
    if bom_mode:
        max_sources = min(max_sources, int(bom_limit))

    return _deduplicate_sources(pool, max_sources)


def _normalized_folder(metadata: dict[str, Any]) -> Any:
    folder = metadata.get("phong_ban_quyen", "")
    if isinstance(folder, (list, tuple)):
        return folder[0] if folder else ""
    return folder


def _render_citation(metadata: dict[str, Any], folder: Any) -> str:
    source = metadata.get("file_goc", "Khong ro")
    page = metadata.get("trang_so", "?")
    stage = metadata.get("cong_doan", "Khong ro")
    citation = f"**{source}** (Trang {page}) - {stage}"
    tags: list[str] = []
    if folder:
        tags.append(str(folder))
    if metadata.get("site"):
        tags.append(f"khu {metadata['site']}")
    if metadata.get("version_no"):
        tags.append(f"v{metadata['version_no']}")
    if metadata.get("doc_id") is not None:
        tags.append(f"DocID {metadata['doc_id']}")
    if tags:
        citation += "  · _" + " | ".join(tags) + "_"
    if metadata.get("loai_du_lieu", "") == "image_summary":
        citation += " *(phan tich hinh anh)*"
    return citation


def _find_reference_image(metadata: dict[str, Any], folder: Any) -> str | None:
    source = metadata.get("file_goc", "Khong ro")
    if source == "Anh dinh kem tu nguoi dung":
        return None
    safe_folder = re.sub(r'[\\/*?:"<>|]', "", folder) if folder else ""
    base_name = os.path.splitext(str(source))[0]
    page = metadata.get("trang_so", "?")
    image_name = (
        f"{safe_folder}_{base_name}_page{page}.png"
        if safe_folder
        else f"{base_name}_page{page}.png"
    )
    project_root = Path(__file__).resolve().parents[4]
    image_path = str(project_root / "data" / "processed" / image_name)
    return image_path if os.path.exists(image_path) else None


def build_source_citations(docs: Any) -> tuple[str, list[str]]:
    references: list[str] = []
    reference_images: list[str] = []
    for doc in docs:
        metadata = doc.metadata
        folder = _normalized_folder(metadata)
        citation = _render_citation(metadata, folder)
        if citation not in references:
            references.append(citation)

        image_path = _find_reference_image(metadata, folder)
        if image_path and image_path not in reference_images:
            reference_images.append(image_path)

    if not references:
        return "", []
    reference_text = "\n\n---\n**Nguon tham chieu:**\n" + "\n".join(
        f"- {reference}" for reference in references
    )
    return reference_text, reference_images
