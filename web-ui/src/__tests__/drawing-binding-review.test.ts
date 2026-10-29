import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import * as api from '@/api/client';
import DrawingBindingReview from '@/components/DrawingBindingReview.vue';

vi.mock('@/api/client', () => ({ apiGet: vi.fn(), apiSend: vi.fn() }));
const documents = [
  { DocID: 17, OriginalFileName: 'Parent.pdf', VersionNo: 3, Site: 'factory' },
  { DocID: 18, OriginalFileName: 'Child.pdf', VersionNo: 2, Site: 'factory' },
  { DocID: 19, OriginalFileName: 'Other site.pdf', VersionNo: 1, Site: 'elsewhere' },
  { DocID: 20, OriginalFileName: 'Invalid revision.pdf', VersionNo: 0, Site: 'factory' },
];
const binding = { BindingID: 51, PartKey: 'part:m8', DocID: 18, VersionNo: 2,
  SourceDocID: 17, SourceVersion: 3, SourcePage: 4, SourceQuote: 'M8 detail',
  SourceHash: 'a'.repeat(64), SnapshotID: 'snapshot', ReviewStatus: 'pending' };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.apiGet).mockImplementation(async (path, params) => path === '/api/documents'
    ? { documents } as never : { items: [binding], offset: Number(params?.offset || 0), has_more: !params?.offset } as never);
  vi.mocked(api.apiSend).mockResolvedValue({ ok: true, review_status: 'pending' } as never);
});
function button(w: VueWrapper, text: string) { return w.findAll('button').find(b => b.text() === text)!; }
async function loaded() {
  const w = mount(DrawingBindingReview, { props: { docId: 17 } });
  await flushPromises();
  await w.get('[name="product_scope"]').setValue('Chelsea');
  await button(w, 'Tải liên kết').trigger('click');
  await flushPromises();
  return w;
}
it('proposes an explicit binding pending review, preserving exact evidence and omitting client pins', async () => {
  const w = await loaded();
  expect(w.text()).toContain('Parent.pdf');
  expect(w.get('[name="target_doc"]').text()).not.toContain('Other site');
  expect(w.get('[name="target_doc"]').text()).not.toContain('Invalid revision');
  await w.get('[name="part_code"]').setValue(' M8 ');
  await w.get('[name="target_doc"]').setValue('18');
  await w.get('[name="source_page"]').setValue('4');
  await w.get('[name="source_quote"]').setValue(' M8 detail ');
  await w.get('[name="proposal_note"]').setValue('Đối chiếu nguồn');
  await w.get('form[data-form="proposal"]').trigger('submit');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/graph/binding', 'POST', {
    product_scope: 'Chelsea', site: 'factory', note: 'Đối chiếu nguồn',
    payload: { part_key: 'part:m8', doc_id: 18, version_no: 2, source_doc_id: 17, source_version: 3, source_page: 4, source_quote: ' M8 detail ' },
  });
  expect(w.text()).toContain('Đã gửi đề xuất chờ duyệt');
  expect(api.apiSend).toHaveBeenCalledTimes(1);
});
it('requires separate explicit review and shows stale-proof failure without false acceptance, allowing retry', async () => {
  const w = await loaded();
  await button(w, 'Kiểm tra liên kết').trigger('click');
  await w.get('[name="binding_review_note"]').setValue('Đã đối chiếu');
  await w.get('form[data-form="review"]').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  await w.get('[name="binding_checked"]').setValue(true);
  vi.mocked(api.apiSend).mockResolvedValueOnce({ ok: false, reason: 'invalid_provenance' } as never);
  await w.get('form[data-form="review"]').trigger('submit');
  await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain('Cần tạo đề xuất mới');
  expect(w.text()).not.toContain('Đã lưu quyết định');
  expect(w.find('form[data-form="review"]').exists()).toBe(true);
  await w.get('form[data-form="review"]').trigger('submit');
  await flushPromises();
  expect(api.apiSend).toHaveBeenLastCalledWith('/api/rag-evidence/graph/binding/51/review', 'POST', {
    action: 'approve', product_scope: 'Chelsea', site: 'factory', note: 'Đã đối chiếu',
  });
  expect(w.text()).toContain('Đã lưu quyết định');
  expect(w.find('form[data-form="review"]').exists()).toBe(false);
});
it('paginates explicitly and rejects a pending binding with the same source attestation', async () => {
  const w = await loaded();
  await button(w, 'Trang liên kết sau').trigger('click');
  await flushPromises();
  expect(api.apiGet).toHaveBeenLastCalledWith('/api/rag-evidence/documents/17/drawing-bindings', {
    product_scope: 'Chelsea', site: 'factory', status: 'pending', offset: 50, limit: 50,
  });
  await button(w, 'Kiểm tra liên kết').trigger('click');
  await w.get('[name="binding_review_note"]').setValue('Mã không khớp');
  await w.get('[name="binding_checked"]').setValue(true);
  await button(w, 'Từ chối liên kết').trigger('click');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/graph/binding/51/review', 'POST', expect.objectContaining({ action: 'reject' }));
});
it('requires a new proposal for a legacy binding without source pins', async () => {
  vi.mocked(api.apiGet).mockImplementation(async path => path === '/api/documents'
    ? { documents } as never : { items: [{ ...binding, SourceHash: null, SnapshotID: null }], offset: 0, has_more: false } as never);
  const w = await loaded();
  await button(w, 'Kiểm tra liên kết').trigger('click');
  await w.get('[name="binding_review_note"]').setValue('Không có snapshot');
  await w.get('[name="binding_checked"]').setValue(true);
  expect(button(w, 'Duyệt liên kết').attributes('disabled')).toBeDefined();
  await w.get('form[data-form="review"]').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  expect(w.text()).toContain('Cần tạo đề xuất mới');
});
it.each(['product_scope', 'binding_status'])('changing %s invalidates selected rows before any write', async field => {
  const w = await loaded();
  await button(w, 'Kiểm tra liên kết').trigger('click');
  await w.get(`[name="${field}"]`).setValue(field === 'product_scope' ? 'Other product' : 'approved');
  expect(w.find('form[data-form="review"]').exists()).toBe(false);
  await w.get('form[data-form="proposal"]').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  expect(w.text()).not.toContain('part:m8');
});
it('discards old list and catalog responses after the source document changes', async () => {
  const w = await loaded();
  let finish: (data: unknown) => void = () => {};
  vi.mocked(api.apiGet).mockImplementationOnce(async () => await new Promise<unknown>(resolve => { finish = resolve; }) as never);
  await button(w, 'Trang liên kết sau').trigger('click');
  await w.setProps({ docId: 18 });
  await flushPromises();
  finish({ items: [{ ...binding, PartKey: 'part:OLD' }], offset: 50, has_more: false });
  await flushPromises();
  expect(w.text()).not.toContain('part:OLD');
  expect(w.get('[name="product_scope"]').element).toHaveProperty('value', '');
  vi.mocked(api.apiGet).mockImplementationOnce(async () => await new Promise<unknown>(resolve => { finish = resolve; }) as never);
  await w.setProps({ docId: 19 });
  await w.setProps({ docId: 17 });
  await flushPromises();
  finish({ documents: [] });
  await flushPromises();
  expect(w.text()).toContain('Parent.pdf');
  w.unmount();
});
it.each([[], documents.map(d => d.DocID === 17 ? { ...d, VersionNo: 0 } : d), documents.map(d => d.DocID === 17 ? { ...d, Site: '' } : d)].map(docs => ({ docs })))('disables mutations when source is not valid in the effective catalog', async ({ docs }) => {
  vi.mocked(api.apiGet).mockResolvedValue({ documents: docs } as never);
  const w = mount(DrawingBindingReview, { props: { docId: 17 } });
  await flushPromises();
  expect(w.text()).toContain('Cần tài liệu nguồn đã xuất bản');
  expect(button(w, 'Tải liên kết').attributes('disabled')).toBeDefined();
  await w.get('form[data-form="proposal"]').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
});
it('does not post an old success notice when its reload finishes after changing document', async () => {
  const w = await loaded();
  await button(w, 'Kiểm tra liên kết').trigger('click');
  await w.get('[name="binding_review_note"]').setValue('Đã đối chiếu');
  await w.get('[name="binding_checked"]').setValue(true);
  let finish: (data: unknown) => void = () => {};
  vi.mocked(api.apiGet).mockImplementationOnce(async () => await new Promise<unknown>(resolve => { finish = resolve; }) as never);
  await w.get('form[data-form="review"]').trigger('submit');
  await flushPromises();
  await w.setProps({ docId: 18 }); await flushPromises();
  finish({ items: [binding], offset: 0, has_more: false }); await flushPromises();
  expect(w.text()).not.toContain('Đã lưu quyết định');
});
it('switches an approved filter to pending only after creating a new proposal', async () => {
  const w = await loaded();
  await w.get('[name="binding_status"]').setValue('approved');
  await button(w, 'Tải liên kết').trigger('click'); await flushPromises();
  await w.get('[name="part_code"]').setValue('M8');
  await w.get('[name="target_doc"]').setValue('18');
  await w.get('[name="source_quote"]').setValue('M8 detail');
  await w.get('[name="proposal_note"]').setValue('Đề xuất mới');
  await w.get('form[data-form="proposal"]').trigger('submit'); await flushPromises();
  expect(api.apiGet).toHaveBeenLastCalledWith('/api/rag-evidence/documents/17/drawing-bindings', expect.objectContaining({ status: 'pending', product_scope: 'Chelsea' }));
  expect(w.text()).toContain('Đã gửi đề xuất chờ duyệt');
  expect(w.get('fieldset').attributes('disabled')).toBeUndefined();
});
it('does not let completion of an old save repopulate the new document', async () => {
  const w = await loaded();
  await button(w, 'Kiểm tra liên kết').trigger('click');
  await w.get('[name="binding_review_note"]').setValue('Đã đối chiếu');
  await w.get('[name="binding_checked"]').setValue(true);
  let finish: (data: unknown) => void = () => {};
  vi.mocked(api.apiSend).mockImplementationOnce(async () => await new Promise<unknown>(resolve => { finish = resolve; }) as never);
  await w.get('form[data-form="review"]').trigger('submit');
  await w.setProps({ docId: 18 });
  await flushPromises();
  finish({ ok: true }); await flushPromises();
  expect(w.text()).not.toContain('Đã lưu quyết định');
  expect(w.text()).not.toContain('part:m8');
});
it('recovers catalog and list failures with explicit reload and never enables writes from a failed list', async () => {
  vi.mocked(api.apiGet).mockRejectedValueOnce(new Error('Mất kết nối danh mục'));
  const w = mount(DrawingBindingReview, { props: { docId: 17 } }); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain('Mất kết nối danh mục');
  await button(w, 'Tải lại danh mục bản vẽ').trigger('click'); await flushPromises();
  await w.get('[name="product_scope"]').setValue('Chelsea');
  vi.mocked(api.apiGet).mockRejectedValueOnce(new Error('Mất kết nối liên kết'));
  await button(w, 'Tải liên kết').trigger('click'); await flushPromises();
  expect(w.get('fieldset').attributes('disabled')).toBeDefined();
  await button(w, 'Tải liên kết').trigger('click'); await flushPromises();
  expect(w.get('fieldset').attributes('disabled')).toBeUndefined();
});
it('rejects incomplete proposal inputs and displays backend proposal failure without success', async () => {
  const w = await loaded();
  await w.get('form[data-form="proposal"]').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  expect(w.get('[role="alert"]').text()).toContain('Cần chọn bản vẽ');
  await w.get('[name="part_code"]').setValue('M8');
  await w.get('[name="target_doc"]').setValue('18');
  await w.get('[name="source_quote"]').setValue('M8 detail');
  await w.get('[name="proposal_note"]').setValue('Đề xuất');
  vi.mocked(api.apiSend).mockResolvedValueOnce({ ok: false, reason: 'invalid_provenance' } as never);
  await w.get('form[data-form="proposal"]').trigger('submit'); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain('Cần tạo đề xuất mới');
  expect(w.text()).not.toContain('Đã gửi đề xuất');
  vi.mocked(api.apiSend).mockRejectedValueOnce(new Error('Lỗi kết nối'));
  await w.get('form[data-form="proposal"]').trigger('submit'); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain('Lỗi kết nối');
});
it('reports an already reviewed binding and a transport failure without clearing the decision', async () => {
  const w = await loaded();
  await button(w, 'Kiểm tra liên kết').trigger('click');
  await w.get('[name="binding_review_note"]').setValue('Đã đối chiếu');
  await w.get('[name="binding_checked"]').setValue(true);
  vi.mocked(api.apiSend).mockResolvedValueOnce({ ok: false, reason: 'already_reviewed' } as never);
  await button(w, 'Từ chối liên kết').trigger('click'); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain('đã được người khác xử lý');
  vi.mocked(api.apiSend).mockRejectedValueOnce(new Error('Mất kết nối'));
  await button(w, 'Từ chối liên kết').trigger('click'); await flushPromises();
  expect(w.get('[role="alert"]').text()).toContain('Mất kết nối');
  expect(w.find('form[data-form="review"]').exists()).toBe(true);
});
