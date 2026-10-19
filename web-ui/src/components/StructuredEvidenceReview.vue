<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { apiGet, apiSend } from "@/api/client";
import TechnicalFieldReview from '@/components/TechnicalFieldReview.vue';
import DrawingBindingReview from '@/components/DrawingBindingReview.vue';

type Locator = { doc_id: string | number; version: string | number; page?: number; row_key: string; sheet?: string; cell?: string; table_index?: number; bbox?: number[] };
type Fact = { fact_id: string; label: string; raw_value: string; value: string | null; unit: string; currency?: string; review_status: string; conflicts: string[]; extraction_method?: string; locator: Locator;
  source_row?: { headers: string[]; cells: string[]; source_row: number } | null };
type Scope = { scope_id: string; snapshot_id: string; completeness_reviewed: boolean; expected_row_ids?: string[]; excluded_row_reasons?: Record<string, string>; rounding_scale?: number; rounding_mode?: string; definition?: string; period?: string; unit?: string; currency?: string; empty_source_locator?: Locator; empty_source_hash?: string; empty_source_extraction_method?: string;
  quantity_binding?: { root_doc_id: number; root_version: number; root_source_hash: string; product_scope: string; site: string } | null };
type Result = { ok?: boolean; error?: string };
const props = defineProps<{ docId: number }>();
const conflictLabels: Record<string, string> = { non_numeric_cell_type: 'Ô có kiểu dữ liệu hoặc định dạng cần kiểm tra trước khi dùng làm số' };
const showTechnicalFields = ref(false);
const showDrawingBindings = ref(false);
const scopes = ref<Scope[]>([]);
const scopeId = ref("");
const facts = ref<Fact[]>([]);
const offset = ref(0);
const hasMore = ref(false);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const notice = ref("");
const selected = ref<Fact | null>(null);
const draft = ref({ value: "", unit: "", currency: "", note: "" });
const manifest = ref({ rows: "", scale: 0, mode: "ROUND_HALF_UP", definition: "", period: "", note: "", checked: false, unit: "", currency: "" });
const quantityTarget = ref({ enabled: false, rootDoc: '', product: '', site: '' });
const exclusions = ref<{ row: string; reason: string }[]>([]);
let requestSerial = 0;
const expectedRows = computed(() => manifest.value.rows.split(/\r?\n/).map((row) => row.trim()).filter(Boolean));
const currentScope = computed(() => scopes.value.find((scope) => scope.scope_id === scopeId.value));
const emptyCandidate = computed(() => !!currentScope.value?.empty_source_locator
  && String(currentScope.value.empty_source_locator.doc_id) === String(props.docId)
  && /^[a-fA-F0-9]{64}$/.test(currentScope.value.empty_source_hash || '')
  && !!currentScope.value.empty_source_extraction_method && !facts.value.length && offset.value === 0 && !hasMore.value);
const canApproveScope = computed(() => !!scopeId.value && manifest.value.checked
  && (!quantityTarget.value.enabled || (expectedRows.value.length === 1
    && /^[1-9][0-9]*$/.test(quantityTarget.value.rootDoc) && Number.isSafeInteger(Number(quantityTarget.value.rootDoc))
    && !!quantityTarget.value.product.trim() && !!quantityTarget.value.site.trim()))
  && (emptyCandidate.value ? expectedRows.value.length === 0 && !!manifest.value.unit.trim() && !!manifest.value.note.trim() : expectedRows.value.length > 0)
  && !!manifest.value.definition.trim() && !loading.value && !saving.value);

function ensureSuccess(result: Result) {
  if (result.ok === false) throw new Error(result.error || "Thao tác chưa thành công. Hãy kiểm tra dữ liệu và thử lại.");
}

function statusLabel(status: string) {
  return ({ pending: "Chờ duyệt", approved: "Đã duyệt", rejected: "Đã từ chối" } as Record<string, string>)[status] || status;
}

function sourceUrl(fact: Fact, page = false) {
  const id = Number(fact.locator.doc_id);
  if (!Number.isInteger(id) || id <= 0) return "";
  if (page) {
    const pageNo = fact.locator.page;
    return pageNo && Number.isInteger(pageNo) && pageNo > 0 ? `/api/files/documents/${id}/pages/${pageNo}` : "";
  }
  return `/api/files/documents/${id}/original`;
}

