<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { apiGet, apiSend } from '@/api/client';

type Field = { fact_id: string; kind: string; label: string; raw_value: string; value: string | null; sequence_number?: number;
  bom_position?: string; candidates: string[]; conflicts: string[]; review_status: string; linked_step_fact_id?: string | null; linked_bom_fact_id?: string | null; linked_bom_status?: 'valid' | 'unknown' | null;
  locator: { doc_id: string; version: string; page?: number; row_key?: string; bbox?: number[] } };
type BomOption = Pick<Field, 'fact_id' | 'label' | 'bom_position' | 'locator'>;
type StepOption = { fact_id: string; sequence_number: number; label: string; review_status: string };
type Sequence = { status: string; snapshot_id?: string; expected_step_numbers?: number[]; definition?: string; step_options?: StepOption[] };
const props = defineProps<{ docId: number }>();
const fields = ref<Field[]>([]);
const selected = ref<Field | null>(null);
const offset = ref(0);
const hasMore = ref(false);
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const notice = ref('');
const draft = ref({ value: '', number: 1, note: '', noteStep: '' });
const sequence = ref<Sequence | null>(null);
const sequenceDraft = ref({ numbers: '', definition: '', note: '' });
const bomOptions = ref<BomOption[]>([]);
const bomOffset = ref(0);
const bomMore = ref(true);
const bomLoading = ref(false);
let serial = 0;
const kinds: Record<string, string> = { material: 'Vật liệu', symbol: 'Mã / ký hiệu', assembly_step: 'Bước lắp ráp', drawing_note: 'Chú thích bản vẽ' };
const statuses: Record<string, string> = { pending: 'Chờ duyệt', approved: 'Đã duyệt', rejected: 'Đã từ chối' };
const conflicts: Record<string, string> = { material_conflict: 'Vật liệu chưa thống nhất giữa tên chi tiết và bảng kê',
  material_extractor_disagreement: 'Vật liệu đọc từ chữ và ảnh chưa thống nhất', missing_material: 'Chưa đọc được vật liệu',
  duplicate_step_number: 'Số bước bị trùng; cần đối chiếu lại trình tự', invalid_step: 'Bước chưa đủ nội dung hoặc số thứ tự',
  caption_boundary_requires_review: 'Cần đối chiếu vùng chú thích để xác nhận đầy đủ nội dung bước',
  caption_geometry_unavailable: 'Chưa xác định được vùng chú thích; cần kiểm tra văn bản với trang nguồn',
  caption_marker_geometry_mismatch: 'Số bước trong vùng chú thích chưa khớp văn bản; cần đối chiếu nguồn',
  drawing_note_boundary_requires_review: 'Cần đối chiếu đầy đủ vùng chú thích trước khi duyệt',
  drawing_note_association_requires_review: 'Cần đối chiếu chú thích và chọn bước liên quan, dòng bảng kê hoặc ghi chú chung' };

function approvedStep(id: string) {
  return sequence.value?.step_options?.find(step => step.fact_id === id && step.review_status === 'approved');
}

function noteAssociation(field: Field) {
  if (field.review_status !== 'approved') return 'Chưa gắn bước';
  if (field.linked_bom_status === 'unknown') return 'Liên kết bảng kê cần kiểm tra lại';
  if (field.linked_bom_fact_id) return field.linked_bom_status === 'valid' ? 'Liên kết bảng kê đã duyệt' : 'Liên kết bảng kê cần kiểm tra lại';
  if (field.linked_step_fact_id === null) return 'Ghi chú chung của tài liệu';
  const step = approvedStep(field.linked_step_fact_id || '');
  return step ? `Bước ${step.sequence_number} — ${step.label}` : 'Liên kết bước cần kiểm tra lại';
}

