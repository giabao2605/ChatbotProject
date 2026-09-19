"""Canonical part-identifier metadata shared by ingestion and retrieval."""

from __future__ import annotations

from collections.abc import Mapping


PART_ID_METADATA_FIELDS = (
    "ma_chinh",
    "ma_btp",
    "ma_vat_tu",
    "ma_lien_quan",
    "ma_doi_tuong",
)


def canonical_part_id(value) -> str:
    return str(value or "").strip().upper()


def canonical_part_ids(value) -> list[str]:
    raw_values = [value] if isinstance(value, str) else list(value or ())
    return list(dict.fromkeys(
        canonical
        for item in raw_values
        if (canonical := canonical_part_id(item))
    ))


def canonical_part_id_updates(metadata: Mapping) -> dict[str, list[str]]:
    updates = {}
    for field in PART_ID_METADATA_FIELDS:
        if field not in metadata:
            continue
        raw_value = metadata.get(field)
        current = [
            str(item).strip()
            for item in ([raw_value] if isinstance(raw_value, str) else (raw_value or ()))
            if str(item).strip()
        ]
        canonical = canonical_part_ids(raw_value)
        if canonical != current:
            updates[field] = canonical
    return updates