function resetManifest(scope?: Scope) {
  quantityTarget.value = { enabled: !!scope?.quantity_binding, rootDoc: scope?.quantity_binding ? String(scope.quantity_binding.root_doc_id) : '',
    product: scope?.quantity_binding?.product_scope || '', site: scope?.quantity_binding?.site || '' };
  exclusions.value = Object.entries(scope?.excluded_row_reasons || {}).map(([row, reason]) => ({ row, reason }));
  manifest.value = {
    rows: (scope?.expected_row_ids || []).join("\n"), scale: scope?.rounding_scale ?? 0,
    mode: scope?.rounding_mode || "ROUND_HALF_UP", definition: scope?.definition || "",
    period: scope?.period || "", note: "", checked: false, unit: scope?.unit || "", currency: scope?.currency || "",
  };
}

async function loadFacts(id: string, pageOffset = 0, reset = false) {
  const serial = ++requestSerial;
  loading.value = true;
  error.value = "";
  facts.value = [];
  selected.value = null;
  hasMore.value = false;
  if (reset) resetManifest(scopes.value.find((scope) => scope.scope_id === id));
  try {
    const data = await apiGet<Result & { items: Fact[]; offset: number; has_more: boolean }>(`/api/rag-evidence/scopes/${encodeURIComponent(id)}/facts`, { offset: pageOffset, limit: 50 });
    if (serial !== requestSerial) return;
    ensureSuccess(data);
    facts.value = data.items || [];
    offset.value = data.offset;
    hasMore.value = data.has_more;
  } catch (err) {
    if (serial === requestSerial) error.value = err instanceof Error ? err.message : "Không tải được dữ liệu. Hãy tải lại.";
  } finally {
    if (serial === requestSerial) loading.value = false;
  }
}

async function loadScopes() {
  const serial = ++requestSerial;
  loading.value = true;
  error.value = "";
  notice.value = "";
  scopes.value = [];
  facts.value = [];
  scopeId.value = "";
  selected.value = null;
  resetManifest();
  try {
    const data = await apiGet<Result & { scopes: Scope[] }>(`/api/rag-evidence/documents/${props.docId}/scopes`);
    if (serial !== requestSerial) return;
    ensureSuccess(data);
    scopes.value = data.scopes || [];
    scopeId.value = scopes.value[0]?.scope_id || "";
    if (scopeId.value) await loadFacts(scopeId.value, 0, true);
  } catch (err) {
    if (serial === requestSerial) error.value = err instanceof Error ? err.message : "Không tải được phạm vi. Hãy tải lại.";
  } finally {
    if (serial === requestSerial) loading.value = false;
  }
}

function editFact(fact: Fact) {
  selected.value = fact;
  draft.value = { value: fact.conflicts.length ? "" : fact.value ?? "", unit: fact.unit, currency: fact.currency || "", note: "" };
  error.value = "";
  notice.value = "";
}

async function reviewFact(action: "approve" | "reject") {
  if (!selected.value || saving.value) return;
  const doc = props.docId;
  const fact = selected.value;
  saving.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await apiSend<Result>(`/api/rag-evidence/facts/${encodeURIComponent(fact.fact_id)}/review`, "POST", {
      action, note: draft.value.note,
      ...(action === "approve" ? { value_text: draft.value.value, unit: draft.value.unit, currency: draft.value.currency } : {}),
    });
    ensureSuccess(result);
    if (doc === props.docId) {
      await loadFacts(scopeId.value, offset.value);
      notice.value = "Đã lưu quyết định duyệt giá trị.";
    }
  } catch (err) {
    if (doc === props.docId) error.value = err instanceof Error ? err.message : "Chưa lưu được quyết định. Hãy thử lại.";
  } finally {
    saving.value = false;
  }
}

