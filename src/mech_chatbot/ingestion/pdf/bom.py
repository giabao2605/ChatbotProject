# -*- coding: utf-8 -*-
"""Structured BOM extraction shared by PDF and Markdown ingestion."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re

from mech_chatbot.ingestion.pdf.config import remove_accents


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
    if value in {"row id", "source row", "source row id"}:
        return "row_id"
    if value in {"quantity", "qty", "value", "gia tri", "so luong", "sl"}:
        return "sl"
    if value in {"unit", "don vi", "dvt"}:
        return "unit"
    if value in {"material", "material code", "vat lieu", "ma vat lieu"}:
        return "vat_lieu"
    if value in {"description", "ten", "ten vat tu", "ten hang", "ten goi", "mo ta"}:
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


def extract_bom_records(table, table_idx=None):
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
        }
        record["confidence"] = 0.9 if record["ma_hang"] and quantity_decimal is not None else 0.5
        record["raw_row_json"] = json.dumps(
            {
                "cells": row,
                "quantity_decimal": quantity_decimal,
                "source_row_id": source_row_id,
                "source_table_index": table_number,
                "source_row_index": source_row_index,
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


__all__ = [
    "extract_bom_records",
    "extract_bom_records_from_markdown",
    "extract_markdown_tables",
]
