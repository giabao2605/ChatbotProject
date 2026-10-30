import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import * as api from '@/api/client';
import TechnicalFieldReview from '@/components/TechnicalFieldReview.vue';

vi.mock('@/api/client', () => ({ apiGet: vi.fn(), apiSend: vi.fn() }));
const material = { fact_id: 'material-1', kind: 'material', label: 'Part A', raw_value: 'Inox 304',
  value: 'Inox 304', review_status: 'pending', conflicts: ['material_conflict'],
  candidates: ['Description: Inox 201'], bom_position: '7',
  locator: { doc_id: '17', version: '3', page: 2, row_key: 'table-1-row-7' } };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.apiGet).mockResolvedValue({ items: [material], offset: 0, has_more: false } as never);
  vi.mocked(api.apiSend).mockResolvedValue({ ok: true } as never);
});

it.each([
  ['caption_boundary_requires_review', 'Cần đối chiếu vùng chú thích để xác nhận đầy đủ nội dung bước'],
  ['caption_geometry_unavailable', 'Chưa xác định được vùng chú thích; cần kiểm tra văn bản với trang nguồn'],
  ['caption_marker_geometry_mismatch', 'Số bước trong vùng chú thích chưa khớp văn bản; cần đối chiếu nguồn'],
])('explains pending caption evidence %s before any approval', async (conflict, message) => {
  vi.mocked(api.apiGet).mockResolvedValue({ items: [{ ...material, kind: 'assembly_step',
    sequence_number: 1, conflicts: [conflict] }], offset: 0, has_more: false } as never);
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  expect(wrapper.text()).toContain(message);
  expect(api.apiSend).not.toHaveBeenCalled();
});

it('lets an approver correct material while preserving raw evidence and source links', async () => {
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  expect(wrapper.text()).toContain('Inox 304');
  expect(wrapper.text()).toContain('Description: Inox 201');
  expect(wrapper.text()).toContain('Vật liệu chưa thống nhất');
  expect(wrapper.find('a[data-source="page"]').attributes('href')).toBe('/api/files/documents/17/pages/2');
  await wrapper.findAll('button').find(b => b.text() === 'Kiểm tra trường')!.trigger('click');
  await wrapper.get('textarea[name="technical_value"]').setValue('Inox 201');
  await wrapper.get('textarea[name="technical_note"]').setValue('Đối chiếu bảng và chú thích');
  await wrapper.get('form').trigger('submit');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/text-facts/material-1/review', 'POST', {
    action: 'approve', value_text: 'Inox 201', note: 'Đối chiếu bảng và chú thích',
  });
  expect(material.raw_value).toBe('Inox 304');
});

it('requires source-check notes and rejects without inventing a normalized value', async () => {
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === 'Kiểm tra trường')!.trigger('click');
  await wrapper.get('form').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  expect(wrapper.get('[role="alert"]').text()).toContain('Cần ghi chú');
  await wrapper.get('textarea[name="technical_note"]').setValue('Không đủ căn cứ');
  await wrapper.findAll('button').find(b => b.text() === 'Từ chối trường')!.trigger('click');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/text-facts/material-1/review', 'POST', {
    action: 'reject', note: 'Không đủ căn cứ',
  });
});

it('reviews an explicit assembly step number and shows backend failure without false success', async () => {
  vi.mocked(api.apiGet).mockResolvedValue({ items: [{ ...material, kind: 'assembly_step', sequence_number: 3 }], offset: 0, has_more: false } as never);
  vi.mocked(api.apiSend).mockResolvedValue({ ok: false, error: 'Nguồn đã đổi phiên bản' } as never);
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === 'Kiểm tra trường')!.trigger('click');
  await wrapper.get('input[name="technical_step"]').setValue(2);
  await wrapper.get('textarea[name="technical_note"]').setValue('Đối chiếu bước');
  await wrapper.get('form').trigger('submit');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/text-facts/material-1/review', 'POST', expect.objectContaining({ sequence_number: 2 }));
  expect(wrapper.get('[role="alert"]').text()).toContain('Nguồn đã đổi phiên bản');
  expect(wrapper.text()).not.toContain('Đã lưu quyết định');
});