function sourceUrl(field: Pick<Field, 'locator'>, page = false) {
  const id = Number(field.locator.doc_id);
  if (!Number.isInteger(id) || id <= 0) return '';
  if (!page) return `/api/files/documents/${id}/original`;
  const number = field.locator.page;
  return number && Number.isInteger(number) && number > 0 ? `/api/files/documents/${id}/pages/${number}` : '';
}

async function load(pageOffset = 0) {
  const request = ++serial;
  loading.value = true;
  fields.value = [];
  selected.value = null;
  draft.value = { value: '', number: 1, note: '', noteStep: '' };
  sequence.value = null;
  bomOptions.value = []; bomOffset.value = 0; bomMore.value = true; bomLoading.value = false;
  hasMore.value = false;
  error.value = '';
  try {
    const [data, order] = await Promise.all([
      apiGet<{ ok?: boolean; error?: string; items: Field[]; offset: number; has_more: boolean }>(
        `/api/rag-evidence/documents/${props.docId}/text-facts`, { offset: pageOffset, limit: 50 }),
      apiGet<Sequence & { ok?: boolean; error?: string }>(
        `/api/rag-evidence/documents/${props.docId}/assembly-sequence`),
    ]);
    if (request !== serial) return;
    if (data.ok === false) throw new Error(data.error || 'Chưa tải được chứng cứ. Hãy tải lại.');
    if (order.ok === false) throw new Error(order.error || 'Chưa tải được xác nhận trình tự.');
    fields.value = data.items || [];
    offset.value = data.offset;
    hasMore.value = data.has_more;
    sequence.value = order;
  } catch (err) {
    if (request === serial) error.value = err instanceof Error ? err.message : 'Chưa tải được chứng cứ. Hãy tải lại.';
  } finally {
    if (request === serial) loading.value = false;
  }
}

async function loadBom() {
  if (bomLoading.value || !bomMore.value) return;
  const request = serial;
  bomLoading.value = true;
  error.value = '';
  try {
    const data = await apiGet<{ ok?: boolean; error?: string; items: BomOption[]; offset: number; limit: number; has_more: boolean }>(
      `/api/rag-evidence/documents/${props.docId}/bom-reference-options`, { offset: bomOffset.value, limit: 50 });
    if (request !== serial) return;
    if (data.ok === false || !Array.isArray(data.items)) throw new Error(data.error || 'Chưa tải được dòng bảng kê.');
    bomOptions.value = [...bomOptions.value, ...data.items];
    bomOffset.value = data.offset + data.limit;
    bomMore.value = data.has_more;
  } catch (err) {
    if (request === serial) error.value = err instanceof Error ? err.message : 'Chưa tải được dòng bảng kê.';
  } finally { if (request === serial) bomLoading.value = false; }
}

async function reviewSequence() {
  if (!sequence.value?.snapshot_id || saving.value) return;
  const tokens = sequenceDraft.value.numbers.trim().split(/[\s,;]+/);
  const numbers = tokens.map(Number);
  if (!sequenceDraft.value.note.trim() || !sequenceDraft.value.definition.trim()
      || tokens.some(n => !/^[1-9]\d*$/.test(n)) || numbers.some(n => n > 100000)
      || numbers.length > 500 || new Set(numbers).size !== numbers.length) {
    error.value = 'Cần khai báo các số bước không trùng, phạm vi và ghi chú đã đối chiếu toàn bộ nguồn.'; return;
  }
  const doc = props.docId;
  const snapshot = sequence.value.snapshot_id;
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const result = await apiSend<{ ok?: boolean; error?: string }>(`/api/rag-evidence/documents/${doc}/assembly-sequence/review`, 'POST', {
      snapshot_id: snapshot, expected_step_numbers: numbers,
      definition: sequenceDraft.value.definition, note: sequenceDraft.value.note,
    });
    if (result.ok === false) throw new Error(result.error || 'Chưa lưu được xác nhận trình tự.');
    if (doc === props.docId) { await load(offset.value); notice.value = 'Đã lưu quyết định xác nhận trình tự.'; }
  } catch (err) {
    if (doc === props.docId) error.value = err instanceof Error ? err.message : 'Chưa lưu được xác nhận. Hãy thử lại.';
  } finally { saving.value = false; }
}

