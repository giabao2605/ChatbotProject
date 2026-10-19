import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import StructuredEvidenceReview from "@/components/StructuredEvidenceReview.vue";
import DrawingBindingReview from "@/components/DrawingBindingReview.vue";

vi.mock("@/api/client", () => ({ apiGet: vi.fn(), apiSend: vi.fn() }));

it('submits a quantity target without reviewer-entered version or hash',async()=>{
  const wrapper=mount(StructuredEvidenceReview,{props:{docId:17}});await flushPromises();
  await wrapper.get('textarea[name="expected_rows"]').setValue('row-1');
  await wrapper.get('input[name="definition"]').setValue('Số lượng đơn hàng');
  await wrapper.get('input[name="quantity_target_enabled"]').setValue(true);
  await wrapper.get('input[name="quantity_root_doc_id"]').setValue('29');
  await wrapper.get('input[name="quantity_product_scope"]').setValue(' assembly-A ');
  await wrapper.get('input[name="quantity_site"]').setValue(' factory ');
  await wrapper.get('input[name="scope_checked"]').setValue(true);
  expect(wrapper.text()).toContain('một dòng đã duyệt với đơn vị piece');
  await wrapper.get('input[name="quantity_root_doc_id"]').setValue('0');
  expect(button(wrapper,'Xác nhận phạm vi đầy đủ').attributes('disabled')).toBeDefined();
  await wrapper.get('input[name="quantity_root_doc_id"]').setValue('29');
  await wrapper.get('textarea[name="expected_rows"]').setValue('row-1\nrow-2');
  expect(button(wrapper,'Xác nhận phạm vi đầy đủ').attributes('disabled')).toBeDefined();
  await wrapper.get('textarea[name="expected_rows"]').setValue('row-1');
  await wrapper.get('form').trigger('submit');await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/scopes/scope-1/review','POST',expect.objectContaining({
    quantity_target:{root_doc_id:29,product_scope:'assembly-A',site:'factory'}
  }));
  expect(wrapper.find('input[name="quantity_root_source_hash"]').exists()).toBe(false);
});

it('prefills a quantity mapping, removes it explicitly and resets it for another document',async()=>{
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if(path.endsWith('/scopes')) return {scopes:[{scope_id:'scope-1',snapshot_id:'snapshot-1',expected_row_ids:['row-1'],definition:'Số lượng',
      ...(path.includes('/17/')?{quantity_binding:{root_doc_id:29,root_version:3,root_source_hash:'a'.repeat(64),product_scope:'assembly-A',site:'factory'}}:{})}]} as never;
    return {items:[fact],offset:0,has_more:false} as never;
  });
  const wrapper=mount(StructuredEvidenceReview,{props:{docId:17}});await flushPromises();
  expect(wrapper.get<HTMLInputElement>('input[name="quantity_root_doc_id"]').element.value).toBe('29');
  expect(wrapper.get<HTMLInputElement>('input[name="quantity_product_scope"]').element.value).toBe('assembly-A');
  await wrapper.get('input[name="quantity_target_enabled"]').setValue(false);
  await wrapper.get('input[name="scope_checked"]').setValue(true);
  await wrapper.get('form').trigger('submit');await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith(expect.any(String),'POST',expect.objectContaining({quantity_target:null}));
  await wrapper.setProps({docId:18});await flushPromises();
  expect(wrapper.get<HTMLInputElement>('input[name="quantity_target_enabled"]').element.checked).toBe(false);
  expect(wrapper.find('input[name="quantity_root_doc_id"]').exists()).toBe(false);
});

