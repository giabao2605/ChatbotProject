"""Filter declarations remain review evidence, never execute spreadsheet rules."""
from decimal import Decimal
from html import escape
from zipfile import ZipFile

import pytest

from mech_chatbot.ingestion.pdf.structured import extract_structured_table_file
from .test_structured_evidence_repository import PROFILE, store
from .test_structured_evidence_ingestion import write_workbook

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RNS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PNS = 'http://schemas.openxmlformats.org/package/2006/relationships'
ACTIVE = '<autoFilter ref="A1:A3"><filterColumn colId="0"><filters><filter val="keep"/></filters></filterColumn></autoFilter>'


def workbook(tmp_path, *, sheet_filter='', table_filter=None, target='../tables/table1.xml',
             mode='', hidden='0', formula=False, merged='', relationship_ns=PNS, table_type='table',
             part_ids=('table1',), extra_relationships=''):
    path = tmp_path / 'filtered.xlsx'
    last = '<f>SUM(B2)</f><v>999</v>' if formula else '<v>20</v>'
    parts = ('<tableParts>' + ''.join(f'<tablePart r:id="{identity}"/>' for identity in part_ids)
             + '</tableParts>') if table_filter is not None else ''
    with ZipFile(path, 'w') as archive:
        archive.writestr('xl/workbook.xml', f'<workbook xmlns="{NS}" xmlns:r="{RNS}"><sheets><sheet name="Ledger" sheetId="1" r:id="sheet1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', f'<Relationships xmlns="{PNS}"><Relationship Id="sheet1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{NS}" xmlns:r="{RNS}"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>status</t></is></c><c r="B1" t="inlineStr"><is><t>amount [VND]</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>keep</t></is></c><c r="B2"><v>10</v></c></row>'
            f'<row r="3" hidden="{hidden}"><c r="A3" t="inlineStr"><is><t>exclude</t></is></c><c r="B3">{last}</c></row>'
            '<row r="4"><c r="A4" t="inlineStr"><is><t>outside</t></is></c><c r="B4"><v>30</v></c></row>'
            f'</sheetData>{sheet_filter}{merged}{parts}</worksheet>')
        if table_filter is not None:
            archive.writestr('xl/worksheets/_rels/sheet1.xml.rels', f'<Relationships xmlns="{relationship_ns}"><Relationship Id="table1" Type="{RNS}/{table_type}" Target="{escape(target, quote=True)}"{mode}/>{extra_relationships}</Relationships>')
            archive.writestr('xl/tables/table1.xml', f'<table xmlns="{NS}" ref="A1:A3">{table_filter}</table>')
    return path


@pytest.mark.parametrize('kind', ['sheet', 'table'])
def test_active_filter_marks_numeric_rows_even_outside_filtered_columns_and_keeps_all_rows(tmp_path, kind):
    path = workbook(tmp_path, **({'sheet_filter': ACTIVE} if kind == 'sheet' else {'table_filter': ACTIVE}))
    table = extract_structured_table_file(path, doc_id='1', version='2').tables[0]
    facts = table.scopes[0].facts
    assert [row.source_row for row in table.rows] == [2, 3, 4]
    assert [fact.value for fact in facts] == [Decimal('10'), Decimal('20'), Decimal('30')]
    assert [fact.locator.cell for fact in facts] == ['B2', 'B3', 'B4']
    assert [fact.conflicts for fact in facts] == [('filter_scope_requires_review',), ('filter_scope_requires_review',), ()]


@pytest.mark.parametrize('kind', ['sheet', 'table'])
@pytest.mark.parametrize('declaration', ['', '<autoFilter ref="A1:A3"/>',
    '<autoFilter ref="A1:A3"><sortState ref="A2:A3"><sortCondition ref="A2:A3"/></sortState></autoFilter>'])
def test_dropdown_or_sort_only_does_not_claim_an_active_filter(tmp_path, kind, declaration):
    path = workbook(tmp_path, **({'sheet_filter': declaration} if kind == 'sheet' else {'table_filter': declaration}))
    facts = extract_structured_table_file(path, doc_id='1', version='2').tables[0].scopes[0].facts
    assert all(fact.conflicts == () for fact in facts)


@pytest.mark.parametrize('hidden', ['1', 'true'])
def test_filter_preserves_hidden_formula_and_merge_flags_without_using_cached_value(tmp_path, hidden):
    path = workbook(tmp_path, sheet_filter=ACTIVE, hidden=hidden, formula=True,
        merged='<mergeCells><mergeCell ref="B2:B3"/></mergeCells>')
    facts = extract_structured_table_file(path, doc_id='1', version='2').tables[0].scopes[0].facts
    assert facts[1].raw_value == '=SUM(B2)' and facts[1].value is None
    assert set(facts[1].conflicts) == {'hidden_row', 'formula_requires_validation',
        'merged_cell_requires_review', 'filter_scope_requires_review'}


