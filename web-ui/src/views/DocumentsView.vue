<script setup lang="ts">
import { computed, reactive, ref, watch, watchEffect } from "vue";
import { useRoute, useRouter } from "vue-router";
import ResourcePage from "@/components/ResourcePage.vue";
import StructuredEvidenceReview from "@/components/StructuredEvidenceReview.vue";
import LifecycleView from "@/views/LifecycleView.vue";
import { apiGet, apiSend } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { isRoleAllowed } from "@/authorization";
import { num, pickStr, SECURITY_LEVELS } from "@/utils/rows";
import type { ApiRow, ResourceColumn, ResourceFilter, RowAction, ToolbarAction } from "@/types";

type DocumentTab = "effective" | "expired" | "expiring-soon" | "needs-review";
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const canManage = computed(() => isRoleAllowed(auth.user?.roles, ["reviewer", "admin"]));
const evidenceDialog = reactive({ visible: false, docId: 0 });
const pagePreview = ref({ visible: false, pageUrl: '', originalUrl: '', name: '', failed: false });
const requestedTab = computed<DocumentTab>(() => {
  const value = String(route.query.tab ?? "effective");
  return ["effective", "expired", "expiring-soon", "needs-review"].includes(value) ? value as DocumentTab : "effective";
});
const activeTab = computed<DocumentTab>(() => canManage.value ? requestedTab.value : "effective");
const lifecycleGroup = computed(() => activeTab.value.replace(/-/g, "_") as "expired" | "expiring_soon" | "needs_review");
watchEffect(() => {
  if (!canManage.value && route.query.tab && route.query.tab !== "effective") {
    void router.replace({ path: "/documents", query: { tab: "effective" } });
  }
});
function selectTab(tab: DocumentTab) { void router.replace({ path: "/documents", query: { tab } }); }

const columns: ResourceColumn[] = [
  { field: "DocID", header: "DocID" },
  { field: "OriginalFileName", header: "Tên file" },
  { field: "Department", header: "Phòng ban" },
  { field: "Domain", header: "Domain" },
  { field: "SecurityLevel", header: "Mức mật", kind: "tag" },
  { field: "Site", header: "Site" },
  { field: "VersionNo", header: "Phiên bản" },
  { field: "IsCurrentVersion", header: "Hiện hành", kind: "bool" },
  { field: "LifecycleStatus", header: "Vòng đời", kind: "tag" },
  { field: "ReviewStatus", header: "Duyệt", kind: "tag" },
  { field: "CreatedAt", header: "Ngày tải" },
];

const filters: ResourceFilter[] = [
  { key: "search", label: "Tìm kiếm", type: "text" },
  { key: "dept", label: "Phòng ban", type: "text" },
  { key: "domain", label: "Domain", type: "text" },
  { key: "sec", label: "Mức mật", type: "select", options: SECURITY_LEVELS },
];

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/documents", { ...f, bucket: "effective", soon_days: 30 });
  return data.documents ?? [];
}

// GD2: form sua metadata day du (~13 truong) thay cho chuoi window.prompt cu.
type MetaFieldType = "text" | "textarea" | "date" | "lang" | "status";
const META_FIELDS: ReadonlyArray<{ key: string; label: string; type: MetaFieldType }> = [
  { key: "title", label: "Tiêu đề", type: "text" },
  { key: "summary", label: "Tóm tắt", type: "textarea" },
  { key: "tags", label: "Tags", type: "text" },
  { key: "doc_number", label: "Số hiệu", type: "text" },
  { key: "issued_date", label: "Ngày ban hành", type: "date" },
  { key: "effective_date", label: "Ngày hiệu lực", type: "date" },
  { key: "expiry_date", label: "Ngày hết hạn", type: "date" },
  { key: "review_date", label: "Ngày rà soát", type: "date" },
  { key: "owner_signer", label: "Người ký / chủ quản", type: "text" },
  { key: "site", label: "Site", type: "text" },
  { key: "language", label: "Ngôn ngữ", type: "lang" },
  { key: "effective_status", label: "Trạng thái hiệu lực", type: "status" },
];
const LANGUAGES = ["vi", "en", "ja", "zh", "ko"];
const EFFECTIVE_STATUSES = ["draft", "effective", "superseded", "expired", "withdrawn"];