const fact = {
  fact_id: "fact-1", label: "Số lượng", raw_value: "không rõ", value: null,
  unit: "pcs", currency: "", review_status: "pending", conflicts: ["Thiếu số lượng"],
  extraction_method: "pdf_table", locator: { doc_id: "17", version: "3", page: 2, row_key: "row-1", bbox: [1, 2, 3, 4] },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.apiGet).mockImplementation(async (path, params) => {
    if (path.endsWith("/assembly-sequence")) return { status: "unknown", snapshot_id: "snapshot-1" } as never;
    if (path.endsWith("/scopes")) return { scopes: [{ scope_id: "scope-1", snapshot_id: "snapshot-1", completeness_reviewed: false }] } as never;
    if (path.endsWith("/facts")) return { items: [fact], offset: Number(params?.offset ?? 0), limit: 50, has_more: Number(params?.offset ?? 0) === 0 } as never;
    throw new Error("Unexpected GET " + path);
  });
  vi.mocked(api.apiSend).mockResolvedValue({ ok: true } as never);
});

it('opens binding review only on explicit request and remounts it for a different document', async () => {
  const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 }, global: { stubs: { DrawingBindingReview: true } } });
  await flushPromises();
  expect(wrapper.findComponent(DrawingBindingReview).exists()).toBe(false);
  await wrapper.findAll('button').find(b => b.text() === 'Liên kết chi tiết với bản vẽ')!.trigger('click');
  expect(wrapper.getComponent(DrawingBindingReview).props('docId')).toBe(17);
  const first = wrapper.getComponent(DrawingBindingReview).vm;
  await wrapper.setProps({ docId: 18 }); await flushPromises();
  expect(wrapper.getComponent(DrawingBindingReview).props('docId')).toBe(18);
  expect(wrapper.getComponent(DrawingBindingReview).vm).not.toBe(first);
});

function button(wrapper: VueWrapper, text: string) {
  const found = wrapper.findAll("button").find((item) => item.text() === text);
  expect(found).toBeDefined();
  return found!;
}

it('shows original row context as text and clears it on the next page', async () => {
  vi.mocked(api.apiGet).mockImplementation(async (path, params) => {
    if (path.endsWith('/scopes')) return { scopes: [{ scope_id: 'scope-1', snapshot_id: 'snapshot-1' }] } as never;
    return { items: [{ ...fact, locator: { ...fact.locator, sheet: 'Ledger' }, source_row: params?.offset ? null : {
      headers: ['ID', 'Status'], cells: ['001', '<img src=x onerror=alert(1)>'], source_row: 2,
    } }], offset: Number(params?.offset ?? 0), has_more: !params?.offset } as never;
  });
  const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
  await flushPromises();
  expect(wrapper.get('[data-source="original-row"]').text()).toContain('001');
  expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>');
  expect(wrapper.find('img').exists()).toBe(false);
  expect(wrapper.text()).toContain('(chưa xác nhận)');
  await button(wrapper, 'Trang sau').trigger('click'); await flushPromises();
  expect(wrapper.find('[data-source="original-row"]').exists()).toBe(false);
  expect(wrapper.text()).toContain('Không đọc được dòng nguồn đúng phiên bản');
});

it.each([
  ['filter_scope_requires_review', '100', 'Tệp có khai báo bộ lọc. Đối chiếu toàn bộ dòng trước khi xác nhận phạm vi; hệ thống chưa tự áp bộ lọc.'],
  ['hidden_row', '100', 'Dòng nguồn đang bị ẩn. Xác nhận rõ giữ hay loại dòng này trong phạm vi.'],
  ['formula_requires_validation', '=SUM(B2:B3)', 'Ô chứa công thức. Kiểm tra kết quả từ nguồn và nhập giá trị đã xác minh; hệ thống không thực thi công thức.'],
])('explains Excel source warnings without approving or computing %s', async (conflict, raw, warning) => {
  vi.mocked(api.apiGet).mockImplementation(async (path) => {
    if (path.endsWith('/scopes')) return { scopes: [{ scope_id: 'scope-1', snapshot_id: 'snapshot-1' }] } as never;
    return { items: [{ ...fact, raw_value: raw, value: conflict === 'formula_requires_validation' ? null : '100',
      conflicts: [conflict], locator: { ...fact.locator, sheet: 'Ledger', cell: 'B2' } }], offset: 0, has_more: false } as never;
  });
  const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
  await flushPromises();
  expect(wrapper.text()).toContain(warning);
  expect(wrapper.text()).toContain(raw);
  await button(wrapper, 'Kiểm tra dòng').trigger('click');
  expect(wrapper.get<HTMLInputElement>('input[name="value_text"]').element.value).toBe('');
  expect(api.apiSend).not.toHaveBeenCalled();
});