function edit(field: Field) {
  selected.value = field;
  const noteStep = field.kind === 'drawing_note' && field.review_status === 'approved'
    ? field.linked_bom_status === 'unknown' || field.linked_bom_fact_id ? ''
      : field.linked_step_fact_id === null ? 'document'
      : approvedStep(field.linked_step_fact_id || '') ? `step:${field.linked_step_fact_id}` : ''
    : '';
  draft.value = { value: field.value ?? '', number: field.sequence_number ?? 1, note: '', noteStep };
  error.value = '';
  notice.value = '';
}

async function review(action: 'approve' | 'reject') {
  if (!selected.value || saving.value) return;
  if (!draft.value.note.trim()) { error.value = 'Cần ghi chú kết quả đối chiếu nguồn trước khi duyệt.'; return; }
  const field = selected.value;
  const noteStep = draft.value.noteStep || '';
  if (action === 'approve' && field.kind === 'drawing_note'
      && noteStep !== 'document' && !(noteStep.startsWith('step:') && approvedStep(noteStep.slice(5)))
      && !(noteStep.startsWith('bom:') && bomOptions.value.some(option => option.fact_id === noteStep.slice(4)))) {
    error.value = 'Cần chọn bước đã duyệt hoặc ghi chú chung, hoặc dòng bảng kê đã duyệt sau khi đối chiếu nguồn.'; return;
  }
  const doc = props.docId;
  saving.value = true;
  error.value = '';
  notice.value = '';
  try {
    const result = await apiSend<{ ok?: boolean; error?: string }>(`/api/rag-evidence/text-facts/${encodeURIComponent(field.fact_id)}/review`, 'POST', {
      action, note: draft.value.note,
      ...(action === 'approve' ? { value_text: draft.value.value,
        ...(field.kind === 'assembly_step' ? { sequence_number: draft.value.number } : {}),
        ...(field.kind === 'drawing_note' ? { linked_step_fact_id: noteStep.startsWith('step:') ? noteStep.slice(5) : null,
          linked_bom_fact_id: noteStep.startsWith('bom:') ? noteStep.slice(4) : null } : {}) } : {}),
    });
    if (result.ok === false) throw new Error(result.error || 'Chưa lưu được quyết định duyệt.');
    if (doc === props.docId) { await load(offset.value); notice.value = 'Đã lưu quyết định duyệt trường kỹ thuật.'; }
  } catch (err) {
    if (doc === props.docId) error.value = err instanceof Error ? err.message : 'Chưa lưu được quyết định. Hãy thử lại.';
  } finally { saving.value = false; }
}

watch(() => props.docId, () => { notice.value = ''; sequenceDraft.value = { numbers: '', definition: '', note: '' }; void load(); }, { immediate: true });
onBeforeUnmount(() => { serial += 1; });
</script>