const editVisible = ref(false);
const editBusy = ref(false);
const editError = ref("");
const linkError = ref("");
const editDocId = ref<number | null>(null);
const editPending = ref(false);
const variantCode = ref("");
const editValues = reactive<Record<string, string>>({});
let resolveEdit: (() => void) | null = null;
let rejectEdit: ((e: unknown) => void) | null = null;

function toDateStr(value: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return m ? m[1] : "";
}

function openEdit(row: ApiRow): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveEdit = resolve;
    rejectEdit = reject;
    editDocId.value = num(row, "DocID");
    editPending.value = pickStr(row, ["ReviewStatus"]) === "pending_review";
    variantCode.value = pickStr(row, ["BranchLabel", "VariantCode"]);
    editError.value = "";
    for (const f of META_FIELDS) {
      let val = pickStr(row, [f.key, f.key.replace(/_/g, "")]);
      if (f.type === "date") val = toDateStr(val);
      editValues[f.key] = val;
    }
    editVisible.value = true;
  });
}

watch(() => route.query.doc, async (value) => {
  const id = Number(value);
  if (!canManage.value || !Number.isSafeInteger(id) || id <= 0) return;
  linkError.value = "";
  try {
    const data = await apiGet<{ documents: ApiRow[] }>("/api/documents/pending-review");
    const row = (data.documents ?? []).find((item) => num(item, "DocID") === id);
    if (row) void openEdit({ ...row, ReviewStatus: "pending_review" }).catch(() => {});
    else linkError.value = "Không tìm thấy tài liệu chờ duyệt được phép sửa.";
  } catch {
    linkError.value = "Không thể mở tài liệu chờ duyệt này.";
  }
}, { immediate: true });

async function submitEdit() {
  if (editDocId.value === null) return;
  editBusy.value = true;
  editError.value = "";
  try {
    const body: Record<string, unknown> = {};
    for (const f of META_FIELDS) {
      const v = (editValues[f.key] ?? "").trim();
      if (v) body[f.key] = v; // de trong = giu nguyen
    }
    if (!Object.keys(body).length) {
      editError.value = "Chưa nhập trường nào để cập nhật.";
      editBusy.value = false;
      return;
    }
    await apiSend(`/api/documents/${editDocId.value}/metadata`, "PATCH", body);
    editVisible.value = false;
    resolveEdit?.();
    resolveEdit = null;
    rejectEdit = null;
  } catch (err) {
    editError.value = err instanceof Error ? err.message : "Lỗi";
  } finally {
    editBusy.value = false;
  }
}

async function submitVariantCode() {
  if (editDocId.value === null) return;
  const code = variantCode.value.trim();
  if (!code || code.toLowerCase() === "default") {
    editError.value = "Nhập mã biến thể riêng, khác default.";
    return;
  }
  editBusy.value = true;
  editError.value = "";
  try {
    const result = await apiSend<{ ok: boolean }>(`/api/documents/${editDocId.value}/variant-code`, "PATCH", { variant_code: code });
    if (result?.ok !== true) throw new Error("Không lưu được mã biến thể");
    editVisible.value = false;
    resolveEdit?.();
    resolveEdit = null;
    rejectEdit = null;
    void router.push({ path: "/review" });
  } catch (err) {
    editError.value = err instanceof Error ? err.message : "Không lưu được mã biến thể";
  } finally {
    editBusy.value = false;
  }
}

function cancelEdit() {
  editVisible.value = false;
  rejectEdit?.(new Error(""));
  resolveEdit = null;
  rejectEdit = null;
}

function sourceUrl(row: ApiRow, resource: string): string {
  const version = num(row, 'VersionNo');
  const suffix = Number.isInteger(version) && version > 0 ? `?citation_version=${version}` : '';
  return `/api/files/documents/${num(row, 'DocID')}/${resource}${suffix}`;
}