it('shows pinned row warnings for existing facts without changing their saved conflicts', async () => {
  const existing = { ...fact, raw_value: '100', value: '100', conflicts: [], source_row: {
    headers: ['Amount'], cells: ['100'], source_row: 2, warnings: ['filter_scope_requires_review'],
  } };
  vi.mocked(api.apiGet).mockImplementation(async (path) => {
    if (path.endsWith('/scopes')) return { scopes: [{ scope_id: 'scope-1', snapshot_id: 'snapshot-1' }] } as never;
    return { items: [existing], offset: 0, has_more: false } as never;
  });
  const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
  await flushPromises();
  expect(wrapper.text()).toContain('Tệp có khai báo bộ lọc.');
  await button(wrapper, 'Kiểm tra dòng').trigger('click');
  expect(wrapper.get<HTMLInputElement>('input[name="value_text"]').element.value).toBe('');
  expect(existing.conflicts).toEqual([]);
  expect(api.apiSend).not.toHaveBeenCalled();
});

it('requires an entered value for a numeric cell with a source type conflict', async () => {
  vi.mocked(api.apiGet).mockImplementation(async (path) => {
    if (path.endsWith('/scopes')) return { scopes: [{ scope_id: 'scope-1', snapshot_id: 'snapshot-1', completeness_reviewed: false }] } as never;
    return { items: [{ ...fact, raw_value: '45292', value: '45292', unit: 'VND',
      conflicts: ['non_numeric_cell_type'], locator: { ...fact.locator, sheet: 'Ledger', cell: 'A2' } }], offset: 0, has_more: false } as never;
  });
  const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
  await flushPromises();
  await button(wrapper, 'Kiểm tra dòng').trigger('click');
  expect(wrapper.get<HTMLInputElement>('input[name="value_text"]').element.value).toBe('');
  expect(wrapper.text()).toContain('Ô có kiểu dữ liệu hoặc định dạng cần kiểm tra trước khi dùng làm số');
  expect(button(wrapper, 'Duyệt giá trị').attributes('disabled')).toBeDefined();
  expect(api.apiSend).not.toHaveBeenCalled();
  await wrapper.get('input[name="value_text"]').setValue('100');
  expect(button(wrapper, 'Duyệt giá trị').attributes('disabled')).toBeUndefined();
});

it('keeps the proposed exact value when the source has no conflict', async () => {
  vi.mocked(api.apiGet).mockImplementation(async (path) => {
    if (path.endsWith('/scopes')) return { scopes: [{ scope_id: 'scope-1', snapshot_id: 'snapshot-1', completeness_reviewed: false }] } as never;
    return { items: [{ ...fact, raw_value: '100.01', value: '100.01', conflicts: [] }], offset: 0, has_more: false } as never;
  });
  const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
  await flushPromises();
  await button(wrapper, 'Kiểm tra dòng').trigger('click');
  expect(wrapper.get<HTMLInputElement>('input[name="value_text"]').element.value).toBe('100.01');
  expect(button(wrapper, 'Duyệt giá trị').attributes('disabled')).toBeUndefined();
});

