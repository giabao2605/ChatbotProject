"""Evidence-focused citation selection and source rendering."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def select_citation_docs(
    docs: Any,
    question: str = "",
    is_bom_query: bool = False,
    part_ids: Any = None,
    limit: int | None = None,
) -> list[Any]:
    """Return a small citation set without changing generation candidates."""

    candidates = list(docs or [])
    if not candidates:
        return []
    try:
        from mech_chatbot.rag.text_utils import remove_accents

        normalized_question = remove_accents(str(question or "").lower())
    except Exception:
        normalized_question = str(question or "").lower()
    bom_mode = bool(is_bom_query) or any(
        token in normalized_question
        for token in ("bom", "bang ke vat tu", "vat tu", "bill of materials")
    )
    wanted_codes = {
        str(value).strip().lower() for value in (part_ids or []) if value
    }

    def values(metadata: dict[str, Any], *keys: str) -> set[str]:
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

    def is_bom_evidence(doc: Any) -> bool:
        metadata = getattr(doc, "metadata", {}) or {}
        kind = str(metadata.get("loai_du_lieu") or "").lower()
        source = str(metadata.get("file_goc") or "").lower()
        if kind in {"sql_bom", "bang_ke_vat_tu", "bom"} or "bom" in source:
            return True
        if wanted_codes:
            codes = values(
                metadata,
                "base_code",
                "ma_chinh",
                "ma_doi_tuong",
                "ma_btp",
                "ma_vat_tu",
            )
            return bool(codes & wanted_codes)
        return False

    pool = [doc for doc in candidates if is_bom_evidence(doc)] if bom_mode else candidates
    if not pool:
        pool = candidates
    max_sources = int(limit or os.getenv("CITATION_MAX_SOURCES", "5"))
    if bom_mode:
        max_sources = min(
            max_sources,
            int(os.getenv("BOM_CITATION_MAX_SOURCES", "3")),
        )

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


def build_source_citations(docs: Any) -> tuple[str, list[str]]:
    references: list[str] = []
    reference_images: list[str] = []
    for doc in docs:
        metadata = doc.metadata
        source = metadata.get("file_goc", "Khong ro")
        page = metadata.get("trang_so", "?")
        stage = metadata.get("cong_doan", "Khong ro")
        data_type = metadata.get("loai_du_lieu", "")
        folder = metadata.get("phong_ban_quyen", "")
        if isinstance(folder, (list, tuple)):
            folder = folder[0] if folder else ""
        doc_id = metadata.get("doc_id")
        site = metadata.get("site")
        version_no = metadata.get("version_no")

        citation = f"**{source}** (Trang {page}) - {stage}"
        tags: list[str] = []
        if folder:
            tags.append(str(folder))
        if site:
            tags.append(f"khu {site}")
        if version_no:
            tags.append(f"v{version_no}")
        if doc_id is not None:
            tags.append(f"DocID {doc_id}")
        if tags:
            citation += "  \u00b7 _" + " | ".join(tags) + "_"
        if data_type == "image_summary":
            citation += " *(phan tich hinh anh)*"
        if citation not in references:
            references.append(citation)

        if source != "Anh dinh kem tu nguoi dung":
            safe_folder = re.sub(r'[\\/*?:"<>|]', "", folder) if folder else ""
            base_name = os.path.splitext(str(source))[0]
            image_name = (
                f"{safe_folder}_{base_name}_page{page}.png"
                if safe_folder
                else f"{base_name}_page{page}.png"
            )
            project_root = Path(__file__).resolve().parents[4]
            image_path = str(project_root / "data" / "processed" / image_name)
            if image_path not in reference_images and os.path.exists(image_path):
                reference_images.append(image_path)

    if not references:
        return "", []
    reference_text = "\n\n---\n**Nguon tham chieu:**\n" + "\n".join(
        f"- {reference}" for reference in references
    )
    return reference_text, reference_images