it('paginates technical sources and recovers from load errors', async () => {
  vi.mocked(api.apiGet).mockResolvedValueOnce({ items: [material], offset: 0, has_more: true } as never);
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  vi.mocked(api.apiGet).mockRejectedValueOnce(new Error('Chưa tải được nguồn'));
  await wrapper.findAll('button').find(b => b.text() === 'Trường sau')!.trigger('click');
  await flushPromises();
  expect(api.apiGet).toHaveBeenCalledWith('/api/rag-evidence/documents/17/text-facts', { offset: 50, limit: 50 });
  expect(wrapper.get('[role="alert"]').text()).toContain('Chưa tải được nguồn');
  await wrapper.findAll('button').find(b => b.text() === 'Tải lại trường kỹ thuật')!.trigger('click');
  await flushPromises();
  expect(wrapper.find('[role="alert"]').exists()).toBe(false);
});

it('discards responses from a document that is no longer selected', async () => {
  let finish: (value: unknown) => void = () => {};
  vi.mocked(api.apiGet).mockImplementationOnce(async () => await new Promise<unknown>(resolve => { finish = resolve; }) as never);
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await wrapper.setProps({ docId: 18 });
  await flushPromises();
  finish({ items: [{ ...material, raw_value: 'OLD restricted document' }], offset: 0, has_more: false });
  await flushPromises();
  expect(wrapper.text()).not.toContain('OLD restricted document');
  wrapper.unmount();
});


it('requires a manual whole-sequence declaration and submits the displayed source snapshot', async () => {
  vi.mocked(api.apiGet).mockImplementation(async path => path.endsWith('/assembly-sequence')
    ? { status: 'unknown', snapshot_id: 'snapshot-17', expected_step_numbers: [], definition: '' } as never
    : { items: [{ ...material, kind: 'assembly_step', sequence_number: 3 }], offset: 0, has_more: true } as never);
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  expect(wrapper.get('textarea[name="sequence_numbers"]').element).toHaveProperty('value', '');
  await wrapper.get('form[data-sequence-review]').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  await wrapper.get('textarea[name="sequence_numbers"]').setValue('1, 2, 3, 4, 5, 6');
  await wrapper.get('textarea[name="sequence_definition"]').setValue('Toàn bộ sáu bước trong bản vẽ');
  await wrapper.get('textarea[name="sequence_note"]').setValue('Đã đối chiếu toàn bộ bản vẽ');
  await wrapper.get('form[data-sequence-review]').trigger('submit');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/documents/17/assembly-sequence/review', 'POST', {
    snapshot_id: 'snapshot-17', expected_step_numbers: [1,2,3,4,5,6],
    definition: 'Toàn bộ sáu bước trong bản vẽ', note: 'Đã đối chiếu toàn bộ bản vẽ',
  });
});


it('keeps failed sequence approval unresolved and rejects duplicated manual step numbers', async () => {
  vi.mocked(api.apiGet).mockImplementation(async path => path.endsWith('/assembly-sequence')
    ? { status: 'unknown', snapshot_id: 'snapshot-17' } as never
    : { items: [material], offset: 0, has_more: false } as never);
  vi.mocked(api.apiSend).mockResolvedValue({ ok: false, error: 'Có bước chưa được duyệt' } as never);
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  await wrapper.get('textarea[name="sequence_numbers"]').setValue('1, 1');
  await wrapper.get('textarea[name="sequence_definition"]').setValue('Toàn bộ bản vẽ');
  await wrapper.get('textarea[name="sequence_note"]').setValue('Đã đối chiếu');
  await wrapper.get('form[data-sequence-review]').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  await wrapper.get('textarea[name="sequence_numbers"]').setValue('1, 2');
  await wrapper.get('form[data-sequence-review]').trigger('submit');
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain('Có bước chưa được duyệt');
  expect(wrapper.text()).toContain('Chưa xác nhận đầy đủ');
  expect(wrapper.text()).not.toContain('Đã lưu quyết định xác nhận');
});

const drawingNote = { ...material, fact_id: 'note-1', kind: 'drawing_note', label: 'Ghi chú hàn',
  raw_value: 'Hàn đầy', value: 'Hàn đầy', conflicts: ['drawing_note_association_requires_review'],
  linked_step_fact_id: null as string | null, locator: { ...material.locator, bbox: [10, 20, 30, 40] } };
