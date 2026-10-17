<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { apiGet, apiSend } from '@/api/client';

type Document = { DocID: number; OriginalFileName: string; VersionNo: number; Site: string };
type Binding = { BindingID: number; PartKey: string; DocID: number; VersionNo: number; SourceDocID: number;
  SourceVersion: number; SourcePage: number; SourceQuote: string; SourceHash?: string; SnapshotID?: string; ReviewStatus: string };
type Result = { ok?: boolean; error?: string; reason?: string };
const props = defineProps<{ docId: number }>();
const documents = ref<Document[]>([]);
const scope = ref('');
const status = ref('pending');
const loadedScope = ref('');
const rows = ref<Binding[]>([]);
const offset = ref(0);
const hasMore = ref(false);
const loadingCatalog = ref(false);
const loading = ref(false);
const saving = ref(false);
const error = ref('');
const notice = ref('');
const selected = ref<Binding | null>(null);
const proposal = ref({ code: '', target: '', page: 1, quote: '', note: '' });
const decision = ref({ note: '', checked: false });
let documentSerial = 0;
let requestSerial = 0;
const source = computed(() => documents.value.find(d => d.DocID === props.docId));
const positive = (n: number) => Number.isInteger(n) && n > 0;
const validSource = computed(() => !!source.value && positive(source.value.VersionNo) && !!source.value.Site?.trim());
const targets = computed(() => validSource.value ? documents.value.filter(d => d.Site === source.value!.Site && positive(d.DocID) && positive(d.VersionNo)) : []);
const busy = computed(() => loadingCatalog.value || loading.value || saving.value);
const ready = computed(() => validSource.value && !!loadedScope.value && loadedScope.value === scope.value.trim() && !busy.value);
const hasPin = (row: Binding) => /^[a-fA-F0-9]{64}$/.test(row.SourceHash || '') && !!row.SnapshotID?.trim();
const canReview = computed(() => ready.value && selected.value?.ReviewStatus === 'pending' && hasPin(selected.value)
  && decision.value.checked && !!decision.value.note.trim());
const statusLabel = (value: string) => ({ pending: 'Chờ duyệt', approved: 'Đã duyệt', rejected: 'Đã từ chối' }[value] || value);
function name(id: number) { return documents.value.find(d => d.DocID === id)?.OriginalFileName || 'Bản vẽ được phép xem'; }
function ensureSuccess(result: Result) {
  if (result.ok !== false) return;
  const reason = result.reason || result.error;
  const message = ({ invalid_provenance: 'Chứng cứ hoặc phiên bản đã thay đổi. Cần tạo đề xuất mới từ nguồn hiện hành.',
    already_reviewed: 'Liên kết đã được người khác xử lý. Hãy tải lại danh sách.' } as Record<string, string>)[reason || ''];
  throw new Error(message || 'Chưa lưu được liên kết. Hãy kiểm tra nguồn và thử lại.');
}
function clearContext() {
  requestSerial += 1;
  rows.value = []; selected.value = null; loadedScope.value = ''; hasMore.value = false; offset.value = 0;
  loading.value = false; error.value = ''; notice.value = ''; decision.value = { note: '', checked: false };
}
async function loadCatalog() {
  const serial = ++documentSerial;
  clearContext(); documents.value = []; scope.value = ''; status.value = 'pending'; saving.value = false;
  proposal.value = { code: '', target: '', page: 1, quote: '', note: '' };
  loadingCatalog.value = true;
  try {
    const data = await apiGet<Result & { documents: Document[] }>('/api/documents', { bucket: 'effective' });
    if (serial !== documentSerial) return;
    ensureSuccess(data); documents.value = data.documents || [];
  } catch (err) {
    if (serial === documentSerial) error.value = err instanceof Error ? err.message : 'Không tải được danh mục bản vẽ.';
  } finally { if (serial === documentSerial) loadingCatalog.value = false; }
}
async function loadBindings(pageOffset = 0) {
  if (!validSource.value || !scope.value.trim() || saving.value) return;
  const serial = ++requestSerial;
  const productScope = scope.value.trim();
  loading.value = true; error.value = ''; notice.value = ''; rows.value = []; selected.value = null; loadedScope.value = ''; hasMore.value = false;
  decision.value = { note: '', checked: false };
  try {
    const data = await apiGet<Result & { items: Binding[]; offset: number; has_more: boolean }>(
      `/api/rag-evidence/documents/${props.docId}/drawing-bindings`,
      { product_scope: productScope, site: source.value!.Site, status: status.value, offset: pageOffset, limit: 50 });
    if (serial !== requestSerial) return;
    ensureSuccess(data); rows.value = data.items || []; offset.value = data.offset; hasMore.value = data.has_more; loadedScope.value = productScope;
    return true;
  } catch (err) {
    if (serial === requestSerial) error.value = err instanceof Error ? err.message : 'Không tải được liên kết.';
  } finally { if (serial === requestSerial) loading.value = false; }
}
async function propose() {
  if (!ready.value) return;
  const target = targets.value.find(d => d.DocID === Number(proposal.value.target));
  if (!target || !proposal.value.code.trim() || !positive(proposal.value.page) || !proposal.value.quote.trim() || !proposal.value.note.trim()) {
    error.value = 'Cần chọn bản vẽ, nhập mã, trang chứng cứ, trích dẫn và ghi chú đề xuất.'; return;
  }
  const serial = requestSerial;
  saving.value = true; error.value = ''; notice.value = '';
  try {
    const result = await apiSend<Result>('/api/rag-evidence/graph/binding', 'POST', {
      product_scope: loadedScope.value, site: source.value!.Site, note: proposal.value.note,
      payload: { part_key: 'part:' + proposal.value.code.trim().toLowerCase(), doc_id: target.DocID, version_no: target.VersionNo,
        source_doc_id: props.docId, source_version: source.value!.VersionNo, source_page: proposal.value.page, source_quote: proposal.value.quote },
    });
    if (serial !== requestSerial) return;
    ensureSuccess(result); saving.value = false; status.value = 'pending';
    proposal.value = { code: '', target: '', page: 1, quote: '', note: '' };
    if (await loadBindings()) notice.value = 'Đã gửi đề xuất chờ duyệt. Chưa được dùng làm quan hệ phục vụ.';
  } catch (err) {
    if (serial === requestSerial) error.value = err instanceof Error ? err.message : 'Chưa gửi được đề xuất.';
  } finally { if (serial === requestSerial) saving.value = false; }
}
function select(row: Binding) {
  selected.value = row; decision.value = { note: '', checked: false }; error.value = ''; notice.value = '';
}
async function review(action: 'approve' | 'reject') {
  if (!canReview.value || !selected.value) return;
  const serial = requestSerial;
  saving.value = true; error.value = ''; notice.value = '';
  try {
    const result = await apiSend<Result>(`/api/rag-evidence/graph/binding/${selected.value.BindingID}/review`, 'POST', {
      action, product_scope: loadedScope.value, site: source.value!.Site, note: decision.value.note,
    });
    if (serial !== requestSerial) return;
    ensureSuccess(result); saving.value = false;
    if (await loadBindings()) notice.value = 'Đã lưu quyết định liên kết.';
  } catch (err) {
    if (serial === requestSerial) error.value = err instanceof Error ? err.message : 'Chưa lưu được quyết định.';
  } finally { if (serial === requestSerial) saving.value = false; }
}
watch([scope, status], clearContext, { flush: 'sync' });
watch(() => props.docId, loadCatalog, { immediate: true });
onBeforeUnmount(() => { documentSerial += 1; requestSerial += 1; });
</script>