describe("StructuredEvidenceReview", () => {
  it('restores explicit exclusions, retains raw facts and sends the reviewed partition', async () => {
    vi.mocked(api.apiGet).mockResolvedValueOnce({ scopes: [{ scope_id: 'scope-1', snapshot_id: 'snapshot-1',
      expected_row_ids: ['detail'], definition: 'Chi tiết', excluded_row_reasons: { 'row-1': 'Dòng tổng' } }] } as never);
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    expect(wrapper.get<HTMLInputElement>('input[name="excluded_row_0"]').element.value).toBe('row-1');
    expect(wrapper.get<HTMLTextAreaElement>('textarea[name="excluded_reason_0"]').element.value).toBe('Dòng tổng');
    expect(wrapper.get('tbody').text()).toContain('row-1');
    await button(wrapper, 'Trang sau').trigger('click');
    await flushPromises();
    await wrapper.get('input[name="scope_checked"]').setValue(true);
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(api.apiSend).toHaveBeenCalledWith(expect.any(String), 'POST', expect.objectContaining({
      expected_row_ids: ['detail'], excluded_row_reasons: { 'row-1': 'Dòng tổng' },
    }));
  });

  it.each(['missing-id', 'missing-reason', 'duplicate', 'overlap'])('blocks invalid exclusion: %s', async (scenario) => {
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    await wrapper.get('textarea[name="expected_rows"]').setValue('detail');
    await wrapper.get('input[name="definition"]').setValue('Chi tiết');
    await wrapper.get('input[name="scope_checked"]').setValue(true);
    await button(wrapper, 'Thêm dòng loại trừ').trigger('click');
    await wrapper.get('input[name="excluded_row_0"]').setValue(scenario === 'missing-id' ? ' ' : scenario === 'overlap' ? 'detail' : 'total');
    await wrapper.get('textarea[name="excluded_reason_0"]').setValue(scenario === 'missing-reason' ? ' ' : 'Dòng tổng');
    if (scenario === 'duplicate') {
      await button(wrapper, 'Thêm dòng loại trừ').trigger('click');
      await wrapper.get('input[name="excluded_row_1"]').setValue(' total ');
      await wrapper.get('textarea[name="excluded_reason_1"]').setValue('Dòng tổng');
    }
    await wrapper.get('form').trigger('submit');
    expect(api.apiSend).not.toHaveBeenCalled();
    expect(wrapper.get('[role="alert"]').text()).toBeTruthy();
    await button(wrapper, 'Bỏ dòng loại trừ 1').trigger('click');
    expect(wrapper.findAll('input[name^="excluded_row_"]').length).toBe(scenario === 'duplicate' ? 1 : 0);
  });

  it.each(['missing-hash', 'wrong-document', 'with-facts'])('does not infer an empty scope from %s', async (scenario) => {
    vi.mocked(api.apiGet).mockImplementation(async (path) => {
      if (path.endsWith('/scopes')) return { scopes: [{ scope_id: 'candidate', snapshot_id: 'export',
        completeness_reviewed: false, unit: 'VND', currency: 'VND',
        empty_source_locator: { doc_id: scenario==='wrong-document' ? '18' : '17', version: '3', sheet: 'CSV', cell: 'B1', row_key: '' },
        empty_source_hash: scenario==='missing-hash' ? '' : 'a'.repeat(64), empty_source_extraction_method: 'csv_header_only' }] } as never;
      return { items: scenario==='with-facts' ? [fact] : [], offset: 0, has_more: false } as never;
    });
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    await wrapper.get('input[name="definition"]').setValue('Phạm vi đang đối soát');
    await wrapper.get('input[name="scope_checked"]').setValue(true);
    expect(wrapper.text()).not.toContain('Nguồn khai báo không có dòng số liệu');
    expect(button(wrapper, 'Xác nhận phạm vi đầy đủ').attributes('disabled')).toBeDefined();
    expect(api.apiSend).not.toHaveBeenCalled();
  });

  it.each(['string', 'integer'])('reviews a source-backed empty candidate with %s identity only after explicit unit and empty-source attestation', async (identity) => {
    vi.mocked(api.apiGet).mockImplementation(async (path) => {
      if (path.endsWith('/scopes')) return { scopes: [{ scope_id: 'empty', snapshot_id: 'empty-export',
        completeness_reviewed: false, unit: 'VND', currency: 'VND',
        empty_source_locator: { doc_id: identity==='integer' ? 17 : '17', version: identity==='integer' ? 3 : '3', sheet: 'CSV', cell: 'B1', row_key: '' },
        empty_source_hash: 'a'.repeat(64), empty_source_extraction_method: 'csv_header_only' }] } as never;
      return { items: [], offset: 0, has_more: false } as never;
    });
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    expect(wrapper.text()).toContain('Nguồn khai báo không có dòng số liệu');
    expect(wrapper.get('a[data-source="empty-original"]').attributes('href')).toBe('/api/files/documents/17/original');
    expect(button(wrapper, 'Xác nhận phạm vi đầy đủ').attributes('disabled')).toBeDefined();
    await wrapper.get('input[name="definition"]').setValue('Chi phí kỳ đã đối soát');
    await wrapper.get('textarea[name="scope_note"]').setValue('Đã mở tệp, đối soát kỳ và xác nhận không có dòng');
    await wrapper.get('input[name="scope_checked"]').setValue(true);
    expect(button(wrapper, 'Xác nhận phạm vi đầy đủ').attributes('disabled')).toBeUndefined();
    await wrapper.get('input[name="scope_unit"]').setValue('');
    expect(button(wrapper, 'Xác nhận phạm vi đầy đủ').attributes('disabled')).toBeDefined();
    await wrapper.get('input[name="scope_unit"]').setValue('VND');
    await wrapper.get('form').trigger('submit');
    await flushPromises();
    expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/scopes/empty/review', 'POST', {
      expected_row_ids: [], rounding_scale: 0, rounding_mode: 'ROUND_HALF_UP',
      definition: 'Chi phí kỳ đã đối soát', period: '', note: 'Đã mở tệp, đối soát kỳ và xác nhận không có dòng',
      unit: 'VND', currency: 'VND',
    });
  });

  it('opens technical field review in the existing document review flow', async () => {
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    vi.mocked(api.apiGet).mockResolvedValueOnce({ items: [], offset: 0, has_more: false } as never);
    await button(wrapper, 'Vật liệu, ký hiệu và bước lắp ráp').trigger('click');
    await flushPromises();
    expect(api.apiGet).toHaveBeenCalledWith('/api/rag-evidence/documents/17/text-facts', { offset: 0, limit: 50 });
    expect(wrapper.text()).toContain('Chưa có trường kỹ thuật để duyệt');
  });
  it("shows source, conflicts and pending state; paginates without treating a page as complete", async () => {
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    expect(wrapper.text()).toContain("không rõ");
    expect(wrapper.text()).toContain("Thiếu số lượng");
    expect(wrapper.text()).toContain("Chờ duyệt");
    expect(wrapper.text()).toContain("row-1");
    expect(wrapper.find('a[data-source="page"]').attributes("href")).toBe("/api/files/documents/17/pages/2");
    await button(wrapper, "Trang sau").trigger("click");
    await flushPromises();
    expect(api.apiGet).toHaveBeenLastCalledWith("/api/rag-evidence/scopes/scope-1/facts", { offset: 50, limit: 50 });
    expect(wrapper.get<HTMLTextAreaElement>('textarea[name="expected_rows"]').element.value).toBe("");
  });

  it("submits corrected normalized values without changing raw source or impersonating reviewer", async () => {
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    await button(wrapper, "Kiểm tra dòng").trigger("click");
    await wrapper.get('input[name="value_text"]').setValue("19");
    await wrapper.get('input[name="unit"]').setValue("pcs");
    await wrapper.get('textarea[name="fact_note"]').setValue("Đối chiếu bảng kê trang 2");
    await wrapper.findAll("form")[0].trigger("submit");
    await flushPromises();
    expect(api.apiSend).toHaveBeenCalledWith("/api/rag-evidence/facts/fact-1/review", "POST", {
      action: "approve", value_text: "19", unit: "pcs", currency: "", note: "Đối chiếu bảng kê trang 2",
    });
    expect(fact.raw_value).toBe("không rõ");
  });

  it("requires explicit whole-scope row manifest and attestation before completeness approval", async () => {
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    expect(button(wrapper, "Xác nhận phạm vi đầy đủ").attributes("disabled")).toBeDefined();
    await wrapper.get('textarea[name="expected_rows"]').setValue("row-1\nrow-2");
    await wrapper.get('input[name="scope_checked"]').setValue(true);
    await wrapper.get('input[name="definition"]').setValue("Số lượng chi tiết");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    expect(api.apiSend).toHaveBeenCalledWith("/api/rag-evidence/scopes/scope-1/review", "POST", {
      expected_row_ids: ["row-1", "row-2"], rounding_scale: 0, rounding_mode: "ROUND_HALF_UP", definition: "Số lượng chi tiết", period: "", note: "",
    });
  });

  it("rejects a missing value without submitting an invented numeric correction", async () => {
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    await button(wrapper, "Kiểm tra dòng").trigger("click");
    await wrapper.get('textarea[name="fact_note"]').setValue("Không đủ chứng cứ số lượng");
    await button(wrapper, "Từ chối giá trị").trigger("click");
    await flushPromises();
    expect(api.apiSend).toHaveBeenCalledWith("/api/rag-evidence/facts/fact-1/review", "POST", {
      action: "reject", note: "Không đủ chứng cứ số lượng",
    });
  });

  it("shows backend rejection and does not report false success", async () => {
    vi.mocked(api.apiSend).mockResolvedValue({ ok: false, error: "Dòng chưa được duyệt" } as never);
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    await button(wrapper, "Kiểm tra dòng").trigger("click");
    await button(wrapper, "Từ chối giá trị").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("Dòng chưa được duyệt");
    expect(wrapper.text()).not.toContain("Đã lưu");
  });

  it("recovers from a load error and never applies stale scopes after document changes", async () => {
    let resolveOld: (value: unknown) => void = () => {};
    vi.mocked(api.apiGet).mockImplementation(async (path) => {
      if (path.includes("/17/")) return await new Promise<unknown>((resolve) => { resolveOld = resolve; }) as never;
      return { scopes: [] } as never;
    });
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await wrapper.setProps({ docId: 18 });
    await flushPromises();
    resolveOld({ scopes: [{ scope_id: "stale", snapshot_id: "old" }] });
    await flushPromises();
    expect(wrapper.text()).not.toContain("stale");
    expect(api.apiGet).not.toHaveBeenCalledWith(expect.stringContaining("stale"), expect.anything());
    vi.mocked(api.apiGet).mockRejectedValueOnce(new Error("Không có quyền nguồn"));
    await button(wrapper, "Tải lại").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("Không có quyền nguồn");
    await button(wrapper, "Tải lại").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Tài liệu chưa có phạm vi dữ liệu");
    wrapper.unmount();
  });

  it("blocks duplicate manifest rows and invalid rounding instead of silently changing them", async () => {
    const wrapper = mount(StructuredEvidenceReview, { props: { docId: 17 } });
    await flushPromises();
    await wrapper.get('textarea[name="expected_rows"]').setValue("row-1\nrow-1");
    await wrapper.get('input[name="scope_checked"]').setValue(true);
    await wrapper.get('input[name="definition"]').setValue("Số lượng");
    await wrapper.get("form").trigger("submit");
    expect(wrapper.get('[role="alert"]').text()).toContain("mã dòng trùng");
    await wrapper.get('textarea[name="expected_rows"]').setValue("row-1");
    await wrapper.get('input[name="rounding_scale"]').setValue("-1");
    await wrapper.get("form").trigger("submit");
    expect(wrapper.get('[role="alert"]').text()).toContain("số nguyên từ 0 đến 12");
    expect(api.apiSend).not.toHaveBeenCalled();
  });
});