@pytest.mark.parametrize('target,mode', [('https://example.invalid/table.xml', ' TargetMode="External"'),
    ('../../../outside.xml', ''), ('../tables/../../outside.xml', ''),
    ('..\\tables\\table1.xml', ''), ('../tables/missing.xml', '')])
def test_untrusted_or_missing_table_targets_fail_closed(tmp_path, target, mode):
    path = workbook(tmp_path, table_filter=ACTIVE, target=target, mode=mode)
    with pytest.raises(ValueError, match='XLSX'):
        extract_structured_table_file(path, doc_id='1', version='2')


@pytest.mark.parametrize('kind', ['sheet', 'table'])
@pytest.mark.parametrize('ref', ['A0:A3', 'A3:A1', 'A1:XFE3', 'A1:A1048577', 'A1,A3', ''])
def test_malformed_filter_ranges_fail_closed(tmp_path, kind, ref):
    declaration = ACTIVE.replace('A1:A3', ref)
    path = workbook(tmp_path, **({'sheet_filter': declaration} if kind == 'sheet' else {'table_filter': declaration}))
    with pytest.raises(ValueError, match='XLSX'):
        extract_structured_table_file(path, doc_id='1', version='2')


@pytest.mark.parametrize('relationship_ns,table_type', [('invalid', 'table'), (PNS, 'externalLink')])
def test_table_reference_requires_package_namespace_and_table_relationship(tmp_path, relationship_ns, table_type):
    path = workbook(tmp_path, table_filter=ACTIVE, relationship_ns=relationship_ns, table_type=table_type)
    with pytest.raises(ValueError, match='XLSX'):
        extract_structured_table_file(path, doc_id='1', version='2')


def test_filter_metadata_does_not_invalidate_transaction_or_currency_and_explicit_review_works(tmp_path, store):
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact
    path = tmp_path / 'filtered-ledger.xlsx'
    write_workbook(path, f'<worksheet xmlns="{NS}"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Transaction ID</t></is></c>'
        '<c r="B1" t="inlineStr"><is><t>Currency</t></is></c><c r="C1" t="inlineStr"><is><t>Amount [VND]</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>TX-1</t></is></c>'
        '<c r="B2" t="inlineStr"><is><t>VND</t></is></c><c r="C2"><v>10</v></c></row>'
        '</sheetData>' + ACTIVE.replace('A1:A3', 'A1:C2') + '</worksheet>')
    scope = extract_structured_table_file(path, doc_id='1', version='2', snapshot_id='filtered-ledger').tables[0].scopes[0]
    fact = scope.facts[0]
    assert fact.conflicts == ('filter_scope_requires_review',)
    save_extracted_scope(scope, db_engine=store)
    with pytest.raises(ValueError, match='Resolve conflicts'):
        review_numeric_fact(fact.fact_id, PROFILE, action='approve', note='Fixture source review', db_engine=store)
    reviewed = review_numeric_fact(fact.fact_id, PROFILE, action='approve', value_text='10',
        note='Fixture reviewer verified transaction, currency and selected rows', db_engine=store)
    assert reviewed['review_status'] == 'approved' and reviewed['conflicts'] == []


@pytest.mark.parametrize('duplicate', ['part_id', 'target', 'relationship_id'])
def test_duplicate_table_references_fail_before_reading_table_xml(tmp_path, monkeypatch, duplicate):
    extra = f'<Relationship Id="table2" Type="{RNS}/table" Target="../tables/./table1.xml"/>'
    ids = ('table1', 'table2')
    if duplicate == 'part_id': ids, extra = ('table1', 'table1'), ''
    elif duplicate == 'relationship_id': extra = extra.replace('table2', 'table1')
    path = workbook(tmp_path, table_filter=ACTIVE, part_ids=ids, extra_relationships=extra)
    read = ZipFile.read
    def guarded(archive, member, *args, **kwargs):
        assert member != 'xl/tables/table1.xml', 'Duplicate table reference reached XML parsing'
        return read(archive, member, *args, **kwargs)
    monkeypatch.setattr(ZipFile, 'read', guarded)
    with pytest.raises(ValueError, match='XLSX'):
        extract_structured_table_file(path, doc_id='1', version='2')