<template>
  <section class="binding-review" :aria-busy="busy">
    <h3>Liên kết chi tiết với bản vẽ</h3>
    <p>Đối chiếu mã và chứng cứ trong đúng phạm vi sản phẩm. Không suy diễn quan hệ từ tên tệp hoặc thư mục.</p>
    <p role="alert" v-if="error">{{ error }}</p><p role="status" v-if="notice">{{ notice }}</p>
    <button type="button" :disabled="busy" @click="loadCatalog">Tải lại danh mục bản vẽ</button>
    <p v-if="loadingCatalog" role="status">Đang tải danh mục bản vẽ…</p>
    <p v-else-if="!validSource">Cần tài liệu nguồn đã xuất bản, còn hiệu lực, có phiên bản và site hợp lệ. Hãy mở lại từ kho tài liệu hiện hành.</p>
    <p v-else>Nguồn: {{ source!.OriginalFileName }} — phiên bản {{ source!.VersionNo }} — site {{ source!.Site }}.
      <a :href="`/api/files/documents/${docId}/original`" target="_blank" rel="noopener noreferrer">Mở bản nguồn</a>
    </p>
    <div class="fields">
      <label class="stack-field">Phạm vi sản phẩm<input name="product_scope" v-model="scope" maxlength="255" :disabled="busy || !validSource"></label>
      <label class="stack-field">Trạng thái liên kết<select name="binding_status" v-model="status" :disabled="busy || !validSource">
        <option value="pending">Chờ duyệt</option><option value="approved">Đã duyệt</option><option value="rejected">Đã từ chối</option>
      </select></label>
    </div>
    <button type="button" :disabled="busy || !validSource || !scope.trim()" @click="loadBindings()">Tải liên kết</button>
    <p v-if="!loadedScope">Tải phạm vi sản phẩm trước khi đề xuất hoặc duyệt liên kết.</p>
    <form data-form="proposal" @submit.prevent="propose">
      <h4>Đề xuất liên kết mới</h4>
      <fieldset :disabled="!ready">
        <div class="fields">
          <label class="stack-field">Mã chi tiết / cụm<input name="part_code" v-model="proposal.code" maxlength="250" required></label>
          <label class="stack-field">Bản vẽ liên kết<select name="target_doc" v-model="proposal.target" required>
            <option value="">Chọn bản vẽ hiện hành</option><option v-for="target in targets" :key="target.DocID" :value="String(target.DocID)">{{ target.OriginalFileName }} — phiên bản {{ target.VersionNo }}</option>
          </select></label>
          <label class="stack-field">Trang chứng cứ<input name="source_page" type="number" v-model.number="proposal.page" min="1" step="1" required></label>
          <label class="stack-field">Trích dẫn chứng cứ<textarea name="source_quote" v-model="proposal.quote" maxlength="2000" required></textarea></label>
          <label class="stack-field">Ghi chú đề xuất<textarea name="proposal_note" v-model="proposal.note" maxlength="2000" required></textarea></label>
        </div>
        <button type="submit">Gửi đề xuất liên kết</button>
      </fieldset>
    </form>
    <div class="table-wrap" v-if="loadedScope">
      <table><caption>Liên kết trong phạm vi {{ loadedScope }} — {{ rows.length }} dòng trên trang</caption>
        <thead><tr><th scope="col">Mã / bản vẽ</th><th scope="col">Chứng cứ nguồn</th><th scope="col">Trạng thái</th><th scope="col">Thao tác</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.BindingID">
          <td>{{ row.PartKey }}<br>{{ name(row.DocID) }} — phiên bản {{ row.VersionNo }}<br>
            <a :href="`/api/files/documents/${row.DocID}/original`" target="_blank" rel="noopener noreferrer">Mở bản vẽ liên kết</a></td>
          <td>Phiên bản nguồn {{ row.SourceVersion }}, trang {{ row.SourcePage }}<p class="quote">{{ row.SourceQuote }}</p>
            <a :href="`/api/files/documents/${row.SourceDocID}/pages/${row.SourcePage}`" target="_blank" rel="noopener noreferrer">Xem trang chứng cứ</a>
            <a :href="`/api/files/documents/${row.SourceDocID}/original`" target="_blank" rel="noopener noreferrer">Mở bản nguồn</a></td>
          <td>{{ statusLabel(row.ReviewStatus) }}<p v-if="!hasPin(row)">Thiếu snapshot hoặc hash nguồn; cần tạo đề xuất mới.</p></td>
          <td><button type="button" :disabled="!ready || row.ReviewStatus !== 'pending'" @click="select(row)">Kiểm tra liên kết</button></td>
        </tr></tbody>
      </table>
      <div class="toolbar"><button type="button" :disabled="!ready || offset === 0" @click="loadBindings(Math.max(0, offset - 50))">Trang liên kết trước</button>
        <span>Trang {{ Math.floor(offset / 50) + 1 }}</span><button type="button" :disabled="!ready || !hasMore" @click="loadBindings(offset + 50)">Trang liên kết sau</button></div>
    </div>
    <form v-if="selected" data-form="review" @submit.prevent="review('approve')">
      <h4>Kiểm tra {{ selected.PartKey }}</h4>
      <p v-if="!hasPin(selected)">Liên kết cũ thiếu chứng cứ đã đóng phiên bản. Cần tạo đề xuất mới; không thể duyệt bản này.</p>
      <label class="stack-field">Ghi chú duyệt liên kết<textarea name="binding_review_note" v-model="decision.note" maxlength="2000" :disabled="busy" required></textarea></label>
      <label class="attestation"><input type="checkbox" name="binding_checked" v-model="decision.checked" :disabled="busy">Tôi đã đối chiếu mã, bản vẽ, phiên bản và phạm vi sản phẩm</label>
      <div class="toolbar"><button type="submit" :disabled="!canReview">Duyệt liên kết</button><button type="button" :disabled="!canReview" @click="review('reject')">Từ chối liên kết</button></div>
    </form>
  </section>
</template>

<style scoped>
.binding-review { display: grid; gap: 1rem; }
.fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: .8rem; margin-bottom: .8rem; }
.toolbar, .attestation { display: flex; align-items: center; flex-wrap: wrap; gap: .7rem; margin: .8rem 0; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: .65rem; border-bottom: 1px solid var(--p-content-border-color); vertical-align: top; overflow-wrap: anywhere; }
caption { text-align: left; margin-bottom: .5rem; }
form { border-top: 1px solid var(--p-content-border-color); padding-top: .7rem; }
fieldset { border: 0; padding: 0; min-width: 0; }
a { display: inline-block; margin: .3rem .5rem .3rem 0; }
button, input:not([type="checkbox"]), select, textarea { min-height: 44px; padding: .55rem; background: var(--p-content-background); color: var(--p-text-color); border: 1px solid var(--p-content-border-color); border-radius: .4rem; font: inherit; }
button { cursor: pointer; } button:disabled { opacity: .6; cursor: not-allowed; }
textarea { min-height: 90px; resize: vertical; }
.quote { white-space: pre-wrap; }
[role="alert"] { color: var(--p-red-500); }
</style>