const managementActions: RowAction[] = [
  {
    label: "Xem trang",
    visible: (row) => !/\.(md|markdown)$/i.test(pickStr(row, ['OriginalFileName'])),
    run: async (row) => {
      pagePreview.value = {
        visible: true, pageUrl: sourceUrl(row, 'pages/1'), originalUrl: sourceUrl(row, 'original'),
        name: pickStr(row, ['OriginalFileName']) || `Doc ${num(row, 'DocID')}`, failed: false,
      };
    },
  },
  {
    label: "Tải bản gốc",
    run: async (row) => {
      window.open(sourceUrl(row, 'original'), "_blank", "noopener");
    },
  },
  {
    label: "Sửa metadata",
    run: (row) => openEdit(row),
  },
  {
    label: "Kiểm tra chứng cứ",
    run: async (row) => { evidenceDialog.docId = num(row, "DocID"); evidenceDialog.visible = true; },
  },
  {
    label: "Đặt hiện hành",
    visible: (row) => !num(row, "IsCurrentVersion"),
    run: (row) => apiSend(`/api/documents/${num(row, "DocID")}/current`, "PATCH"),
  },
  {
    label: "Đánh dấu hết hạn",
    severity: "warning",
    confirm: "Đánh dấu tài liệu này hết hiệu lực?",
    run: (row) => apiSend(`/api/documents/${num(row, "DocID")}/expired`, "PATCH"),
  },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá vĩnh viễn tài liệu này?",
    run: (row) => apiSend(`/api/documents/${num(row, "DocID")}`, "DELETE"),
  },
];
const viewerActions = managementActions.slice(0, 2);
const rowActions = computed(() => canManage.value ? managementActions : viewerActions);

const bulk = reactive({ visible: false, busy: false, error: "", ids: "" });
let resolveBulk: (() => void) | null = null;
let rejectBulk: ((e: unknown) => void) | null = null;
let bulkChanged = false;

function openBulkDelete(): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveBulk = resolve;
    rejectBulk = reject;
    bulkChanged = false;
    bulk.ids = "";
    bulk.error = "";
    bulk.visible = true;
  });
}

async function submitBulkDelete() {
  const ids = bulk.ids
    .split(/[\s,]+/)
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);
  if (!ids.length) {
    bulk.error = "Nhập ít nhất một DocID.";
    return;
  }
  bulk.busy = true;
  bulk.error = "";
  try {
    for (const id of ids) {
      const result = await apiSend<{ ok: boolean }>(`/api/documents/${id}`, "DELETE");
      if (!result.ok) throw new Error(`Không xóa được DocID ${id}.`);
      bulkChanged = true;
    }
    bulk.visible = false;
    resolveBulk?.();
    resolveBulk = null;
    rejectBulk = null;
  } catch (err) {
    bulk.error = err instanceof Error ? err.message : "Lỗi";
  } finally {
    bulk.busy = false;
  }
}

function cancelBulkDelete() {
  bulk.visible = false;
  if (bulkChanged) resolveBulk?.();
  else rejectBulk?.(new Error(""));
  resolveBulk = null;
  rejectBulk = null;
}

const toolbar = computed<ToolbarAction[]>(() => canManage.value ? [
  { label: "Xóa nhiều DocID", severity: "danger", outlined: true, run: () => openBulkDelete() },
] : []);
</script>