async function reviewScope() {
  if (!canApproveScope.value) return;
  if (new Set(expectedRows.value).size !== expectedRows.value.length) {
    error.value = "Danh sách đầy đủ có mã dòng trùng. Hãy đối soát lại.";
    return;
  }
  if (!Number.isInteger(manifest.value.scale) || manifest.value.scale < 0 || manifest.value.scale > 12) {
    error.value = "Số chữ số làm tròn phải là số nguyên từ 0 đến 12.";
    return;
  }
  const excluded = exclusions.value.map(({ row, reason }) => [row.trim(), reason.trim()] as const);
  if (excluded.some(([row, reason]) => !row || !reason)
    || new Set(excluded.map(([row]) => row)).size !== excluded.length
    || excluded.some(([row]) => expectedRows.value.includes(row))) {
    error.value = "Mỗi dòng loại trừ cần mã và lý do, không trùng nhau hoặc trùng dòng được tính.";
    return;
  }
  const doc = props.docId;
  saving.value = true;
  error.value = "";
  notice.value = "";
  try {
    const result = await apiSend<Result>(`/api/rag-evidence/scopes/${encodeURIComponent(scopeId.value)}/review`, "POST", {
      ...(excluded.length ? { excluded_row_reasons: Object.fromEntries(excluded) } : {}),
      expected_row_ids: expectedRows.value, rounding_scale: manifest.value.scale, rounding_mode: manifest.value.mode,
      definition: manifest.value.definition.trim(), period: manifest.value.period, note: manifest.value.note,
      ...(quantityTarget.value.enabled ? { quantity_target: { root_doc_id: Number(quantityTarget.value.rootDoc),
        product_scope: quantityTarget.value.product.trim(), site: quantityTarget.value.site.trim() } }
        : currentScope.value?.quantity_binding ? { quantity_target: null } : {}),
      ...(emptyCandidate.value ? { unit: manifest.value.unit.trim(), currency: manifest.value.currency.trim() } : {}),
    });
    ensureSuccess(result);
    if (doc === props.docId) {
      await loadScopes();
      notice.value = "Đã lưu xác nhận phạm vi đầy đủ.";
    }
  } catch (err) {
    if (doc === props.docId) error.value = err instanceof Error ? err.message : "Chưa xác nhận được phạm vi. Hãy thử lại.";
  } finally {
    saving.value = false;
  }
}

watch(() => props.docId, loadScopes, { immediate: true });
onBeforeUnmount(() => { requestSerial += 1; });
</script>