const stepOptions = [
  { fact_id: 'step-6', sequence_number: 6, label: 'Xiết chân', review_status: 'approved' },
  { fact_id: 'step-pending', sequence_number: 7, label: 'Chưa đối chiếu', review_status: 'pending' },
];
function noteResponse(note = drawingNote, options: unknown = stepOptions) {
  vi.mocked(api.apiGet).mockImplementation(async path => path.endsWith('/assembly-sequence')
    ? { status: 'unknown', snapshot_id: 'snapshot-17', ...(options === undefined ? {} : { step_options: options }) } as never
    : { items: [note], offset: 0, has_more: true } as never);
}
async function editNote() {
  const wrapper = mount(TechnicalFieldReview, { props: { docId: 17 } });
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === 'Kiểm tra trường')!.trigger('click');
  await wrapper.get('textarea[name="technical_note"]').setValue('Đã đối chiếu chú thích với trang nguồn');
  return wrapper;
}

it('requires an explicit drawing-note association, without guessing a step or document note', async () => {
  noteResponse();
  const wrapper = await editNote();
  expect(wrapper.text()).toContain('Chú thích bản vẽ');
  expect(wrapper.text()).toContain('Chưa gắn bước');
  expect(wrapper.text()).toContain('Cần đối chiếu chú thích và chọn bước liên quan, dòng bảng kê hoặc ghi chú chung');
  expect(wrapper.text()).toContain('Vùng: 10, 20, 30, 40');
  expect(wrapper.get('select[name="technical_note_step"]').element).toHaveProperty('value', '');
  await wrapper.get('form').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  expect(wrapper.get('[role="alert"]').text()).toContain('Cần chọn bước đã duyệt hoặc ghi chú chung');
});

it.each([['document', null], ['step:step-6', 'step-6']])('submits the explicitly selected drawing-note scope %s', async (choice, linkedStep) => {
  noteResponse();
  const wrapper = await editNote();
  const select = wrapper.get('select[name="technical_note_step"]');
  expect(select.text()).toContain('Bước 6 — Xiết chân');
  expect(wrapper.findAll('tbody tr')).toHaveLength(1);
  expect(select.get('option[value="step:step-pending"]').attributes('disabled')).toBeDefined();
  await select.setValue(choice);
  await wrapper.get('form').trigger('submit');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/text-facts/note-1/review', 'POST', {
    action: 'approve', value_text: 'Hàn đầy', note: 'Đã đối chiếu chú thích với trang nguồn', linked_step_fact_id: linkedStep, linked_bom_fact_id: null,
  });
  expect(wrapper.find('select[name="technical_note_step"]').exists()).toBe(false);
});

it.each(['step:step-pending', 'step:unknown'])('does not submit an unapproved or unknown step %s', async choice => {
  noteResponse();
  const wrapper = await editNote();
  await wrapper.get('select[name="technical_note_step"]').setValue(choice);
  await wrapper.get('form').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
  expect(wrapper.get('[role="alert"]').text()).toContain('Cần chọn bước đã duyệt hoặc ghi chú chung');
});

it.each([['step-6', 'step:step-6'], [null, 'document'], ['missing-step', '']])('prefills only known approved drawing-note associations %s', async (link, choice) => {
  noteResponse({ ...drawingNote, review_status: 'approved', linked_step_fact_id: link });
  const wrapper = await editNote();
  expect(wrapper.get('select[name="technical_note_step"]').element).toHaveProperty('value', choice);
  expect(api.apiSend).not.toHaveBeenCalled();
});

it('supports an older sequence response and rejects notes without sending an association', async () => {
  // The old API response has no step_options.
  vi.mocked(api.apiGet).mockImplementation(async path => path.endsWith('/assembly-sequence')
    ? { status: 'unknown' } as never : { items: [drawingNote], offset: 0, has_more: false } as never);
  const wrapper = await editNote();
  expect(wrapper.get('select[name="technical_note_step"]').findAll('option')).toHaveLength(2);
  await wrapper.findAll('button').find(b => b.text() === 'Từ chối trường')!.trigger('click');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/text-facts/note-1/review', 'POST', {
    action: 'reject', note: 'Đã đối chiếu chú thích với trang nguồn',
  });
});

