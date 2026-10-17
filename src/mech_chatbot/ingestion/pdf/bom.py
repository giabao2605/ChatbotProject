# -*- coding: utf-8 -*-
"""Structured BOM extraction shared by PDF and Markdown ingestion."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from dataclasses import replace
import json
import math
import re

from mech_chatbot.ingestion.pdf.config import remove_accents
from mech_chatbot.domain.structured_evidence import AssemblyStep, NumericFact, ScopeManifest, SourceLocator, TextFact
from mech_chatbot.ingestion.pdf.structured import parse_exact_decimal


_MARKDOWN_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_NUMBER_TOKEN = re.compile(r"[-+]?\d[\d.,\s]*")


def _split_markdown_row(line: str) -> list[str]:
    placeholder = "\x00PIPE\x00"
    value = str(line or "").strip().replace(r"\|", placeholder)
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.replace(placeholder, "|").strip() for cell in value.split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(
        _MARKDOWN_SEPARATOR.fullmatch(cell.replace(" ", ""))
        for cell in cells
    )


def extract_markdown_tables(text: str) -> list[list[list[str]]]:
    """Return GitHub-style Markdown tables while ignoring fenced examples."""
    lines = str(text or "").splitlines()
    tables: list[list[list[str]]] = []
    in_fence = False
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            index += 1
            continue
        if in_fence or "|" not in stripped or index + 1 >= len(lines):
            index += 1
            continue
        header = _split_markdown_row(stripped)
        separator = _split_markdown_row(lines[index + 1])
        if len(header) < 2 or len(separator) != len(header) or not _is_separator_row(separator):
            index += 1
            continue
        rows = [header]
        index += 2
        while index < len(lines):
            candidate = lines[index].strip()
            if not candidate or "|" not in candidate:
                break
            cells = _split_markdown_row(candidate)
            if len(cells) != len(header):
                break
            rows.append(cells)
            index += 1
        tables.append(rows)
    return tables


def _normalized_header(value: str) -> str:
    normalized = remove_accents(str(value or "").casefold())
    normalized = re.sub(r"[_-]+", " ", normalized)
    return " ".join(normalized.split())


def _column_kind(header: str) -> str | None:
    value = _normalized_header(header)
    if value in {"stt", "position", "item", "item no", "vi tri"}:
        return "position"
    if value in {"row id", "source row", "source row id"}:
        return "row_id"
    if value in {"quantity", "qty", "value", "gia tri", "so luong", "sl"}:
        return "sl"
    if value in {"unit", "don vi", "dvt"}:
        return "unit"
    if value in {"material", "material code", "vat lieu", "ma vat lieu"}:
        return "vat_lieu"
    if value in {"description", "ten", "ten vat tu", "vat tu", "ten hang", "ten goi", "mo ta"}:
        return "ten"
    if value in {
        "ma", "code", "part", "part code", "ma hang", "ma chi tiet",
        "ma btp", "ma tp", "ky hieu",
    }:
        return "ma"
    if value in {"note", "notes", "ghi chu"}:
        return "ghi_chu"
    return None


def _decimal_text(raw: object) -> str | None:
    match = _NUMBER_TOKEN.search(str(raw or ""))
    if not match:
        return None
    token = match.group(0).replace(" ", "")
    if "," in token and "." in token:
        decimal_separator = "," if token.rfind(",") > token.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        token = token.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in token:
        left, right = token.rsplit(",", 1)
        token = left + right if len(right) == 3 else left + "." + right
    elif "." in token:
        left, right = token.rsplit(".", 1)
        token = left + right if len(right) == 3 else left + "." + right
    try:
        value = Decimal(token)
    except InvalidOperation:
        return None
    return format(value, "f")


def extract_drawing_tables(page):
    """Preserve close grid borders that the default 3-point snapping merges."""
    return page.extract_tables(table_settings={"snap_tolerance": 2})


def extract_drawing_table_sources(page):
    """Pair extracted rows with native cell geometry without changing legacy tables."""
    if not callable(getattr(page, 'find_tables', None)):
        return tuple((rows, ()) for rows in extract_drawing_tables(page))
    return tuple((table.extract(), tuple(tuple(row.cells) for row in table.rows))
                 for table in page.find_tables(table_settings={'snap_tolerance': 2}))


def _valid_bbox(value):
    try:
        if (not isinstance(value, (tuple, list)) or len(value) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in value)
                or value[0] >= value[2] or value[1] >= value[3]):
            return None
    except OverflowError:
        return None
    return tuple(value)


def _bom_row_bbox(row_cells, physical_row, columns):
    if not isinstance(row_cells, (tuple, list)) or physical_row >= len(row_cells):
        return None
    cells = row_cells[physical_row]
    if not isinstance(cells, (tuple, list)) or any(index >= len(cells) for index in columns.values()):
        return None
    boxes = tuple(cells[index] for index in columns.values() if cells[index] is not None)
    if not boxes or any(_valid_bbox(box) is None for box in boxes):
        return None
    return (min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes))


def extract_bom_records(table, table_idx=None, *, row_cells=None):
    """Extract BOM rows and preserve exact quantity/source identity in RawRowJson."""
    records = []
    if not table or len(table) < 2:
        return records
    cleaned_table = [
        [str(cell).replace("\n", " ").strip() if cell is not None else "" for cell in row]
        for row in table
    ]
    header_index = -1
    columns: dict[str, int] = {}
    for row_index in range(min(5, len(cleaned_table))):
        mapped = {
            kind: column_index
            for column_index, header in enumerate(cleaned_table[row_index])
            if (kind := _column_kind(header)) is not None
        }
        if ("ma" in mapped or "ten" in mapped) and (
            "sl" in mapped or "vat_lieu" in mapped
        ):
            header_index = row_index
            columns = mapped
            break
    if header_index < 0:
        return records

    def cell(row: list[str], kind: str) -> str:
        index = columns.get(kind, -1)
        return row[index] if 0 <= index < len(row) else ""

    table_number = 1 if table_idx is None else int(table_idx) + 1
    for source_row_index, row in enumerate(
        cleaned_table[header_index + 1:], start=1
    ):
        quantity_decimal = _decimal_text(cell(row, "sl"))
        quantity_integer = None
        if quantity_decimal is not None:
            quantity_value = Decimal(quantity_decimal)
            if quantity_value == quantity_value.to_integral_value():
                quantity_integer = int(quantity_value)
        source_row_id = cell(row, "row_id") or (
            f"table-{table_number}-row-{source_row_index}"
        )
        record = {
            "ma_hang": cell(row, "ma"),
            "ten_vat_tu": cell(row, "ten"),
            "vat_lieu": cell(row, "vat_lieu"),
            "so_luong": quantity_integer,
            "quantity_decimal": quantity_decimal,
            "ghi_chu": cell(row, "ghi_chu"),
            "don_vi": cell(row, "unit"),
            "source_row_id": source_row_id,
            "source_table_index": table_number,
            "bom_position": cell(row, "position"),
            "review_status": "pending",
        }
        bbox = _bom_row_bbox(row_cells, header_index + source_row_index, columns)
        if bbox is not None:
            record = {**record, 'source_row_bbox': bbox}
        reviewed_quantity, quantity_conflicts = parse_exact_decimal(cell(row, "sl"))
        conflicts = ["missing_quantity" if reason == "missing_value" else reason for reason in quantity_conflicts]
        name_materials = set(re.findall(r"inox\s*(\d{3})", record["ten_vat_tu"], re.I))
        column_materials = set(re.findall(r"inox\s*(\d{3})", record["vat_lieu"], re.I))
        if name_materials and column_materials and name_materials != column_materials:
            conflicts.append("material_conflict")
        record["conflicts"] = conflicts
        record["quantity_review_decimal"] = format(reviewed_quantity, "f") if reviewed_quantity is not None else None
        record["confidence"] = 0.9 if record["ma_hang"] and quantity_decimal is not None else 0.5
        record["raw_row_json"] = json.dumps(
            {
                "cells": row,
                "quantity_decimal": quantity_decimal,
                "source_row_id": source_row_id,
                "source_table_index": table_number,
                "source_row_index": source_row_index,
                "quantity_raw": cell(row, "sl"),
                "quantity_review_decimal": record["quantity_review_decimal"],
                "bom_position": record["bom_position"],
                "review_status": "pending",
                "conflicts": conflicts,
                **({'source_row_bbox': bbox} if bbox is not None else {}),
            },
            ensure_ascii=False,
        )
        if record["ten_vat_tu"] or record["ma_hang"]:
            records.append(record)
    return records


def extract_bom_records_from_markdown(text: str) -> list[dict]:
    records = []
    for table_index, table in enumerate(extract_markdown_tables(text)):
        records.extend(extract_bom_records(table, table_idx=table_index))
    return records


def extract_assembly_steps(text: str, locator: SourceLocator, *, page=None) -> tuple[AssemblyStep, ...]:
    """Only explicit step markers establish order; never infer from filenames."""
    if page is not None:
        return _assembly_caption_regions(text, locator, page)
    pattern = re.compile(r"\b(?:Bước|Buoc|Step)\s+(\d+)\s*[:.)-]\s*", re.I)
    matches = list(pattern.finditer(str(text or "")))
    numbers = [int(match[1]) for match in matches]
    steps = []
    for index, match in enumerate(matches):
        number = int(match[1])
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.end():end].strip()
        conflicts = ("duplicate_step_number",) if numbers.count(number) > 1 else ()
        if number < 1 or not content:
            conflicts += ("invalid_step",)
        steps.append(AssemblyStep(number, content, locator, conflicts=conflicts,
                                  raw_text=text[match.start():end].strip()))
    return tuple(sorted(steps, key=lambda step: step.number))


def _page_text_runs(page):
    from pdfplumber.utils import cluster_objects

    runs = []
    for line in cluster_objects(page.extract_words(), 'top', 3):
        groups = [[]]
        for char in sorted(line, key=lambda char: char['x0']):
            previous = groups[-1][-1] if groups[-1] else None
            if previous and char['x0'] - previous['x1'] > max(char['height'], previous['height']):
                groups.append([])
            groups[-1].append(char)
        for chars in groups:
            if chars:
                runs.append((' '.join(c['text'] for c in chars), (
                    min(c['x0'] for c in chars), min(c['top'] for c in chars),
                    max(c['x1'] for c in chars), max(c['bottom'] for c in chars))))
    return tuple(runs)


def _assembly_caption_regions(text, locator, page):
    """Whitespace bounds candidates, not semantic completeness or approval."""
    fallback = extract_assembly_steps(text, locator)
    if not fallback:
        return ()
    try:
        runs = _page_text_runs(page)
    except Exception:
        return tuple(replace(step, conflicts=step.conflicts + ('caption_geometry_unavailable',)) for step in fallback)

    headings = [(value, bbox) for value, bbox in runs if extract_assembly_steps(value, locator)]
    steps = []
    for value, bbox in headings:
        caption, region = value, bbox
        # ponytail: whitespace adjacency is a candidate boundary; human review establishes completeness.
        while True:
            height = bbox[3] - bbox[1]
            nearby = [(line, box) for line, box in runs if (line, box) not in headings
                      and 0 <= box[1] - region[3] <= height
                      and box[3] > region[3]
                      and bbox[0] - height <= box[0] and box[2] <= bbox[2] + height]
            if len(nearby) != 1:
                break
            line, box = nearby[0]
            caption += '\n' + line
            region = (min(region[0], box[0]), region[1], max(region[2], box[2]), box[3])
        steps.extend(replace(step, conflicts=step.conflicts + ('caption_boundary_requires_review',))
                     for step in extract_assembly_steps(caption, replace(locator, bbox=region)))
    if sorted(step.number for step in steps) != sorted(step.number for step in fallback):
        return tuple(replace(step, conflicts=step.conflicts + ('caption_marker_geometry_mismatch',)) for step in fallback)
    numbers = [step.number for step in steps]
    return tuple(sorted((replace(step, conflicts=tuple(dict.fromkeys((*step.conflicts, 'duplicate_step_number'))))
                         if numbers.count(step.number) > 1 else step for step in steps), key=lambda step: step.number))


def extract_drawing_notes(text, locator, *, page, steps, snapshot_id, source_hash):
    """Propose instruction regions on assembly pages; a reviewer assigns their scope."""
    if not steps:
        return ()
    try:
        if any(not step.locator.bbox for step in steps):
            raise ValueError('Assembly caption geometry unavailable')
        runs = _page_text_runs(page)
        captions = tuple(step.locator.bbox for step in steps)
        outside = [(value, box) for value, box in runs if not any(
            box[0] >= cap[0] and box[1] >= cap[1] and box[2] <= cap[2] and box[3] <= cap[3]
            for cap in captions)]
        # shortcut: textual Vietnamese/English instructions only; add a vision parser when symbol-only notes are required.
        marker = re.compile(r'^(?:han|weld|xiet|tighten|lap|assemble|xo|insert|khoan|drill|cat|cut|mai|grind|deburr|hdcv)\b')
        starts = {i for i, (value, _) in enumerate(outside) if marker.search(remove_accents(value).lower().strip())}
        notes, consumed = [], set()
        for index in sorted(starts):
            value, box = outside[index]
            region = box
            height = box[3] - box[1]
            if height <= 0 or not all(math.isfinite(v) for v in box):
                raise ValueError('Invalid drawing-note geometry')
            while True:
                nearby = [(i, bounds) for i, (_, bounds) in enumerate(outside)
                    if i not in starts and i not in consumed and 0 <= bounds[1] - region[3] <= height
                    and bounds[3] > region[3]
                    and abs((bounds[0]+bounds[2]-box[0]-box[2])/2) <= max(bounds[2]-bounds[0],box[2]-box[0])/2]
                if len(nearby) != 1:
                    break
                candidate, bounds = nearby[0]
                consumed.add(candidate)
                region = (min(region[0],bounds[0]),region[1],max(region[2],bounds[2]),bounds[3])
            raw = page.crop(region).extract_text()
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError('Drawing-note geometry has no source text')
            position = len(notes)+1
            note_locator = replace(locator, bbox=region, row_key=f'page-{locator.page}:drawing-note-{position}')
            notes.append(TextFact(f'{locator.doc_id}:{locator.version}:{snapshot_id}:{note_locator.row_key}',
                'drawing_note', f'Note {position}', raw, raw.strip(), note_locator,
                conflicts=('drawing_note_boundary_requires_review','drawing_note_association_requires_review'),
                source_hash=source_hash, snapshot_id=snapshot_id, extraction_method='pdf_note_geometry'))
        return tuple(notes)
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError('Drawing-note geometry unavailable') from error


def reconcile_bom_records(records, vision_rows):
    """Keep text/vision candidates distinct for review; never repair a number silently."""
    result = []
    for record in records:
        code = str(record.get("ma_hang") or "").strip()
        position = str(record.get("bom_position") or "").strip()
        candidates = [dict(row) for row in (vision_rows or ()) if isinstance(row, dict)
                      and code and str(row.get("ma_hang") or row.get("ma") or row.get("code") or "").strip() == code
                      and position and str(row.get("stt") or row.get("position") or "").strip() == position]
        conflicts = list(record.get("conflicts") or ())
        for row in candidates:
            text_quantity, _ = parse_exact_decimal(json.loads(record["raw_row_json"]).get("quantity_raw", ""))
            for field in ("sl", "qty", "so_luong"):
                image_quantity, _ = parse_exact_decimal(str(row.get(field, "")))
                if image_quantity is not None and text_quantity is not None and image_quantity != text_quantity:
                    conflicts.append("quantity_extractor_disagreement")
            material = str(row.get("vat_lieu") or row.get("material") or "").strip().casefold()
            text_material = str(record.get("vat_lieu") or "").strip().casefold()
            if material and text_material and material != text_material:
                conflicts.append("material_extractor_disagreement")
        reviewed = {**record, "vision_candidates": candidates, "conflicts": list(dict.fromkeys(conflicts)), "review_status": "pending"}
        reviewed["raw_row_json"] = json.dumps({**json.loads(record["raw_row_json"]), "vision_candidates": candidates,
                                              "conflicts": reviewed["conflicts"]}, ensure_ascii=False)
        result.append(reviewed)
    return result


def bom_quantity_scope(records, *, doc_id, version, page, snapshot_id, source_hash, table_index):
    """Candidate BOM quantities with lexical values and occurrence source keys."""
    facts = []
    for record in records:
        payload = json.loads(record["raw_row_json"])
        raw = str(payload.get("quantity_raw", ""))
        value, numeric_conflicts = parse_exact_decimal(raw)
        conflicts = tuple(dict.fromkeys((*record.get("conflicts", ()), *numeric_conflicts)))
        row_key = f"page-{page}:{record['source_row_id']}"
        locator = _bom_locator(record, doc_id, version, page, table_index)
        facts.append(NumericFact(f"{doc_id}:{version}:{snapshot_id}:{row_key}", str(record.get("ma_hang") or record.get("ten_vat_tu") or "quantity"),
                                 raw, value, record.get("don_vi") or "", locator, conflicts=conflicts, source_hash=source_hash, extraction_method="pdf_table"))
    return ScopeManifest(f"{doc_id}:{version}:{snapshot_id}:page-{page}:table-{table_index}", snapshot_id, tuple(facts),
                         source_revisions=((str(doc_id), str(version)),))


def bom_text_facts(records, *, doc_id, version, page, snapshot_id, source_hash, table_index):
    facts = []
    for record in records:
        row_key = f"page-{page}:{record['source_row_id']}"
        locator = _bom_locator(record, doc_id, version, page, table_index)
        label = str(record.get('ma_hang') or record.get('ten_vat_tu') or 'BOM')
        for kind, raw in (('symbol', record.get('ma_hang', '')), ('material', record.get('vat_lieu', ''))):
            if not raw and (kind != 'material' or not re.search(r'inox\s*\d{3}', record.get('ten_vat_tu', ''), re.I)):
                continue
            conflicts = tuple(reason for reason in record.get('conflicts', ()) if reason.startswith('material')) if kind == 'material' else ()
            candidates = (f"Description: {record['ten_vat_tu']}",) if kind == 'material' else ()
            if kind == 'material':
                candidates += tuple(f"Image: {r.get('vat_lieu') or r.get('material')}" for r in record.get('vision_candidates', ()) if r.get('vat_lieu') or r.get('material'))
                if not raw:
                    conflicts += ('missing_material',)
            facts.append(TextFact(f'{doc_id}:{version}:{snapshot_id}:{row_key}:{kind}', kind, label, raw,
                                  raw or None, locator, conflicts=conflicts, candidates=candidates,
                                  source_hash=source_hash, extraction_method='pdf_table', snapshot_id=snapshot_id,
                                  bom_position=str(record.get('bom_position') or '')))
    return tuple(facts)


def _bom_locator(record, doc_id, version, page, table_index):
    return SourceLocator(str(doc_id), str(version), page=page,
                         row_key=f"page-{page}:{record['source_row_id']}", table_index=table_index,
                         bbox=_valid_bbox(record.get('source_row_bbox')))


def assembly_text_facts(steps, *, snapshot_id, source_hash):
    return tuple(TextFact(f'{step.locator.doc_id}:{step.locator.version}:{snapshot_id}:page-{step.locator.page}:step-{step.number}:candidate-{index}',
                          'assembly_step', f'Step {step.number}', step.raw_text, step.text, step.locator,
                          conflicts=step.conflicts, sequence_number=step.number, source_hash=source_hash,
                          extraction_method='pdf_caption_geometry' if step.locator.bbox else 'explicit_step_marker', snapshot_id=snapshot_id)
                 for index, step in enumerate(steps, 1))


__all__ = [
    "extract_bom_records",
    "extract_bom_records_from_markdown",
    "extract_markdown_tables",
]