<template>
  <section class="structured-review" :aria-busy="loading || saving">
    <div class="review-toolbar">
      <h2>Kiểm tra dữ liệu có cấu trúc — tài liệu {{ docId }}</h2>
      <button type="button" :disabled="loading || saving" @click="loadScopes">Tải lại</button>
    </div>
    <p role="alert" v-if="error">{{ error }}</p>
    <p role="status" v-if="notice">{{ notice }}</p>
    <p v-if="loading" role="status">Đang tải dữ liệu…</p>
    <p v-else-if="!scopes.length && !error">Tài liệu chưa có phạm vi dữ liệu để kiểm tra.</p>
    <template v-if="scopes.length">
      <label class="stack-field">Phạm vi nguồn
        <select v-model="scopeId" class="native-select" :disabled="loading || saving" @change="loadFacts(scopeId, 0, true)">
          <option v-for="scope in scopes" :key="scope.scope_id" :value="scope.scope_id">{{ scope.scope_id }} — {{ scope.completeness_reviewed ? 'Đã xác nhận đầy đủ' : 'Chưa xác nhận đầy đủ' }}</option>
        </select>
      </label>
      <p>Snapshot: {{ currentScope?.snapshot_id }}. Các dòng trên trang này chưa chứng minh toàn bộ nguồn đã đầy đủ.</p>
      <p v-if="emptyCandidate">Nguồn khai báo không có dòng số liệu. Mở tệp và đối soát phạm vi trước khi xác nhận:
        <a data-source="empty-original" :href="`/api/files/documents/${docId}/original`" target="_blank" rel="noopener noreferrer">Mở tệp gốc</a>.
        Sheet {{ currentScope?.empty_source_locator?.sheet }}, ô {{ currentScope?.empty_source_locator?.cell }}, phiên bản {{ currentScope?.empty_source_locator?.version }}.
      </p>
      <div class="evidence-table-wrap">
        <table>
          <caption>Giá trị và chứng cứ nguồn, {{ facts.length }} dòng trên trang</caption>
          <thead><tr><th scope="col">Dòng / trường</th><th scope="col">Giá trị gốc</th><th scope="col">Chuẩn hóa / đơn vị</th><th scope="col">Trạng thái / xung đột</th><th scope="col">Vị trí nguồn</th><th scope="col">Thao tác</th></tr></thead>
          <tbody><tr v-for="fact in facts" :key="fact.fact_id">
            <td>{{ fact.locator.row_key }}<br>{{ fact.label }}
              <details v-if="fact.source_row" data-source="original-row">
                <summary>Dòng nguồn gốc {{ fact.source_row.source_row }} (chưa xác nhận)</summary>
                <dl><template v-for="(cell, index) in fact.source_row.cells" :key="index">
                  <dt>{{ fact.source_row.headers[index] || `Cột ${index + 1}` }}</dt><dd>{{ cell }}</dd>
                </template></dl>
              </details>
              <p v-else-if="fact.source_row === null && fact.locator.sheet">Không đọc được dòng nguồn đúng phiên bản. Hãy kiểm tra tệp gốc.</p>
            </td>
            <td>{{ fact.raw_value }}</td>
            <td>{{ fact.value ?? 'Chưa xác định' }} {{ fact.unit }} {{ fact.currency }}</td>
            <td>{{ statusLabel(fact.review_status) }}<p v-for="conflict in fact.conflicts" :key="conflict">{{ conflictLabels[conflict] || conflict }}</p></td>
            <td>Phiên bản {{ fact.locator.version }}<template v-if="fact.locator.page">, trang {{ fact.locator.page }}</template><template v-if="fact.locator.sheet">, sheet {{ fact.locator.sheet }}</template><template v-if="fact.locator.cell">, ô {{ fact.locator.cell }}</template><template v-if="fact.locator.table_index != null">, bảng {{ fact.locator.table_index }}</template><br>{{ fact.extraction_method }}<template v-if="fact.locator.bbox"><br>Vùng: {{ fact.locator.bbox.join(', ') }}</template><br>
              <a v-if="sourceUrl(fact, true)" data-source="page" :href="sourceUrl(fact, true)" target="_blank" rel="noopener noreferrer">Xem trang nguồn</a>
              <a v-if="sourceUrl(fact)" :href="sourceUrl(fact)" target="_blank" rel="noopener noreferrer">Mở tệp gốc</a>
            </td>
            <td><button type="button" :disabled="loading || saving" @click="editFact(fact)">Kiểm tra dòng</button></td>
          </tr></tbody>
        </table>
      </div>
      <div class="review-toolbar" aria-label="Phân trang dữ liệu">
        <button type="button" :disabled="offset === 0 || loading || saving" @click="loadFacts(scopeId, Math.max(0, offset - 50))">Trang trước</button>
        <span>Trang {{ Math.floor(offset / 50) + 1 }}</span>
        <button type="button" :disabled="!hasMore || loading || saving" @click="loadFacts(scopeId, offset + 50)">Trang sau</button>
      </div>
      <form v-if="selected" @submit.prevent="reviewFact('approve')">
        <h3>Kiểm tra {{ selected.label }} — {{ selected.locator.row_key }}</h3>
        <p>Giá trị gốc: {{ selected.raw_value }}. Sửa dưới đây giữ nguyên chứng cứ gốc.</p>
        <p v-if="selected.conflicts.length">Dòng có xung đột. Đối chiếu tệp gốc rồi nhập giá trị đã xác minh trước khi duyệt.</p>
        <div class="review-fields">
          <label class="stack-field">Giá trị chuẩn hóa<input name="value_text" v-model="draft.value" :disabled="saving" required></label>
          <label class="stack-field">Đơn vị<input name="unit" v-model="draft.unit" :disabled="saving"></label>
          <label class="stack-field">Loại tiền<input name="currency" v-model="draft.currency" :disabled="saving"></label>
          <label class="stack-field">Ghi chú kiểm tra<textarea name="fact_note" v-model="draft.note" :disabled="saving"></textarea></label>
        </div>
        <div class="review-toolbar"><button type="submit" :disabled="saving || loading || !draft.value.trim()">Duyệt giá trị</button><button type="button" :disabled="saving || loading" @click="reviewFact('reject')">Từ chối giá trị</button></div>
      </form>
      <form @submit.prevent="reviewScope">
        <h3>Xác nhận toàn bộ phạm vi dữ liệu</h3>
        <p>Liệt kê các dòng được tính và các dòng loại trừ có lý do, kể cả ở trang khác. Hai danh sách phải bao phủ toàn bộ nguồn đã đối soát. Dòng loại trừ vẫn giữ nguyên trong bảng nguồn.</p>
        <div class="review-fields">
          <label class="stack-field">Mã dòng được tính, mỗi mã một dòng<textarea name="expected_rows" v-model="manifest.rows" :disabled="saving || loading" :required="!emptyCandidate"></textarea></label>
          <template v-if="emptyCandidate">
            <label class="stack-field">Đơn vị của phạm vi rỗng<input name="scope_unit" v-model="manifest.unit" maxlength="64" :disabled="saving || loading" required></label>
            <label class="stack-field">Loại tiền của phạm vi rỗng<input name="scope_currency" v-model="manifest.currency" maxlength="16" :disabled="saving || loading"></label>
          </template>
          <label class="stack-field">Định nghĩa số liệu<input name="definition" v-model="manifest.definition" :disabled="saving || loading" required></label>
          <label class="stack-field">Kỳ / phạm vi áp dụng<input name="period" v-model="manifest.period" :disabled="saving || loading"></label>
          <label class="stack-field">Số chữ số thập phân<input type="number" name="rounding_scale" v-model.number="manifest.scale" min="0" max="12" step="1" :disabled="saving || loading" required></label>
          <label class="stack-field">Quy tắc làm tròn<select v-model="manifest.mode" class="native-select" :disabled="saving || loading"><option value="ROUND_HALF_UP">Nửa đơn vị làm tròn lên</option><option value="ROUND_HALF_EVEN">Nửa đơn vị về số chẵn</option></select></label>
          <label class="stack-field">Ghi chú xác nhận<textarea name="scope_note" v-model="manifest.note" :disabled="saving || loading" :required="emptyCandidate"></textarea></label>
        </div>
        <label class="review-attestation"><input type="checkbox" name="quantity_target_enabled" v-model="quantityTarget.enabled" :disabled="saving || loading">Dùng phạm vi này làm số lượng sản xuất cho BOM</label>
        <template v-if="quantityTarget.enabled">
          <p>Cần đúng một dòng đã duyệt với đơn vị piece, không có loại tiền, giá trị nguyên không âm. Máy chủ lấy phiên bản và chứng cứ từ bản vẽ hiện hành; không nhập mã băm.</p>
          <div class="review-fields">
            <label class="stack-field">ID bản vẽ gốc<input name="quantity_root_doc_id" v-model="quantityTarget.rootDoc" inputmode="numeric" :disabled="saving || loading" required></label>
            <label class="stack-field">Phạm vi sản phẩm của BOM<input name="quantity_product_scope" v-model="quantityTarget.product" maxlength="255" :disabled="saving || loading" required></label>
            <label class="stack-field">Site của BOM<input name="quantity_site" v-model="quantityTarget.site" maxlength="255" :disabled="saving || loading" required></label>
          </div>
        </template>
        <fieldset v-if="!emptyCandidate" :disabled="saving || loading">
          <legend>Dòng loại trừ khỏi phép tính</legend>
          <div v-for="(entry, index) in exclusions" :key="index" class="review-fields">
            <label class="stack-field">Mã dòng loại trừ {{ index + 1 }}<input :name="`excluded_row_${index}`" v-model="entry.row" required></label>
            <label class="stack-field">Lý do loại trừ {{ index + 1 }}<textarea :name="`excluded_reason_${index}`" v-model="entry.reason" required></textarea></label>
            <button type="button" @click="exclusions = exclusions.filter((_, position) => position !== index)">Bỏ dòng loại trừ {{ index + 1 }}</button>
          </div>
          <button type="button" @click="exclusions = [...exclusions, { row: '', reason: '' }]">Thêm dòng loại trừ</button>
        </fieldset>
        <label class="review-attestation"><input type="checkbox" name="scope_checked" v-model="manifest.checked" :disabled="saving || loading"><template v-if="emptyCandidate">Tôi đã kiểm tra nguồn, phạm vi, phiên bản, đơn vị và quy tắc làm tròn; xác nhận không có dòng số liệu.</template><template v-else>Tôi đã đối soát đầy đủ các dòng, phiên bản và quy tắc làm tròn của phạm vi này.</template></label>
        <button type="submit" :disabled="!canApproveScope">Xác nhận phạm vi đầy đủ</button>
      </form>
    </template>
    <button type="button" :aria-expanded="showTechnicalFields" @click="showTechnicalFields = !showTechnicalFields">Vật liệu, ký hiệu và bước lắp ráp</button>
    <TechnicalFieldReview v-if="showTechnicalFields" :doc-id="docId" />
    <button type="button" :aria-expanded="showDrawingBindings" @click="showDrawingBindings = !showDrawingBindings">Liên kết chi tiết với bản vẽ</button>
    <DrawingBindingReview v-if="showDrawingBindings" :key="docId" :doc-id="docId" />
  </section>
</template>

<style scoped>
.structured-review { display: grid; gap: 1rem; }
.review-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: .75rem; }
.review-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: .85rem; margin: 1rem 0; }
.review-attestation { display: flex; align-items: center; gap: .6rem; margin: 1rem 0; }
.evidence-table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: .65rem; border-bottom: 1px solid var(--p-content-border-color); vertical-align: top; overflow-wrap: anywhere; }
caption { text-align: left; margin-bottom: .5rem; }
a { display: inline-block; margin: .35rem .5rem .35rem 0; }
button, input:not([type="checkbox"]), select, textarea { min-height: 44px; padding: .55rem; background: var(--p-content-background); color: var(--p-text-color); border: 1px solid var(--p-content-border-color); border-radius: .4rem; font: inherit; }
button { cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .6; }
textarea { min-height: 90px; resize: vertical; }
[role="alert"] { color: var(--p-red-500); }
form { border-top: 1px solid var(--p-content-border-color); padding-top: 1rem; }
</style>
