"""Lossless CSV/XLSX evidence; extraction never grants approval or completeness."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import csv
import hashlib
from io import StringIO
from pathlib import Path
import posixpath
import re
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format

from mech_chatbot.domain.structured_evidence import NumericFact, ScopeManifest, SourceLocator
from mech_chatbot.ingestion.pdf.config import remove_accents


@dataclass(frozen=True)
class StructuredRow:
    row_id: str
    cells: tuple[str, ...]
    source_row: int


@dataclass(frozen=True)
class StructuredTable:
    name: str
    headers: tuple[str, ...]
    rows: tuple[StructuredRow, ...]
    scopes: tuple[ScopeManifest, ...]


@dataclass(frozen=True)
class StructuredTableDocument:
    tables: tuple[StructuredTable, ...]
    source_hash: str


def parse_exact_decimal(raw: str, *, canonical: bool = False):
    """Return Decimal and conflict codes without guessing a 3-digit separator."""
    token = str(raw).strip()
    if not token:
        return None, ("missing_value",)
    if canonical:
        valid = re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?", token)
    else:
        valid = re.fullmatch(r"[+-]?\d+(?:[.,]\d+)*", token)
    if not valid:
        return None, ("invalid_numeric_value",)
    if not canonical and "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        group = "." if decimal == "," else ","
        left, right = token.rsplit(decimal, 1)
        if not re.fullmatch(r"[+-]?\d{1,3}(?:" + re.escape(group) + r"\d{3})+", left):
            return None, ("invalid_numeric_value",)
        token = left.replace(group, "") + "." + right
    elif not canonical and ("," in token or "." in token):
        separator = "," if "," in token else "."
        if token.count(separator) != 1 or len(token.rsplit(separator, 1)[1]) == 3:
            return None, ("ambiguous_decimal_locale",)
        token = token.replace(separator, ".")
    try:
        value = Decimal(token)
    except InvalidOperation:
        return None, ("invalid_numeric_value",)
    return value, ()


def _column_letter(index):
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _column_index(cell):
    match = re.fullmatch(r"([A-Z]+)([1-9]\d*)", cell)
    if not match:
        raise ValueError("Invalid XLSX cell reference")
    value = 0
    for char in match[1]:
        value = value * 26 + ord(char) - 64
    row = int(match[2])
    if value > 16384 or row > 1048576:
        raise ValueError("Invalid XLSX cell dimensions")
    return value, row


def _numeric_column(header, cells):
    normalized = remove_accents(header.casefold()).strip()
    if re.search(r"\b(code|id|ma|stt|date|ngay|revision|phien ban)\b", normalized):
        return False
    if re.search(r"\b(amount|quantity|qty|value|price|total|so luong|gia tri|don gia|thanh tien|sl|weight|length|khoi luong)\b", normalized):
        return True
    nonempty = [str(cell).strip() for cell in cells if str(cell).strip()]
    return bool(nonempty) and all(parse_exact_decimal(cell)[0] is not None for cell in nonempty)


def _empty_table_source(name, header, header_row, width, index, flags, canonical_cells,
                        *, doc_id, version, source_hash, table_index, extraction_method):
    cells = tuple(f'{_column_letter(i+1)}{header_row}' for i in range(width))
    if any(flags.get(cell) or cell in canonical_cells for cell in cells) or header.lstrip().startswith('='):
        return None
    return {'empty_source_locator': SourceLocator(doc_id, version, table_index=table_index, sheet=name,
                cell=f'{_column_letter(index+1)}{header_row}', row_key=f'table-{table_index}-header-{header_row}'),
            'empty_source_hash': source_hash, 'empty_source_extraction_method': extraction_method}


def _row_currency_conflicts(row, columns, flags, currency):
    if not currency or not columns:
        return ()
    if len(columns) != 1:
        return ('ambiguous_currency_column',)
    index = columns[0]
    if flags.get(f'{_column_letter(index + 1)}{row.source_row}'):
        return ('unverified_row_currency',)
    token = row.cells[index].strip().upper()
    if not token:
        return ('missing_row_currency',)
    if not re.fullmatch(r'[A-Z]{3}', token):
        return ('invalid_row_currency',)
    return ('currency_conflict',) if token != currency else ()


def _row_transaction_conflicts(row, columns, flags):
    if not columns:
        return ()
    if len(columns) != 1:
        return ('ambiguous_transaction_identifier',)
    index = columns[0]
    if flags.get(f'{_column_letter(index + 1)}{row.source_row}'):
        return ('unverified_transaction_identifier',)
    return () if row.cells[index].strip() else ('missing_transaction_identifier',)


def _build_table(name, source_rows, flags, canonical_cells, *, doc_id, version, snapshot_id, source_hash, table_index, extraction_method):
    if not source_rows:
        return StructuredTable(name, (), (), ())
    header_row, headers = source_rows[0]
    width = len(headers)
    if width * len(source_rows) > MAX_WORKBOOK_CELLS:
        raise ValueError("Structured table exceeds table cell limit; export a smaller reviewed range")
    if any(len(cells) > width for _, cells in source_rows[1:]):
        raise ValueError("Table row has more columns than its header")
    rows = tuple(StructuredRow(f"table-{table_index}-row-{number}", tuple(cells) + ("",) * (width - len(cells)), number)
                 for number, cells in source_rows[1:])
    # Unmapped export schemas still require source review; never infer exchange rates.
    currency_columns = tuple(i for i, header in enumerate(headers)
        if ' '.join(remove_accents(header.casefold()).replace('_', ' ').split())
        in {'currency', 'currency code', 'tien te', 'ma tien te', 'loai tien'})
    transaction_columns = tuple(i for i, header in enumerate(headers)
        if ' '.join(remove_accents(header.casefold()).replace('_', ' ').replace('-', ' ').split())
        in {'transaction id', 'transaction code', 'ma giao dich'})
    scopes = []
    for index, header in enumerate(headers):
        values = [row.cells[index] if index < len(row.cells) else "" for row in rows]
        if index in currency_columns or index in transaction_columns or not _numeric_column(header, values):
            continue
        unit_match = re.search(r"\[([^\]]+)\]", header)
        unit = unit_match[1].strip() if unit_match else ""
        currency = unit if unit in {"VND", "USD", "EUR", "JPY", "GBP", "CNY"} else ""
        empty = {}
        if not rows:
            empty = _empty_table_source(name, header, header_row, width, index, flags, canonical_cells,
                doc_id=doc_id, version=version, source_hash=source_hash, table_index=table_index, extraction_method=extraction_method)
            if empty is None:
                continue
        facts = []
        for row in rows:
            coordinate = f"{_column_letter(index + 1)}{row.source_row}"
            raw = row.cells[index] if index < len(row.cells) else ""
            conflicts = flags.get(coordinate, ())
            conflicts += _row_currency_conflicts(row, currency_columns, flags, currency)
            conflicts += _row_transaction_conflicts(row, transaction_columns, flags)
            if "formula_requires_validation" in conflicts:
                value = None
            else:
                value, parsed_conflicts = parse_exact_decimal(raw, canonical=coordinate in canonical_cells)
                conflicts += parsed_conflicts
            locator = SourceLocator(doc_id, version, row_key=row.row_id, table_index=table_index, sheet=name, cell=coordinate)
            facts.append(NumericFact(f"{doc_id}:{version}:{snapshot_id}:{name}:{coordinate}", header, raw, value, unit, locator,
                                     conflicts=conflicts, source_hash=source_hash, extraction_method=extraction_method, currency=currency))
        scopes.append(ScopeManifest(f"{doc_id}:{version}:{snapshot_id}:{name}:column-{index + 1}", snapshot_id, tuple(facts),
                                    source_revisions=((doc_id, version),), unit=unit, currency=currency, **empty))
    return StructuredTable(name, tuple(headers), rows, tuple(scopes))


_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MAX_EXPANDED_WORKBOOK_BYTES = 100 * 1024 * 1024
MAX_WORKBOOK_CELLS = 1_000_000


def _validate_archive(archive):
    members = archive.infolist()
    if sum(member.file_size for member in members) > MAX_EXPANDED_WORKBOOK_BYTES:
        raise ValueError("XLSX exceeds expanded workbook limit; export a smaller reviewed range")
    if len({member.filename for member in members}) != len(members):
        raise ValueError("Duplicate XLSX archive member")


def validate_workbook_archive(path):
    """Bound expansion before either XML evidence or the legacy text reader."""
    with ZipFile(path) as archive:
        _validate_archive(archive)


def _xlsx_date_styles(archive):
    if 'xl/styles.xml' not in archive.namelist():
        return (False,)
    styles = ET.fromstring(archive.read('xl/styles.xml'))
    custom = {int(item.attrib['numFmtId']): item.attrib['formatCode']
              for item in styles.findall('s:numFmts/s:numFmt', _NS)}
    def date_style(item):
        number = int(item.attrib.get('numFmtId', '0'))
        if number in custom:
            # Literal semicolons/date letters must not change the three numeric sections.
            code = re.sub(r'"(?:[^"\\]|\\.)*"|\\.|_.|\*.', '', custom[number])
            return any(is_date_format(section) for section in code.split(';', 3)[:3])
        if 27 <= number <= 36 or 50 <= number <= 58:
            # OOXML localized date/time builtins are absent from openpyxl's format map.
            return True
        if number in BUILTIN_FORMATS:
            return is_date_format(BUILTIN_FORMATS[number])
        raise ValueError('Unresolved XLSX number format')
    return tuple(date_style(item) for item in styles.findall('s:cellXfs/s:xf', _NS))


def _xlsx_tables(path):
    with ZipFile(path) as archive:
        _validate_archive(archive)
        date_styles = _xlsx_date_styles(archive)
        cell_count = 0
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", _NS)]
        relationships = {item.attrib["Id"]: item.attrib["Target"]
                         for item in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.findall("s:sheets/s:sheet", _NS):
            relationship = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = relationships[relationship]
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            if not member.startswith("xl/") or ".." in member.split("/"):
                raise ValueError("Invalid XLSX sheet target")
            xml = ET.fromstring(archive.read(member))
            rows, flags, canonical = [], {}, set()
            for row in xml.findall("s:sheetData/s:row", _NS):
                number = int(row.attrib["r"])
                values = {}
                for cell in row.findall("s:c", _NS):
                    coordinate = cell.attrib["r"]
                    column, source_row = _column_index(coordinate)
                    if source_row != number:
                        raise ValueError("XLSX row/cell source mismatch")
                    if column in values:
                        raise ValueError("Duplicate XLSX cell reference")
                    raw = cell.findtext("s:v", default="", namespaces=_NS)
                    kind = cell.attrib.get("t", "n")
                    style = cell.attrib.get('s')
                    if style is not None and (not re.fullmatch(r'\d+', style) or int(style) >= len(date_styles)):
                        raise ValueError('Invalid XLSX cell style')
                    date_style = date_styles[int(style or '0')] if date_styles else False
                    conflicts = ("hidden_row",) if row.attrib.get("hidden") == "1" else ()
                    if cell.find("s:f", _NS) is not None:
                        raw = "=" + cell.findtext("s:f", default="", namespaces=_NS)
                        conflicts += ("formula_requires_validation",)
                    elif kind == "s":
                        raw = shared[int(raw)]
                    elif kind == "inlineStr":
                        raw = "".join(cell.find("s:is", _NS).itertext())
                    elif kind == "n":
                        canonical.add(coordinate)
                        if date_style:
                            conflicts += ('non_numeric_cell_type',)
                    elif kind in {"b", "e", "d"}:
                        conflicts += ("non_numeric_cell_type",)
                    values[column] = raw
                    flags[coordinate] = conflicts
                if values:
                    cell_count += max(values)
                    if cell_count > MAX_WORKBOOK_CELLS:
                        raise ValueError("XLSX exceeds workbook cell limit; export a smaller reviewed range")
                    rows.append((number, tuple(values.get(index, "") for index in range(1, max(values) + 1))))
            for merged in xml.findall("s:mergeCells/s:mergeCell", _NS):
                first, last = merged.attrib["ref"].split(":")
                x1, y1 = _column_index(first)
                x2, y2 = _column_index(last)
                if x2 < x1 or y2 < y1:
                    raise ValueError("Invalid XLSX merged range")
                cell_count += (x2 - x1 + 1) * (y2 - y1 + 1)
                if cell_count > MAX_WORKBOOK_CELLS:
                    raise ValueError("XLSX exceeds workbook cell limit; export a smaller reviewed range")
                for source_row in range(y1, y2 + 1):
                    for column in range(x1, x2 + 1):
                        coordinate = f"{_column_letter(column)}{source_row}"
                        flags[coordinate] = flags.get(coordinate, ()) + ("merged_cell_requires_review",)
            yield sheet.attrib["name"], rows, flags, canonical


def extract_structured_table_file(file_path, *, doc_id, version, snapshot_id=""):
    """Extract candidate scopes while preserving the legacy text reader separately."""
    path = Path(file_path)
    with path.open('rb') as source:
        source_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    ext = path.suffix.lower()
    if ext in {".csv", ".tsv"}:
        from mech_chatbot.ingestion.pdf.readers import _read_text_file
        rows = list(csv.reader(StringIO(_read_text_file(path)), delimiter="\t" if ext == ".tsv" else ","))
        width = len(rows[0]) if rows else 0
        if any(len(row) > width for row in rows):
            raise ValueError("CSV row has more columns than its header")
        source_tables = [("CSV", [(number, tuple(row)) for number, row in enumerate(rows, 1)], {}, set())]
    elif ext in {".xlsx", ".xlsm"}:
        source_tables = _xlsx_tables(path)
    else:
        raise ValueError("Structured exact extraction requires CSV, TSV, XLSX or XLSM; export legacy XLS first")
    tables = tuple(_build_table(name, rows, flags, canonical, doc_id=doc_id, version=version,
                               snapshot_id=snapshot_id, source_hash=source_hash, table_index=index,
                               extraction_method='xlsx_xml' if ext in {'.xlsx','.xlsm'} else 'csv')
                   for index, (name, rows, flags, canonical) in enumerate(source_tables, 1))
    return StructuredTableDocument(tables, source_hash)
