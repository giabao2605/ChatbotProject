from decimal import Decimal
from html import escape
from zipfile import ZipFile

import pytest

from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact
from mech_chatbot.ingestion.pdf.structured import extract_structured_table_file
from .test_structured_evidence_repository import PROFILE, store


NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def workbook(tmp_path, *, styles=None, style='1', raw='45292', header_style=''):
    path = tmp_path / 'cell-types.xlsx'
    attribute = '' if style is None else f' s="{style}"'
    with ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Ledger" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{NS}"><sheetData><row r="1"><c r="A1" t="inlineStr"{header_style}><is><t>amount [VND]</t></is></c></row><row r="2"><c r="A2"{attribute}><v>{raw}</v></c></row></sheetData></worksheet>')
        if styles is not None:
            archive.writestr('xl/styles.xml', styles)
    return path


def formats(number_format=14, custom=''):
    return f'<styleSheet xmlns="{NS}">{custom}<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="{number_format}"/></cellXfs></styleSheet>'


@pytest.mark.parametrize('number_format,custom,raw', [
    (14, '', '45292'),
    (20, '', '0.5'),
    (27, '', '45292'),
    (50, '', '0.5'),
    (164, '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>', '45292'),
    (165, '<numFmts count="1"><numFmt numFmtId="165" formatCode="hh:mm:ss"/></numFmts>', '0.5'),
])
def test_date_time_numeric_cells_require_explicit_review_resolution(tmp_path, store, number_format, custom, raw):
    path = workbook(tmp_path, styles=formats(number_format, custom), raw=raw)
    scope = extract_structured_table_file(path, doc_id='1', version='2', snapshot_id='typed').tables[0].scopes[0]
    fact = scope.facts[0]
    save_extracted_scope(scope, db_engine=store)
    with pytest.raises(ValueError, match='Resolve conflicts'):
        review_numeric_fact(fact.fact_id, PROFILE, action='approve', note='Synthetic ordinary review', db_engine=store)
    assert fact.raw_value == raw and fact.value == Decimal(raw)
    assert fact.conflicts == ('non_numeric_cell_type',) and fact.review_status == 'pending'
    assert fact.locator.cell == 'A2' and fact.locator.sheet == 'Ledger'


@pytest.mark.parametrize('style,styles', [
    (None, None),
    ('0', None),
    ('0', formats()),
    ('1', formats(2)),
    ('1', formats(164, '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0000"/></numFmts>')),
    ('1', formats(27, '<numFmts count="1"><numFmt numFmtId="27" formatCode="0.0000"/></numFmts>')),
])
def test_ordinary_numbers_keep_exact_decimal_and_no_styles_compatibility(tmp_path, style, styles):
    raw = '9007199254740993.0001'
    path = workbook(tmp_path, style=style, styles=styles, raw=raw)
    fact = extract_structured_table_file(path, doc_id='1', version='2').tables[0].scopes[0].facts[0]
    assert fact.raw_value == raw and fact.value == Decimal(raw) and fact.conflicts == ()


def test_date_style_does_not_change_text_header_or_implicit_general_cell(tmp_path):
    path = workbook(tmp_path, styles=formats(), style=None, header_style=' s="1"', raw='100')
    table = extract_structured_table_file(path, doc_id='1', version='2').tables[0]
    assert table.headers == ('amount [VND]',)
    assert table.scopes[0].facts[0].value == Decimal('100')
    assert table.scopes[0].facts[0].conflicts == ()


@pytest.mark.parametrize('style,styles', [('-1', formats()), ('2', formats()), ('bad', formats()), ('1', None)])
def test_invalid_explicit_style_reference_fails_closed(tmp_path, style, styles):
    path = workbook(tmp_path, styles=styles, style=style)
    with pytest.raises(ValueError, match='Invalid XLSX cell style'):
        extract_structured_table_file(path, doc_id='1', version='2')


def test_missing_custom_number_format_fails_closed(tmp_path):
    path = workbook(tmp_path, styles=formats(164))
    with pytest.raises(ValueError, match='Unresolved XLSX number format'):
        extract_structured_table_file(path, doc_id='1', version='2')


@pytest.mark.parametrize('format_code', ['[<=999]0;yyyy-mm-dd', '0;0;yyyy-mm-dd;@'])
def test_later_numeric_date_sections_block_ordinary_amount_approval(tmp_path, store, format_code):
    custom = f'<numFmts><numFmt numFmtId="164" formatCode="{escape(format_code, quote=True)}"/></numFmts>'
    path = workbook(tmp_path, styles=formats(164, custom))
    scope = extract_structured_table_file(path, doc_id='1', version='2', snapshot_id='mixed-format').tables[0].scopes[0]
    fact = scope.facts[0]
    save_extracted_scope(scope, db_engine=store)
    with pytest.raises(ValueError, match='Resolve conflicts'):
        review_numeric_fact(fact.fact_id, PROFILE, action='approve', note='Synthetic mixed-format review', db_engine=store)
    assert fact.raw_value == '45292' and fact.value == Decimal('45292')
    assert fact.conflicts == ('non_numeric_cell_type',)


@pytest.mark.parametrize('format_code', [
    '0";yyyy-mm-dd";0;0;@',
    r'0\;0;0;0;yyyy-mm-dd',
    '0;0;0;yyyy-mm-dd',
    r'0\d_\*m;0;0;@',
    '0_d*m;0;0;@',
])
def test_literal_semicolons_date_letters_and_text_section_are_not_numeric_dates(tmp_path, format_code):
    custom = f'<numFmts><numFmt numFmtId="164" formatCode="{escape(format_code, quote=True)}"/></numFmts>'
    raw = '9007199254740993.0001'
    path = workbook(tmp_path, styles=formats(164, custom), raw=raw)
    fact = extract_structured_table_file(path, doc_id='1', version='2').tables[0].scopes[0].facts[0]
    assert fact.raw_value == raw and fact.value == Decimal(raw) and fact.conflicts == ()