it('keeps a failed note review unresolved and removes choices when a document changes or loading fails', async () => {
  noteResponse();
  vi.mocked(api.apiSend).mockResolvedValue({ ok: false, error: 'Bước đã đổi hoặc chưa được duyệt' } as never);
  const wrapper = await editNote();
  await wrapper.get('select[name="technical_note_step"]').setValue('step:step-6');
  await wrapper.get('form').trigger('submit');
  await flushPromises();
  expect(wrapper.get('[role="alert"]').text()).toContain('Bước đã đổi');
  expect(wrapper.text()).not.toContain('Đã lưu quyết định');
  vi.mocked(api.apiGet).mockRejectedValue(new Error('Không tải được nguồn mới'));
  await wrapper.setProps({ docId: 18 });
  await flushPromises();
  expect(wrapper.find('select[name="technical_note_step"]').exists()).toBe(false);
  expect(wrapper.text()).not.toContain('Xiết chân');
  noteResponse({ ...drawingNote, fact_id: 'note-2' }, []);
  await wrapper.findAll('button').find(b => b.text() === 'Tải lại trường kỹ thuật')!.trigger('click');
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === 'Kiểm tra trường')!.trigger('click');
  expect(wrapper.get('select[name="technical_note_step"]').element).toHaveProperty('value', '');
  expect(wrapper.get('select[name="technical_note_step"]').text()).not.toContain('Xiết chân');
});


it('loads later BOM pages and sends only the chosen BOM association with source evidence', async () => {
  noteResponse();
  const original = vi.mocked(api.apiGet).getMockImplementation()!;
  vi.mocked(api.apiGet).mockImplementation(async (path, params) => path.endsWith('/bom-reference-options')
    ? { items: [{ fact_id: params?.offset ? 'symbol-51' : 'symbol-1', label: 'Nut M8', bom_position: params?.offset ? '51' : '1', locator: material.locator }], offset: params?.offset || 0, limit: 50, has_more: !params?.offset } as never
    : original(path, params));
  const wrapper = await editNote();
  await wrapper.findAll('button').find(b => b.text() === 'Tải dòng bảng kê')!.trigger('click');
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === 'Tải thêm dòng bảng kê')!.trigger('click');
  await flushPromises();
  expect(api.apiGet).toHaveBeenCalledWith('/api/rag-evidence/documents/17/bom-reference-options', { offset: 50, limit: 50 });
  await wrapper.get('select[name="technical_note_step"]').setValue('bom:symbol-51');
  expect(wrapper.get('a[data-bom-source]').attributes('href')).toBe('/api/files/documents/17/pages/2');
  await wrapper.get('form').trigger('submit');
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith('/api/rag-evidence/text-facts/note-1/review', 'POST', expect.objectContaining({ linked_step_fact_id: null, linked_bom_fact_id: 'symbol-51' }));
});

it('does not misrepresent a redacted stale BOM link as a general note', async () => {
  noteResponse({ ...drawingNote, review_status: 'approved', linked_bom_fact_id: null, linked_bom_status: 'unknown' } as typeof drawingNote);
  const wrapper = await editNote();
  expect(wrapper.text()).toContain('Liên kết bảng kê cần kiểm tra lại');
  expect(wrapper.get('select[name="technical_note_step"]').element).toHaveProperty('value', '');
  await wrapper.get('form').trigger('submit');
  expect(api.apiSend).not.toHaveBeenCalled();
});

it('discards a pending BOM options response after switching documents', async () => {
  noteResponse();
  const original = vi.mocked(api.apiGet).getMockImplementation()!;
  let finish: (value: unknown) => void = () => {};
  vi.mocked(api.apiGet).mockImplementation((path, params) => path.endsWith('/bom-reference-options')
    ? new Promise(resolve => { finish = resolve; }) as never : original(path, params));
  const wrapper = await editNote();
  await wrapper.findAll('button').find(b => b.text() === 'Tải dòng bảng kê')!.trigger('click');
  await wrapper.setProps({ docId: 18 });
  await flushPromises();
  finish({ items: [{ fact_id:'old-symbol', label:'Old secret label', bom_position:'19', locator:material.locator }],
    offset:0, limit:50, has_more:false });
  await flushPromises();
  await wrapper.findAll('button').find(b => b.text() === 'Kiểm tra trường')!.trigger('click');
  expect(wrapper.text()).not.toContain('Old secret label');
  expect(wrapper.get('select[name="technical_note_step"]').element).toHaveProperty('value', '');
  expect(api.apiSend).not.toHaveBeenCalled();
});