<template>
  <section class="technical-fields" :aria-busy="loading || saving">
    <div class="toolbar"><h3>Vật liệu, ký hiệu và bước lắp ráp</h3><button type="button" :disabled="loading || saving" @click="load(offset)">Tải lại trường kỹ thuật</button></div>
    <p>Đối chiếu từng trường với tài liệu gốc trước khi duyệt. Một trang dữ liệu hoặc các trường đã duyệt chưa xác nhận đầy đủ cấu trúc sản phẩm hay trình tự lắp ráp.</p>
    <p v-if="loading" role="status">Đang tải chứng cứ kỹ thuật…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="!loading && !error && !fields.length">Chưa có trường kỹ thuật để duyệt.</p>
    <div v-if="fields.length" class="table-wrap"><table>
      <caption>Trường kỹ thuật và căn cứ nguồn, {{ fields.length }} dòng trên trang</caption>
      <thead><tr><th scope="col">Trường / vị trí</th><th scope="col">Giá trị gốc</th><th scope="col">Chuẩn hóa / đối chiếu</th><th scope="col">Trạng thái</th><th scope="col">Nguồn</th><th scope="col">Thao tác</th></tr></thead>
      <tbody><tr v-for="field in fields" :key="field.fact_id">
        <td>{{ kinds[field.kind] || field.kind }} — {{ field.label }}<br><template v-if="field.bom_position">STT bảng kê: {{ field.bom_position }}<br></template><template v-if="field.sequence_number">Bước {{ field.sequence_number }}<br></template>{{ field.locator.row_key }}</td>
        <td>{{ field.raw_value }}</td><td>{{ field.value ?? 'Chưa xác định' }}<p v-for="candidate in field.candidates" :key="candidate">{{ candidate }}</p></td>
        <td>{{ statuses[field.review_status] || field.review_status }}<p v-if="field.kind === 'drawing_note'">{{ noteAssociation(field) }}</p><p v-for="conflict in field.conflicts" :key="conflict">{{ conflicts[conflict] || conflict }}</p></td>
        <td>Phiên bản {{ field.locator.version }}<template v-if="field.locator.page">, trang {{ field.locator.page }}</template><p v-if="field.locator.bbox">Vùng: {{ field.locator.bbox.join(', ') }}</p><br>
          <a v-if="sourceUrl(field, true)" data-source="page" :href="sourceUrl(field, true)" target="_blank" rel="noopener noreferrer">Xem trang nguồn</a><br>
          <a v-if="sourceUrl(field)" :href="sourceUrl(field)" target="_blank" rel="noopener noreferrer">Mở tệp gốc</a>
        </td><td><button type="button" :disabled="loading || saving" @click="edit(field)">Kiểm tra trường</button></td>
      </tr></tbody>
    </table></div>
    <div class="toolbar" aria-label="Phân trang trường kỹ thuật">
      <button type="button" :disabled="offset === 0 || loading || saving" @click="load(Math.max(0, offset - 50))">Trường trước</button>
      <span>Trang trường {{ Math.floor(offset / 50) + 1 }}</span>
      <button type="button" :disabled="!hasMore || loading || saving" @click="load(offset + 50)">Trường sau</button>
    </div>
    <form v-if="selected" @submit.prevent="review('approve')">
      <h4>Kiểm tra {{ selected.label }}</h4><p>Giá trị gốc: {{ selected.raw_value }}. Quyết định duyệt giữ nguyên chứng cứ gốc.</p>
      <label>Giá trị đã đối chiếu<textarea name="technical_value" v-model="draft.value" maxlength="16000" :disabled="saving" required></textarea></label>
      <label v-if="selected.kind === 'assembly_step'">Số bước đã đối chiếu<input type="number" name="technical_step" v-model.number="draft.number" min="1" max="100000" step="1" :disabled="saving" required></label>
      <label v-if="selected.kind === 'drawing_note'">Phạm vi liên quan của chú thích<select name="technical_note_step" v-model="draft.noteStep" :disabled="saving" required>
        <option value="" disabled>Chọn sau khi đối chiếu nguồn</option>
        <option value="document">Ghi chú chung của tài liệu</option>
        <option v-for="step in sequence?.step_options || []" :key="step.fact_id" :value="`step:${step.fact_id}`" :disabled="step.review_status !== 'approved'">Bước {{ step.sequence_number }} — {{ step.label }}</option>
        <option v-for="option in bomOptions" :key="option.fact_id" :value="`bom:${option.fact_id}`">Dòng bảng kê {{ option.bom_position }} — {{ option.label }}</option>
      </select></label>
      <div v-if="selected.kind === 'drawing_note'">
        <button v-if="bomMore" type="button" :disabled="saving || bomLoading" @click="loadBom">{{ bomOptions.length ? 'Tải thêm dòng bảng kê' : 'Tải dòng bảng kê' }}</button>
        <p v-if="bomLoading" role="status">Đang tải dòng bảng kê…</p>
        <p v-if="!bomMore && !bomOptions.length">Chưa có dòng bảng kê đã duyệt để liên kết.</p>
        <template v-for="option in bomOptions.filter(item => draft.noteStep === `bom:${item.fact_id}`)" :key="option.fact_id">
          <p>Phiên bản {{ option.locator.version }}<template v-if="option.locator.bbox">; vùng: {{ option.locator.bbox.join(', ') }}</template></p>
          <div class="toolbar">
            <a v-if="sourceUrl(option, true)" data-bom-source :href="sourceUrl(option, true)" target="_blank" rel="noopener noreferrer">Xem trang nguồn bảng kê</a>
            <a v-if="sourceUrl(option)" :href="sourceUrl(option)" target="_blank" rel="noopener noreferrer">Mở tệp bảng kê gốc</a>
          </div>
        </template>
      </div>
      <label>Ghi chú kiểm tra<textarea name="technical_note" v-model="draft.note" maxlength="2000" :disabled="saving" required></textarea></label>
      <div class="toolbar"><button type="submit" :disabled="loading || saving">Duyệt trường</button><button type="button" :disabled="loading || saving" @click="review('reject')">Từ chối trường</button></div>
    </form>
    <form v-if="sequence?.snapshot_id" data-sequence-review @submit.prevent="reviewSequence">
      <h4>Xác nhận đầy đủ trình tự lắp ráp</h4>
      <p>Trạng thái: {{ sequence.status === 'complete' ? 'Đã xác nhận đầy đủ' : 'Chưa xác nhận đầy đủ' }}.
        Khai báo theo toàn bộ tài liệu gốc; các dòng trên trang hiện tại không thay thế việc kiểm tra đầy đủ. Sửa bước hoặc đổi nguồn sẽ làm xác nhận cũ hết hiệu lực.</p>
      <p v-if="sequence.status === 'complete'">Phạm vi đã duyệt: {{ sequence.definition }}. Số bước: {{ sequence.expected_step_numbers?.join(', ') }}.</p>
      <label>Các số bước đã đối chiếu toàn bộ nguồn<textarea name="sequence_numbers" v-model="sequenceDraft.numbers" maxlength="4000" placeholder="Ví dụ: 1, 2, 3, 4, 5, 6" :disabled="saving" required></textarea></label>
      <label>Phạm vi trình tự trong nguồn<textarea name="sequence_definition" v-model="sequenceDraft.definition" maxlength="2000" :disabled="saving" required></textarea></label>
      <label>Ghi chú xác nhận toàn bộ nguồn<textarea name="sequence_note" v-model="sequenceDraft.note" maxlength="2000" :disabled="saving" required></textarea></label>
      <button type="submit" :disabled="loading || saving">Xác nhận trình tự đầy đủ</button>
    </form>
  </section>
</template>

<style scoped>
.technical-fields { display: grid; gap: 1rem; border-top: 1px solid var(--p-content-border-color); padding-top: 1rem; }
.toolbar { display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: .65rem; border-bottom: 1px solid var(--p-content-border-color); vertical-align: top; overflow-wrap: anywhere; }
caption { text-align: left; margin-bottom: .5rem; }
label { display: grid; gap: .4rem; margin-bottom: .8rem; }
button, textarea, input, select { min-height: 44px; padding: .55rem; background: var(--p-content-background); color: var(--p-text-color); border: 1px solid var(--p-content-border-color); border-radius: .4rem; font: inherit; }
textarea { min-height: 90px; resize: vertical; }
button:disabled { opacity: .6; cursor: not-allowed; }
[role="alert"] { color: var(--p-red-500); }
</style>