<template>
  <section class="content-page document-tabs-shell">
    <header class="page-header"><div><div class="eyebrow">Document library</div><h1>Kho tài liệu</h1><p class="page-subtitle">Tài liệu được phân loại theo trạng thái hiệu lực.</p></div></header>
    <Message v-if="linkError" severity="error" v-text="linkError"></Message>
    <nav class="view-tabs" aria-label="Trạng thái tài liệu">
      <Button label="Còn hiệu lực" :outlined="activeTab !== 'effective'" @click="selectTab('effective')" />
      <template v-if="canManage">
        <Button label="Đã hết hạn" :outlined="activeTab !== 'expired'" @click="selectTab('expired')" />
        <Button label="Sắp hết hạn" :outlined="activeTab !== 'expiring-soon'" @click="selectTab('expiring-soon')" />
        <Button label="Cần rà soát" :outlined="activeTab !== 'needs-review'" @click="selectTab('needs-review')" />
      </template>
    </nav>
  </section>
  <ResourcePage v-if="activeTab === 'effective'"
    title="Kho tài liệu"
    eyebrow="Documents"
    description="Danh sách tài liệu đã xuất bản theo quyền truy cập của bạn."
    :columns="columns"
    :filters="filters"
    :load="load"
    :row-actions="rowActions"
    :toolbar="toolbar"
  />
  <LifecycleView v-else :key="activeTab" :group="lifecycleGroup" />

  <Dialog v-model:visible="pagePreview.visible" header="Xem trang tài liệu" modal :style="{ width: 'min(1000px, 96vw)' }">
    <template v-if="pagePreview.visible">
      <p>{{ pagePreview.name }} · Trang 1</p>
      <Message v-if="pagePreview.failed" severity="warn">Không tải được ảnh xem trước. Bạn có thể mở bản gốc để đối chiếu.</Message>
      <img v-else :key="pagePreview.pageUrl" class="document-page-preview" :src="pagePreview.pageUrl"
        :alt="`${pagePreview.name}, trang 1`" @error="pagePreview = { ...pagePreview, failed: true }" />
      <p><a class="document-original-link" :href="pagePreview.originalUrl" target="_blank" rel="noopener">Mở bản gốc</a></p>
    </template>
  </Dialog>

  <Dialog v-model:visible="evidenceDialog.visible" header="Kiểm tra chứng cứ tài liệu" modal :style="{ width: 'min(1100px, 96vw)' }">
    <StructuredEvidenceReview v-if="evidenceDialog.visible && canManage && evidenceDialog.docId" :key="evidenceDialog.docId" :doc-id="evidenceDialog.docId" />
  </Dialog>

  <Dialog v-model:visible="editVisible" header="Sửa metadata tài liệu" modal :style="{ width: '560px' }" @after-hide="cancelEdit">
    <Message v-if="editError" severity="error" v-text="editError"></Message>
    <div v-if="editPending" class="stack-form">
      <p class="muted-text">Mã biến thể được lưu riêng trước khi xuất bản.</p>
      <label class="stack-field">
        <span>Mã biến thể mới</span>
        <InputText v-model="variantCode" maxlength="255" :disabled="editBusy" />
      </label>
      <Button type="button" label="Lưu mã biến thể" :loading="editBusy" @click="submitVariantCode" />
    </div>
    <p class="muted-text" style="margin-top: 0">Để trống = giữ nguyên giá trị hiện tại.</p>
    <form class="stack-form" @submit.prevent="submitEdit">
      <label v-for="f in META_FIELDS" :key="f.key" class="stack-field">
        <span v-text="f.label"></span>
        <textarea
          v-if="f.type === 'textarea'"
          v-model="editValues[f.key]"
          rows="3"
          class="native-select"
        ></textarea>
        <input v-else-if="f.type === 'date'" type="date" v-model="editValues[f.key]" class="native-select" />
        <template v-else-if="f.type === 'lang'">
          <input v-model="editValues[f.key]" list="doc-langs" class="native-select" />
          <datalist id="doc-langs">
            <option v-for="l in LANGUAGES" :key="l" :value="l"></option>
          </datalist>
        </template>
        <template v-else-if="f.type === 'status'">
          <input v-model="editValues[f.key]" list="doc-statuses" class="native-select" />
          <datalist id="doc-statuses">
            <option v-for="s in EFFECTIVE_STATUSES" :key="s" :value="s"></option>
          </datalist>
        </template>
        <InputText v-else v-model="editValues[f.key]" />
      </label>
      <div class="form-actions">
        <Button type="button" label="Huỷ" severity="secondary" outlined :disabled="editBusy" @click="editVisible = false" />
        <Button type="submit" :label="editPending ? 'Lưu thông tin khác' : 'Lưu'" :loading="editBusy" />
      </div>
    </form>
  </Dialog>

  <Dialog v-model:visible="bulk.visible" :closable="!bulk.busy" :close-on-escape="!bulk.busy" header="Xóa nhiều tài liệu" modal :style="{ width: '460px' }" @after-hide="cancelBulkDelete">
    <div class="stack-form">
      <Message v-if="bulk.error" severity="error" v-text="bulk.error"></Message>
      <label class="stack-field">
        <span>Danh sách DocID</span>
        <textarea v-model="bulk.ids" rows="3" class="native-select" placeholder="ví dụ: 12, 15, 20"></textarea>
      </label>
      <p class="muted-text">Thao tác xóa vĩnh viễn tài liệu và vector liên quan.</p>
    </div>
    <template #footer>
      <Button label="Huỷ" severity="secondary" outlined :disabled="bulk.busy" @click="bulk.visible = false" />
      <Button label="Xóa" severity="danger" :loading="bulk.busy" @click="submitBulkDelete" />
    </template>
  </Dialog>
</template>

<style scoped>
.document-tabs-shell { padding-bottom: 0; }
.view-tabs { display: flex; flex-wrap: wrap; gap: .65rem; }
.document-page-preview { display: block; max-width: 100%; height: auto; }
</style>
